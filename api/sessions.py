"""Anonymous browser sessions. The cookie is a bearer secret; only its hash is stored."""
import hashlib
import re
import secrets
from contextvars import ContextVar

from fastapi.responses import JSONResponse

COOKIE = "gfp_session"
owner: ContextVar[str] = ContextVar("session_owner", default="")


async def session_middleware(request, call_next):
    # Browser mutations must originate from this service. Non-browser API clients
    # retain cookie-based access without requiring an Origin header.
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Запрос должен исходить из этого сервиса"}, status_code=403)
    raw = request.cookies.get(COOKIE, "")
    fresh = not re.fullmatch(r"[a-f0-9]{64}", raw)
    if fresh:
        raw = secrets.token_hex(32)
    token = owner.set(hashlib.sha256(raw.encode()).hexdigest())
    try:
        response = await call_next(request)
        if fresh:
            response.set_cookie(COOKIE, raw, max_age=30 * 86400, httponly=True,
                                secure=request.url.scheme == "https", samesite="lax")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        owner.reset(token)
