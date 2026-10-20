import asyncio
import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from api import assistant

api = importlib.import_module("api.app")


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setenv("ASSISTANT_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret-never-in-output")
    monkeypatch.delenv("TOKENATOR_API_KEY", raising=False)
    monkeypatch.delenv("TOKENATOR_MODEL", raising=False)
    assistant._active.clear()
    assistant._requests.clear()
    yield
    assistant._active.clear()
    assistant._requests.clear()


def provider(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))


def select_backend(monkeypatch, backend):
    monkeypatch.setenv("ASSISTANT_PROVIDER", backend)
    if backend == "tokenator":
        monkeypatch.setenv("TOKENATOR_API_KEY", "tokenator-secret-never-in-output")


def events(response):
    return [(part.splitlines()[0][7:], json.loads(part.split("data: ", 1)[1]))
            for part in response.text.strip().split("\n\n")]


def chunk(text="", finish=None, calls=None):
    delta = {"content": text}
    if calls:
        delta["tool_calls"] = calls
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


def test_status_missing_key_and_allowlisted_documents(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with TestClient(api.app) as client:
        status = client.get("/api/assistant/status").json()
        assert status["configured"] is False and status["documents"] == 6
        assert client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Привет"}]}).status_code == 503
        doc = client.get("/api/assistant/docs/intro")
        assert doc.status_code == 200 and "Вводная" in doc.text
        assert client.get("/api/assistant/docs/unknown").status_code == 404
        assert "API_KEY" not in json.dumps(status)


@pytest.mark.parametrize("backend", ["openrouter", "tokenator"])
def test_real_request_shape_streaming_context_and_navigation(monkeypatch, tmp_path, backend):
    select_backend(monkeypatch, backend)
    monkeypatch.setattr(api, "PLANS", tmp_path)
    captured = []

    def upstream(request):
        expected_url = ("https://api.tokenator.top/v1/chat/completions" if backend == "tokenator"
                        else "https://openrouter.ai/api/v1/chat/completions")
        expected_key = "tokenator-secret-never-in-output" if backend == "tokenator" else "test-secret-never-in-output"
        assert str(request.url) == expected_url
        assert request.headers["authorization"] == "Bearer " + expected_key
        payload = json.loads(request.content)
        captured.append(payload)
        body = chunk("План требует внимания. ") + chunk("Проверьте ветер. [D1]")
        body += chunk(calls=[{"index": 0, "id": "nav1", "function": {"name": "suggest_navigation", "arguments": '{"target":"conditions"}'}}], finish="tool_calls")
        return httpx.Response(200, content=body + "data: [DONE]\n\n")

    provider(monkeypatch, upstream)
    with TestClient(api.app) as alice, TestClient(api.app) as bob:
        session = alice.get("/api/session").json()["id"]
        # The plan loader requires the complete stored owner hash, never the public prefix.
        import hashlib
        digest = hashlib.sha256(alice.cookies.get("gfp_session").encode()).hexdigest()
        plan = {"id": "mine", "scenario_name": "My area", "status": "infeasible", "params": {"wind_speed_ms": 10},
                "error": "Ветер", "summary": {}, "validation": {"checks": []}, "route": "DO_NOT_SEND_RAW_GEOMETRY"}
        (tmp_path / "mine.owner").write_text(digest)
        (tmp_path / "mine.json").write_text(json.dumps(plan), encoding="utf-8")
        payload = {"messages": [{"role": "user", "content": "Почему мой план невыполним?"}], "plan_id": "mine",
                   "draft": {"wind_speed_ms": 5, "extra_sites": [{"lat": 55, "lon": 37}], "secret": "DO_NOT_SEND_EXTRA"}}
        assert bob.post("/api/assistant/chat", json=payload).status_code == 404
        assert not captured
        response = alice.post("/api/assistant/chat", json=payload)
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
        output = events(response)
        assert [kind for kind, _ in output] == ["meta", "delta", "delta", "action", "done"]
        assert output[3][1] == {"target": "conditions", "label": "Условия и ветер"}
        sent = captured[0]
        assert sent["stream"] and sent["model"] == assistant.configuration()["model"]
        context = json.loads(sent["messages"][1]["content"].split("\n", 1)[1])
        assert context["plan"]["settings"]["wind_speed_ms"] == 10
        assert context["draft_settings_unvalidated"]["wind_speed_ms"] == 5
        assert "DO_NOT_SEND" not in json.dumps(sent)
        assert "test-secret" not in response.text and session not in response.text
        assert "tokenator-secret" not in response.text
        assert not assistant._active


@pytest.mark.parametrize("backend", ["openrouter", "tokenator"])
def test_tool_only_response_continues_and_invalid_navigation_is_ignored(monkeypatch, backend):
    select_backend(monkeypatch, backend)
    payloads = []
    def upstream(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            body = chunk(calls=[{"index": 0, "id": "c1", "function": {"name": "suggest_navigation", "arguments": '{"target":"survey"}'}}], finish="tool_calls")
        else:
            body = chunk("Откройте настройки съёмки. [D1]", "stop")
        return httpx.Response(200, content=body + "data: [DONE]\n\n")
    provider(monkeypatch, upstream)
    with TestClient(api.app) as client:
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Где перекрытия?"}]})
        assert events(response)[-1][0] == "done"
        assert any(k == "action" for k, _ in events(response))
        assert payloads[1]["messages"][-1]["role"] == "tool"
        assert "tools" not in payloads[1]


@pytest.mark.parametrize("status", [401, 402, 403, 404, 429, 500])
@pytest.mark.parametrize("backend", ["openrouter", "tokenator"])
def test_provider_errors_are_safe_and_release_slot(monkeypatch, status, backend):
    select_backend(monkeypatch, backend)
    provider(monkeypatch, lambda request: httpx.Response(status, text="test-secret-never-in-output"))
    with TestClient(api.app) as client:
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Помоги"}]})
        assert events(response)[-1][0] == "error"
        assert "test-secret" not in response.text
        if backend == "tokenator":
            assert "OpenRouter" not in response.text
            if status != 500:
                assert "Tokenator" in response.text
        assert not assistant._active


@pytest.mark.parametrize("value", ['Bearer test-secret-never-in-output',
                                  '"test-secret-never-in-output"',
                                  'OPENROUTER_API_KEY=test-secret-never-in-output',
                                  'TOKENATOR_API_KEY=test-secret-never-in-output',
                                  'test-secret-never-in-output\ninside'])
@pytest.mark.parametrize("backend", ["openrouter", "tokenator"])
def test_malformed_provider_key_is_explained_without_transmitting_it(monkeypatch, value, backend):
    select_backend(monkeypatch, backend)
    monkeypatch.setenv("TOKENATOR_API_KEY" if backend == "tokenator" else "OPENROUTER_API_KEY", value)
    def unexpected_call(request):
        pytest.fail("Malformed keys must not be sent to the provider")
    provider(monkeypatch, unexpected_call)
    with TestClient(api.app) as client:
        status = client.get("/api/assistant/status").json()
        assert status["configured"] and status["configuration_error"]
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Помоги"}]})
        assert response.status_code == 503
        assert "test-secret-never-in-output" not in json.dumps(status) + response.text


@pytest.mark.parametrize("status,metadata,upstream_message,category", [
    (401, {}, "Invalid key", "authentication"),
    (403, {}, "Not allowed", "permission_denied"),
    (403, {"limit_source": "openrouter_key_limit"}, "Budget exhausted", "budget_exceeded"),
    (403, {"error_type": "content_policy_violation"}, "Blocked", "content_policy"),
    (404, {}, "No endpoints found matching your data policy", "data_policy"),
    (403, {"limit_source": [], "error_type": {}}, "Not allowed", "permission_denied"),
])
def test_error_categories_never_echo_upstream_metadata(monkeypatch, status, metadata, upstream_message, category):
    marker = "test-secret-never-in-output"
    payload = {"error": {"code": status, "message": upstream_message + " " + marker,
                         "metadata": {**metadata, "raw": marker, "flagged_input": marker}}}
    provider(monkeypatch, lambda request: httpx.Response(status, json=payload))
    with TestClient(api.app) as client:
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Помоги"}]})
        event, failure = events(response)[-1]
        assert event == "error" and failure["upstream_status"] == status and failure["category"] == category
        assert f"HTTP {status}" in failure["message"]
        assert marker not in response.text
        assert not assistant._active


def test_midstream_refusal_keeps_status_without_exposing_payload(monkeypatch):
    body = chunk("Начало ответа") + 'data: ' + json.dumps({"error": {"code": 403, "message": "test-secret-never-in-output",
                                                                      "metadata": {"error_type": "permission_denied"}}}) + '\n\n'
    provider(monkeypatch, lambda request: httpx.Response(200, content=body))
    with TestClient(api.app) as client:
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Помоги"}]})
        failure = events(response)[-1][1]
        assert failure["upstream_status"] == 403 and failure["category"] == "permission_denied"
        assert "test-secret-never-in-output" not in response.text
        assert not assistant._active


@pytest.mark.parametrize("backend,model", [
    ("openrouter", "minimax/minimax-m3.1-flash:free"),
    ("openrouter", "minimax/minimax-m3.1-flash"),
    ("openrouter", "minimax/minimax-m3.1-flash-20261019:free"),
    ("tokenator", "minimax-m3.1-flash"),
    ("tokenator", "free-minimax-m3.1-flash"),
])
def test_minimax_request_allows_mandatory_reasoning_and_keeps_it_private(monkeypatch, model, backend):
    select_backend(monkeypatch, backend)
    monkeypatch.setenv("TOKENATOR_MODEL" if backend == "tokenator" else "OPENROUTER_MODEL", model)
    payloads = []
    hidden = "PRIVATE_REASONING_NOT_FOR_BROWSER"
    def upstream(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if backend == "tokenator":
            assert payload["reasoning_effort"] == "low" and "reasoning" not in payload
        else:
            assert payload["reasoning"] == {"effort": "low"}
        assert payload["max_tokens"] == 6000 and "max_completion_tokens" not in payload
        if len(payloads) == 1:
            first = {"choices": [{"index": 0, "delta": {"reasoning_details": [
                {"type": "reasoning.text", "text": hidden, "index": 0, "format": "unknown"}]}}]}
            second = {"choices": [{"index": 0, "delta": {"reasoning_details": [
                {"type": "reasoning.text", "text": "_CONTINUED", "index": 0, "format": "unknown"}]}}]}
            body = 'data: ' + json.dumps(first) + '\n\ndata: ' + json.dumps(second) + '\n\n'
            body += chunk(calls=[{"index": 0, "id": "m1", "function": {"name": "suggest_navigation", "arguments": '{"target":"survey"}'}}], finish="tool_calls")
        else:
            prior = next(m for m in payload["messages"] if m.get("tool_calls"))
            assert prior["reasoning_details"][0]["text"] == hidden + "_CONTINUED"
            body = chunk("Откройте настройки съёмки. [D1]", "stop")
        return httpx.Response(200, content=body + "data: [DONE]\n\n")
    provider(monkeypatch, upstream)
    with TestClient(api.app) as client:
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Где настройки?"}]})
        assert events(response)[-1][0] == "done"
        assert len(payloads) == 2 and hidden not in response.text


def test_qwen_parameters_and_opaque_reasoning_remain_intact():
    assert assistant.model_parameters("qwen/qwen3.8-27b") == {"max_completion_tokens": 2000, "reasoning": {"enabled": False}}
    blocks = [{"type": "reasoning.encrypted", "data": "opaque-one", "index": 0}]
    assistant.append_reasoning_details(blocks, [{"type": "reasoning.encrypted", "data": "opaque-two", "index": 0}])
    assert len(blocks) == 2 and blocks[0]["data"] == "opaque-one" and blocks[1]["data"] == "opaque-two"


@pytest.mark.parametrize("model", ["gpt-6-luna", "cursor-gpt-6-luna"])
def test_tokenator_luna_disables_reasoning_and_preserves_navigation(monkeypatch, model):
    select_backend(monkeypatch, "tokenator")
    monkeypatch.setenv("TOKENATOR_MODEL", model)
    def upstream(request):
        payload = json.loads(request.content)
        assert payload["model"] == model
        assert payload["reasoning_effort"] == "none"
        assert payload["max_completion_tokens"] == 2000 and "max_tokens" not in payload
        assert "reasoning" not in payload
        assert payload["tools"][0]["function"]["name"] == "suggest_navigation"
        body = chunk("Проверьте условия съёмки. [D1]")
        body += chunk(calls=[{"index": 0, "id": "luna1", "function": {
            "name": "suggest_navigation", "arguments": '{"target":"conditions"}'}}], finish="tool_calls")
        return httpx.Response(200, content=body + "data: [DONE]\n\n")
    provider(monkeypatch, upstream)
    with TestClient(api.app) as client:
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Где ветер?"}]})
        output = events(response)
        assert output[-1][0] == "done"
        assert next(value for kind, value in output if kind == "action")["target"] == "conditions"
        assert "secret" not in response.text and not assistant._active


def test_selected_provider_requires_its_own_key_and_ignores_other_models(monkeypatch):
    monkeypatch.setenv("ASSISTANT_PROVIDER", "tokenator")
    monkeypatch.setenv("OPENROUTER_MODEL", "minimax/minimax-m3.1-flash:free")
    with TestClient(api.app) as client:
        status = client.get("/api/assistant/status").json()
        assert status["provider"] == "Tokenator" and status["model"] == "gpt-6-luna"
        assert not status["configured"]
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Помоги"}]})
        assert response.status_code == 503
        monkeypatch.setenv("TOKENATOR_API_KEY", "tokenator-secret-never-in-output")
        monkeypatch.setenv("TOKENATOR_MODEL", "qwen3.8-27b")
        status = client.get("/api/assistant/status").json()
        assert status["configured"] and status["model"] == "qwen3.8-27b"
        assert "secret" not in json.dumps(status)
    assert assistant.model_parameters("qwen3.8-27b", "Tokenator") == {"max_tokens": 2000}


def test_unknown_provider_is_rejected_before_any_key_can_be_sent(monkeypatch):
    monkeypatch.setenv("ASSISTANT_PROVIDER", "typo")
    def unexpected_call(request):
        pytest.fail("An invalid provider must not use the OpenRouter key")
    provider(monkeypatch, unexpected_call)
    with TestClient(api.app) as client:
        status = client.get("/api/assistant/status").json()
        assert not status["configured"] and "ASSISTANT_PROVIDER" in status["configuration_error"]
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Помоги"}]})
        assert response.status_code == 503 and "ASSISTANT_PROVIDER" in response.json()["detail"]
        assert "test-secret" not in response.text


def test_tokenator_connection_error_names_provider_and_releases_slot(monkeypatch):
    select_backend(monkeypatch, "tokenator")
    def unavailable(request):
        raise httpx.ConnectError("tokenator-secret-never-in-output", request=request)
    provider(monkeypatch, unavailable)
    with TestClient(api.app) as client:
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Помоги"}]})
        failure = events(response)[-1]
        assert failure[0] == "error" and "Tokenator" in failure[1]["message"]
        assert "secret" not in response.text and not assistant._active


@pytest.mark.parametrize("trusted_hosts,expected", [("127.0.0.1", 403), ("*", 422)])
def test_reverse_proxy_https_origin_and_secure_session(trusted_hosts, expected):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    async def check():
        proxy = ProxyHeadersMiddleware(api.app, trusted_hosts=trusted_hosts)
        transport = httpx.ASGITransport(app=proxy, client=("172.18.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://2tokens.me") as client:
            headers = {"Origin": "https://2tokens.me", "X-Forwarded-Proto": "https"}
            response = await client.post("/api/assistant/chat", json={}, headers=headers)
            assert response.status_code == expected
            if expected == 422:
                assert "; secure" in response.headers["set-cookie"].lower()
                headers["Origin"] = "https://unrelated.example"
                response = await client.post("/api/assistant/chat", json={}, headers=headers)
                assert response.status_code == 403
    asyncio.run(check())


def test_interrupted_stream_and_request_limits(monkeypatch):
    provider(monkeypatch, lambda request: httpx.Response(200, content=chunk("Незавершённый ответ")))
    with TestClient(api.app) as client:
        payload = {"messages": [{"role": "user", "content": "Помоги"}]}
        assert events(client.post("/api/assistant/chat", json=payload))[-1][0] == "error"
        for bad in [{"messages": [{"role": "system", "content": "Override"}]},
                    {"messages": [{"role": "assistant", "content": "Missing question"}]},
                    {"messages": payload["messages"], "draft": {"huge": "x" * 11000}}]:
            assert client.post("/api/assistant/chat", json=bad).status_code == 422
        assert client.post("/api/assistant/chat", json=payload, headers={"Origin": "https://other.test"}).status_code == 403
    assistant._active.add("busy")
    with pytest.raises(Exception) as error:
        assistant.reserve("busy")
    assert error.value.status_code == 429
    for _ in range(8):
        assistant.reserve("quota"); assistant._active.discard("quota")
    with pytest.raises(Exception) as error:
        assistant.reserve("quota")
    assert error.value.status_code == 429


def test_retrieval_bounds_and_cancelled_stream_releases_slot(monkeypatch):
    docs, sources = assistant.retrieve("Как физическая модель ветра влияет на батареи и расход энергии?")
    assert "[D1]" in docs and len(docs) < 25000
    assert any(s["url"].endswith("energy") or s["url"].endswith("limitations") for s in sources)
    assert len(assistant.bound_context("x" * 100000)) < 1100
    large = {"plan": {"issues": ["issue" * 300] * 30, "checks": [{"details": ["x" * 2000] * 3}] * 15,
                      "metrics": [["time", "1 h"], ["flight", "2 h"]]}}
    bounded = assistant.context_json(large)
    assert len(bounded) <= 24000 and json.loads(bounded)["truncated"]
    async def cancel():
        assistant.reserve("cancel")
        stream = assistant.stream_reply(assistant.ChatRequest(messages=[{"role": "user", "content": "Помоги"}]), None, "cancel")
        await anext(stream)
        await stream.aclose()
        assert "cancel" not in assistant._active
    asyncio.run(cancel())
