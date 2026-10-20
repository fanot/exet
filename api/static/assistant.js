/* Streaming mission assistant. No provider credentials or generated HTML are trusted. */
"use strict";
(() => {
  const panel = $("#assistant"), feed = $("#assistant-messages"), input = $("#assistant-input");
  const navigation = {
    fleet: ["task", "#fleet"], survey: ["task", "#survey_type"],
    conditions: ["task", "#compass"], optimization: ["task", "#deadline"],
    overview: ["overview"], result: ["result"], history: ["history"],
    geometry: ["result", ".flight-geometry"], exports: ["result", ".mission-exports"]
  };
  let history = [], controller = null, requestId = 0, busy = false, returnFocus = null;
  let lastContext = null, config = null;

  function scrollToEnd(force = false) {
    const near = feed.scrollHeight - feed.clientHeight - feed.scrollTop < 100;
    if (force || near) feed.scrollTop = feed.scrollHeight;
    $("#assistant-jump").hidden = force || near;
  }
  feed.addEventListener("scroll", () => {
    $("#assistant-jump").hidden = feed.scrollHeight - feed.clientHeight - feed.scrollTop < 100;
  });
  $("#assistant-jump").onclick = () => scrollToEnd(true);

  function bubble(role, text = "") {
    const block = document.createElement("article");
    block.className = `assistant-message from-${role}`;
    block.innerHTML = `<div class="message-author">${role === "user" ? "Вы" : "Помощник"}</div><div class="message-text"></div>`;
    $(".message-text", block).textContent = text;
    feed.append(block); scrollToEnd(true);
    return block;
  }
  function greeting() {
    feed.replaceChildren();
    const block = bubble("assistant", "Помогу подготовить миссию и разобраться в результате. Вижу текущие настройки и выбранный план, а объяснения сверяю с документацией сервиса.");
    block.classList.add("assistant-welcome");
    const paths = document.createElement("div"); paths.className = "assistant-paths";
    paths.innerHTML = `<button data-navigate="fleet">${ico("plane")}Настроить парк ${ico("chevR")}</button><button data-navigate="survey">${ico("sliders")}Параметры съёмки ${ico("chevR")}</button><button data-navigate="overview">${ico("file")}Обзор миссии ${ico("chevR")}</button>`;
    block.append(paths);
  }
  function updateContext() {
    const name = S.plan?.scenario_name || S.scenario?.name || S.scenario?.properties?.name || $("#scenario option:checked")?.textContent || "Новая миссия";
    $("#assistant-context").innerHTML = `${ico(S.plan ? "file" : "sliders")}<div><strong>${esc(S.plan ? "Контекст плана" : "Текущие настройки")}</strong><span>${esc(name)}${S.plan ? ` · ${esc(S.plan.id)}` : " · без выбранного плана"}</span></div>`;
    const key = S.plan?.id || "draft";
    if (lastContext !== null && key !== lastContext && history.length && !busy) {
      const notice = document.createElement("div"); notice.className = "chat-context-change";
      notice.textContent = S.plan ? "Выбран другой план. Следующий ответ учтёт его данные." : "Настройки изменены. Следующий ответ учтёт текущие параметры; чтобы обсудить результат, пересчитайте план.";
      feed.append(notice); scrollToEnd();
    }
    lastContext = key;
  }
  async function refreshStatus() {
    try {
      config = await api("/api/assistant/status");
      $("#assistant-provider").textContent = config.provider || "сервис модели";
      const ready = config.configured && !config.configuration_error;
      $("#assistant-model").textContent = `${config.model.split("/").pop()} · ${ready ? "ключ задан" : config.configuration_error ? "проверьте ключ API" : "нужен ключ API"}`;
      $("#assistant-model").classList.toggle("connected", ready);
      if (!busy) $("#assistant-status").textContent = config.configuration_error || (config.configured ? "" : "Для ответов добавьте API-ключ выбранного сервиса в .env на сервере.");
    } catch (_) {
      $("#assistant-model").textContent = "Нет соединения с сервером";
      $("#assistant-model").classList.remove("connected");
    }
  }
  function open() {
    if (panel.hidden) returnFocus = document.activeElement;
    panel.hidden = false; $("#assistant-open").setAttribute("aria-expanded", "true");
    updateContext(); refreshStatus(); input.focus();
  }
  function close() {
    panel.hidden = true; $("#assistant-open").setAttribute("aria-expanded", "false");
    if (returnFocus?.isConnected) returnFocus.focus();
  }
  function setBusy(value) {
    busy = value;
    $("#assistant-send").hidden = value; $("#assistant-stop").hidden = !value;
    $("#assistant-new").disabled = value;
    $$(".assistant-suggestions button").forEach(b => { b.disabled = value; });
    panel.classList.toggle("is-answering", value);
  }

  function inline(text, sources) {
    return esc(text).replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\[(D\d+)\]/g, (all, id) => {
        const source = sources.find(s => s.id === id);
        return source ? `<a class="citation" href="${source.url}" target="_blank" rel="noopener" title="${esc(source.title)}">${id}</a>` : all;
      });
  }
  function cleanAssistantText(text) {
    return text.replace(/<\s*\/?\s*(?:box|row|AsyncImage)\b[^>]*(?:>|$)/gi, tag => /<\s*\/?\s*AsyncImage\b/i.test(tag) ? "" : "\n")
      .replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  }
  function tableCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map(cell => cell.replace(/\\\|/g, "|").trim());
  }
  function markdown(text, sources) {
    // A small formatting subset keeps model output inert (no raw HTML or arbitrary URLs).
    const lines = text.trim().split("\n"), blocks = [];
    const isTable = i => i + 1 < lines.length && tableCells(lines[i]).length > 1 &&
      tableCells(lines[i]).length === tableCells(lines[i + 1]).length &&
      tableCells(lines[i + 1]).every(cell => /^:?-{3,}:?$/.test(cell));
    const listKind = line => /^\s*[-•*]\s+/.test(line) ? "ul" : /^\s*\d+[.)]\s+/.test(line) ? "ol" : null;
    for (let i = 0; i < lines.length;) {
      if (!lines[i].trim()) { i++; continue; }
      if (isTable(i)) {
        const headers = tableCells(lines[i]), rows = [];
        i += 2;
        while (i < lines.length && lines[i].includes("|") && tableCells(lines[i]).length === headers.length) rows.push(tableCells(lines[i++]));
        blocks.push(`<div class="message-table-wrap"><table><thead><tr>${headers.map(cell => `<th scope="col">${inline(cell, sources)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${inline(cell, sources)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      const kind = listKind(lines[i]);
      if (kind) {
        const items = [];
        while (i < lines.length && listKind(lines[i]) === kind) items.push(lines[i++].replace(kind === "ul" ? /^\s*[-•*]\s+/ : /^\s*\d+[.)]\s+/, ""));
        blocks.push(`<${kind}>${items.map(item => `<li>${inline(item, sources)}</li>`).join("")}</${kind}>`);
        continue;
      }
      const paragraph = [];
      while (i < lines.length && lines[i].trim() && !isTable(i) && !listKind(lines[i])) paragraph.push(lines[i++]);
      blocks.push(`<p>${paragraph.map(line => inline(line.replace(/^#{1,6}\s+/, ""), sources)).join("<br>")}</p>`);
    }
    return blocks.join("");
  }
  function validSources(sources) {
    return (Array.isArray(sources) ? sources : []).filter(s => /^D\d+$/.test(s.id) && /^\/api\/assistant\/docs\/[a-z-]+$/.test(s.url) && typeof s.title === "string");
  }
  function attachSources(block, sources, text) {
    const used = sources.filter(s => text.includes(`[${s.id}]`));
    if (!used.length) return;
    const details = document.createElement("details"); details.className = "message-sources";
    details.innerHTML = `<summary>${ico("file")}Источники · ${used.length}${ico("chev")}</summary><div>${used.map(s => `<a href="${s.url}" target="_blank" rel="noopener"><span>${s.id}</span>${esc(s.title)} ${ico("chevR")}</a>`).join("")}</div>`;
    block.append(details);
  }
  function addNavigation(block, data) {
    if (!navigation[data.target] || $(`[data-navigate="${data.target}"]`, block)) return;
    let actions = $(".message-actions", block);
    if (!actions) { actions = document.createElement("div"); actions.className = "message-actions"; block.append(actions); }
    const button = document.createElement("button"); button.className = "btn btn-sm"; button.dataset.navigate = data.target;
    button.textContent = data.label; button.insertAdjacentHTML("beforeend", ico("chevR")); actions.append(button);
  }

  async function ask(text, retry = false) {
    text = text.trim();
    if (!text || busy) return;
    if (text.length > 4000) { toast("Сократите вопрос до 4000 символов"); return; }
    open();
    if (!retry) bubble("user", text);
    const block = bubble("assistant"); block.classList.add("pending"); block.setAttribute("aria-busy", "true");
    const output = $(".message-text", block);
    output.innerHTML = '<span class="typing-dots" aria-label="Готовлю ответ"><i></i><i></i><i></i></span>';
    const id = ++requestId; controller = new AbortController(); setBusy(true);
    $("#assistant-status").textContent = "Изучаю вопрос и контекст миссии…";
    let fullText = "", sources = [], completed = false, notice = "";
    const timeout = setTimeout(() => controller?.abort("timeout"), 110000);
    try {
      let draft = {}; try { draft = params(); } catch (_) { /* The user may be asking about incomplete settings. */ }
      const messages = [...history, {role: "user", content: text}];
      while (messages.length > 19 || messages.reduce((n, m) => n + m.content.length, 0) > 23000) messages.splice(0, 2);
      const response = await fetch("/api/assistant/chat", {method: "POST", headers: {"Content-Type": "application/json"}, signal: controller.signal,
        body: JSON.stringify({messages, plan_id: S.plan?.id || null, draft,
          scenario_name: S.plan?.scenario_name || $("#scenario option:checked")?.textContent || "",
          selected_sortie: S.sel, active_tab: $(".tabs [aria-selected=true]")?.dataset.tab || "task"})});
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(typeof error.detail === "string" ? error.detail : `Не удалось отправить вопрос (${response.status}).`);
      }
      if (!response.body) throw new Error("Браузер не поддерживает потоковый ответ.");
      const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
      function event(part) {
        const type = /^event:\s*(\w+)/m.exec(part)?.[1];
        const raw = part.split("\n").filter(l => l.startsWith("data:")).map(l => l.slice(5).trimStart()).join("\n");
        if (!raw) return;
        const data = JSON.parse(raw);
        if (type === "error") throw new Error(data.message || "Ответ модели прервался.");
        if (type === "meta") {
          sources = validSources(data.sources);
          const stamp = document.createElement("span"); stamp.className = "message-context-stamp";
          stamp.textContent = data.plan_id ? `План ${data.plan_id}` : "Настройки на момент вопроса · план не выбран";
          if (!data.plan_id) stamp.title = "Помощник видит параметры формы, но не результаты расчёта.";
          $(".message-author", block).append(stamp);
        }
        if (type === "delta") {
          const stick = feed.scrollHeight - feed.clientHeight - feed.scrollTop < 110;
          fullText += data.text; output.textContent = cleanAssistantText(fullText); block.classList.remove("pending");
          $("#assistant-status").textContent = "Помощник отвечает…";
          if (stick) scrollToEnd(true); else $("#assistant-jump").hidden = false;
        }
        if (type === "action") addNavigation(block, data);
        if (type === "notice") { notice = data.message; $("#assistant-status").textContent = notice; }
        if (type === "done") completed = true;
      }
      while (true) {
        const {value, done} = await reader.read();
        if (id !== requestId) { await reader.cancel(); return; }
        buffer = (buffer + decoder.decode(value || new Uint8Array(), {stream: !done})).replace(/\r\n/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) { const part = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2); event(part); }
        if (buffer.length > 64000) throw new Error("Получен слишком большой ответ.");
        if (done) break;
      }
      if (!completed || !fullText.trim()) throw new Error("Ответ прервался до завершения. Попробуйте ещё раз.");
      const answer = cleanAssistantText(fullText);
      output.innerHTML = markdown(answer, sources); attachSources(block, sources, answer);
      history.push({role: "user", content: text}, {role: "assistant", content: answer.slice(0, 8000)});
      history = history.slice(-18);
      $("#assistant-status").textContent = notice || "Ответ основан на переданном контексте. Проверьте важные выводы.";
    } catch (e) {
      if (id !== requestId) return;
      const stopped = controller?.signal.reason === "stop", timedOut = controller?.signal.reason === "timeout";
      controller?.abort();
      if (fullText) output.innerHTML = markdown(cleanAssistantText(fullText), sources); else output.textContent = "";
      const error = document.createElement("div"); error.className = "message-error";
      error.textContent = stopped ? "Ответ остановлен." : timedOut ? "Время ожидания ответа истекло. Попробуйте ещё раз." : e.message || "Не удалось получить ответ.";
      block.append(error);
      const retryButton = document.createElement("button"); retryButton.type = "button"; retryButton.className = "btn btn-sm";
      retryButton.textContent = "Повторить вопрос"; retryButton.onclick = () => { if (!busy) { block.remove(); ask(text, true); } };
      error.append(retryButton);
      $("#assistant-status").textContent = "Можно повторить вопрос или начать новый диалог.";
    } finally {
      clearTimeout(timeout);
      if (id === requestId) { block.classList.remove("pending"); block.removeAttribute("aria-busy"); setBusy(false); controller = null; scrollToEnd(); }
    }
  }

  function navigate(target) {
    const entry = navigation[target === "guide" ? "fleet" : target];
    if (!entry) return;
    if (["overview", "result"].includes(entry[0]) && !S.plan) { toast("Сначала рассчитайте или выберите план из истории"); return; }
    switchTab(entry[0]);
    const element = entry[1] ? $(entry[1]) : $(`#tab-${entry[0]}`);
    if (!element) { toast("Этот раздел недоступен для текущего плана"); return; }
    const step = element.closest("details.step"); if (step) step.open = true;
    if (element.matches("details")) element.open = true;
    const highlight = step || element;
    highlight.scrollIntoView({block: "center", behavior: "smooth"});
    highlight.classList.remove("guided-highlight");
    requestAnimationFrame(() => highlight.classList.add("guided-highlight"));
    setTimeout(() => highlight.classList.remove("guided-highlight"), 2400);
    const focus = step?.querySelector("summary") || element.querySelector("summary,button,a") || element;
    if (innerWidth < 860) close();
    if (focus.matches("summary,button,a,input")) focus.focus({preventScroll: true});
  }
  document.addEventListener("click", e => {
    const prompt = e.target.closest("[data-ask]"); if (prompt) ask(prompt.dataset.ask);
    const link = e.target.closest("[data-navigate]"); if (link) navigate(link.dataset.navigate);
  });
  $("#assistant-open").onclick = () => panel.hidden ? open() : close();
  $("#assistant-close").onclick = close;
  $("#assistant-new").onclick = () => { history = []; requestId++; controller?.abort(); setBusy(false); greeting(); updateContext(); refreshStatus(); input.focus(); };
  $("#guide-start").onclick = () => ask("С чего начать? Проведи меня по шагам настройки первой миссии.");
  $("#assistant-stop").onclick = () => controller?.abort("stop");
  $("#assistant-form").onsubmit = e => { e.preventDefault(); const text = input.value.trim(); if (text && !busy) { input.value = ""; ask(text); } };
  input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!busy) $("#assistant-form").requestSubmit(); } });
  window.missionAssistant = {updateContext, close};
  greeting(); updateContext();
})();
