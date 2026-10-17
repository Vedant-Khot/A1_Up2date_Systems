"""SiteLog demo API and office dashboard."""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.mail import poll_loop, send_login_email, probe_mailbox
from app.routing import plan_route
from app.store import BRAND, PHOTO_DIR, ROLE_LABELS, STATUS_LABELS, store

SECRET = b"sitelog-demo-secret"
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_PHOTO_BYTES = 10 * 1024 * 1024
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _format_when(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return moment.strftime("%d %b %Y, %H:%M")


def _parse_date(raw: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_time(raw: str) -> Optional[datetime]:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _display_date(value: object) -> str:
    parsed = _parse_date(str(value or "").strip())
    if parsed is None:
        return ""
    weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[parsed.weekday()]
    month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[parsed.month - 1]
    return f"{weekday} {parsed.day} {month} {parsed.year}"


def _date_value(value: object) -> str:
    parsed = _parse_date(str(value or "").strip())
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def _time_value(value: object) -> str:
    parsed = _parse_time(str(value or "").strip())
    return parsed.strftime("%H:%M") if parsed else ""


def _store_date(value: object) -> str:
    raw = str(value or "").strip()
    parsed = _parse_date(raw)
    return parsed.strftime("%d/%m/%Y") if parsed else raw


def _store_time(value: object) -> str:
    raw = str(value or "").strip()
    parsed = _parse_time(raw)
    return parsed.strftime("%H:%M") if parsed else raw


def _normalize_schedule(data: dict[str, Any]) -> dict[str, Any]:
    if "job_date" in data:
        data["job_date"] = _store_date(data.get("job_date"))
    if "arrival_time" in data:
        data["arrival_time"] = _store_time(data.get("arrival_time"))
    if "departure_time" in data:
        data["departure_time"] = _store_time(data.get("departure_time"))
    return data


_request_scope: ContextVar[dict] = ContextVar("sitelog_request_scope")


class RequestScopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = _request_scope.set(request.scope)
        try:
            return await call_next(request)
        finally:
            _request_scope.reset(token)


def _current_request() -> Request:
    return Request(_request_scope.get())


def ensure_csrf(request: Request) -> str:
    token = str(request.session.get("csrf_token") or "")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def csrf_field() -> Markup:
    token = escape(ensure_csrf(_current_request()))
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')


def flash(request: Request, text: str, type: str = "status") -> None:
    request.session["flash"] = {"text": text, "type": type}


def consume_flash() -> Optional[dict[str, str]]:
    message = _current_request().session.pop("flash", None)
    if not isinstance(message, dict) or not message.get("text"):
        return None
    return {"text": str(message.get("text") or ""), "type": str(message.get("type") or "status")}


def assert_csrf(request: Request, token: str) -> None:
    if os.environ.get("SITELOG_TEST_MODE") == "1":
        return
    expected = str(request.session.get("csrf_token") or "")
    supplied = str(token or "")
    if not expected or len(expected) != len(supplied) or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


def _format_hours_worked(arrival: object, departure: object = "") -> str:
    arr = _parse_time(str(arrival or "").strip())
    dep = _parse_time(str(departure or "").strip())
    if not arr or not dep:
        return ""
    start_min = arr.hour * 60 + arr.minute
    end_min = dep.hour * 60 + dep.minute
    if end_min < start_min:
        end_min += 24 * 60
    diff = end_min - start_min
    h = diff // 60
    m = diff % 60
    if diff == 0:
        return "0 mins"
    if h == 0:
        return f"{m}m"
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


TEMPLATES.env.filters["when"] = _format_when
TEMPLATES.env.filters["display_date"] = _display_date
TEMPLATES.env.filters["date_value"] = _date_value
TEMPLATES.env.filters["time_value"] = _time_value
TEMPLATES.env.filters["hours_worked"] = _format_hours_worked
TEMPLATES.env.globals["today_iso"] = lambda: datetime.now().strftime("%Y-%m-%d")
TEMPLATES.env.globals["csrf_field"] = csrf_field
TEMPLATES.env.globals["consume_flash"] = consume_flash
TEMPLATES.env.globals["labels"] = {"status": STATUS_LABELS, "role": ROLE_LABELS, "brand": BRAND}
SECTIONS = [(key, f"{STATUS_LABELS[key]} jobs") for key in ("new", "in_progress", "discarded", "completed")]
JOB_FILTERS = [("all", "All")] + [(key, STATUS_LABELS[key]) for key in ("new", "in_progress", "discarded", "completed")]
JOB_SORTS = ("newest", "oldest", "visit")
PAGE_SIZE = 25
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, list[float]] = {}
_login_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.initialize()
    task = asyncio.create_task(poll_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="SiteLog Demo", lifespan=lifespan)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
store.initialize()
app.add_middleware(RequestScopeMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET.decode(),
    session_cookie="sitelog_form",
    same_site="lax",
    https_only=False,
    max_age=TOKEN_TTL_SECONDS,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def make_token(user_id: str) -> str:
    expires = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{user_id}.{expires}"
    signature = hmac.new(SECRET, payload.encode(), "sha256").hexdigest()
    return f"{payload}.{signature}"


def read_token(token: str) -> Optional[dict[str, Any]]:
    parts = (token or "").split(".")
    if len(parts) != 3:
        return None
    payload = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(SECRET, payload.encode(), "sha256").hexdigest()
    if not hmac.compare_digest(expected, parts[2]):
        return None
    try:
        expires = int(parts[1])
    except ValueError:
        return None
    if expires < int(time.time()):
        return None
    return store.user_by_id(parts[0])


def user_from_request(request: Request) -> Optional[dict[str, Any]]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return read_token(header[7:].strip())
    return read_token(request.cookies.get("sitelog_session", ""))


def require_user(request: Request) -> dict[str, Any]:
    user = user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _job_or_404(job_id: str, user: dict[str, Any]) -> dict[str, Any]:
    job = store.get_job(job_id)
    if job is None or not store.can_access(user, job):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _page_user(request: Request) -> Optional[dict[str, Any]]:
    return user_from_request(request)


def _checked(values: list[str]) -> list[str]:
    return [value for value in values if value]


def _assignees_for(user: dict[str, Any], values: list[str]) -> list[str]:
    chosen = _checked(values)
    if user["role"] == "engineer" and user["id"] not in chosen:
        chosen.append(user["id"])
    return chosen


def _office_user(user: dict[str, Any]) -> dict[str, Any]:
    if user["role"] != "office":
        raise HTTPException(status_code=403, detail="Office login required")
    return user


def _show_demo_logins() -> bool:
    return os.environ.get("SITELOG_SHOW_DEMO_LOGINS", "1").strip().lower() not in {"0", "false", "no", "off"}


def safe_next(value: str, fallback: str, prefixes: Optional[tuple[str, ...]] = None) -> str:
    raw = str(value or "").strip()
    if not raw or any(ord(char) < 32 or char in "\\" for char in raw):
        return fallback
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return fallback
    path = unquote(parsed.path or "")
    if not path.startswith("/") or path.startswith("//") or "\\" in path or ".." in path.split("/"):
        return fallback
    if path in {"/", "/login"}:
        return fallback
    if prefixes is not None and not any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes):
        return fallback
    query = f"?{parsed.query}" if parsed.query else ""
    return path + query


def _need_login(request: Request) -> RedirectResponse:
    current = request.url.path
    if request.url.query:
        current = f"{current}?{request.url.query}"
    target = safe_next(current, "")
    if not target:
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/?next={quote(target, safe='')}", status_code=303)


def _login_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else ""
    return f"{host}|{email.strip().lower()}"


def _login_blocked(request: Request, email: str) -> bool:
    now = time.time()
    with _login_lock:
        attempts = [stamp for stamp in _login_failures.get(_login_key(request, email), []) if now - stamp < LOGIN_WINDOW_SECONDS]
        _login_failures[_login_key(request, email)] = attempts
        return len(attempts) >= LOGIN_MAX_FAILURES


def _login_failed(request: Request, email: str) -> None:
    now = time.time()
    key = _login_key(request, email)
    with _login_lock:
        attempts = [stamp for stamp in _login_failures.get(key, []) if now - stamp < LOGIN_WINDOW_SECONDS]
        attempts.append(now)
        _login_failures[key] = attempts


def _login_succeeded(request: Request, email: str) -> None:
    with _login_lock:
        _login_failures.pop(_login_key(request, email), None)


def _jobs_href(status: str, query: str, sort: str, page: int = 1) -> str:
    params = [("status", status), ("sort", sort)]
    if query:
        params.append(("q", query))
    if page > 1:
        params.append(("page", str(page)))
    return "/jobs?" + urlencode(params)


def _page_number(raw: Optional[str]) -> int:
    try:
        return max(1, int(raw or "1"))
    except (TypeError, ValueError):
        return 1


def _job_matches(job: dict[str, Any], needle: str) -> bool:
    if not needle:
        return True
    folded = needle.casefold()
    return any(folded in str(job.get(field) or "").casefold() for field in ("work_ref", "client", "site_address"))


def _sorted_jobs(jobs: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "oldest":
        return sorted(jobs, key=lambda job: str(job.get("created_at") or ""))
    if sort == "visit":
        def visit_key(job: dict[str, Any]) -> tuple:
            parsed = _parse_date(str(job.get("job_date") or ""))
            if parsed is None:
                return (1, 0, str(job.get("created_at") or ""))
            return (0, -parsed.toordinal(), str(job.get("created_at") or ""))
        return sorted(jobs, key=visit_key)
    return sorted(jobs, key=lambda job: str(job.get("created_at") or ""), reverse=True)


def _filter_jobs(user: dict[str, Any], status: str, query: str, sort: str, page: int) -> dict[str, Any]:
    jobs = [job for job in store.list_jobs(user) if _job_matches(job, query)]
    counts = {key: sum(1 for job in jobs if job["status"] == key) for key in STATUS_LABELS}
    counts["all"] = len(jobs)
    chosen = jobs if status == "all" else [job for job in jobs if job["status"] == status]
    chosen = _sorted_jobs(chosen, sort)
    total = len(chosen)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
    page = min(max(page, 1), pages)
    start = (page - 1) * PAGE_SIZE
    return {
        "jobs": chosen[start:start + PAGE_SIZE],
        "counts": counts,
        "total": total,
        "page": page,
        "pages": pages,
    }


def _back_label(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path == "/dashboard":
        return "Back to the dashboard"
    status = (parse_qs(parsed.query).get("status") or ["all"])[0]
    if status == "all":
        return "Back to all jobs"
    if status in STATUS_LABELS:
        return f"Back to {STATUS_LABELS[status]} jobs"
    return "Back to jobs"


def _clean_port(port: int) -> int:
    if port < 1 or port > 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return port


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/login")
def api_login(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    email = str(payload.get("email") or "")
    if _login_blocked(request, email):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again in 15 minutes.")
    user = store.authenticate(email, str(payload.get("password") or ""))
    if user is None:
        _login_failed(request, email)
        raise HTTPException(status_code=401, detail="Email or password is wrong")
    _login_succeeded(request, email)
    return {"token": make_token(user["id"]), "user": user}


@app.get("/api/engineers")
def api_engineers(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return {"engineers": store.list_engineers()}


@app.get("/api/outbox")
def api_outbox(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    _office_user(user)
    from app.mail import OUTBOX
    return {"outbox": OUTBOX}


@app.get("/api/jobs")
def api_jobs(status: str = "", user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    chosen = status if status in STATUS_LABELS else None
    return {"jobs": store.list_jobs(user, chosen), "counts": store.counts(user)}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return _job_or_404(job_id, user)


@app.post("/api/jobs")
def api_create_job(payload: dict[str, Any], user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    work_ref = str(payload.get("work_ref") or "").strip()
    address = str(payload.get("site_address") or "").strip()
    if not work_ref or not address:
        raise HTTPException(status_code=400, detail="Work ref and site address are required")
    assignee_ids = _assignees_for(user, [str(item) for item in payload.get("engineer_ids") or []])
    existing = store.job_by_work_ref(work_ref)
    if existing is not None and not store.can_access(user, existing):
        raise HTTPException(status_code=404, detail="Job not found")
    data = _progress_if_assigned(_normalize_schedule(payload), assignee_ids)
    return store.save_job(data, assignee_ids)


@app.post("/api/jobs/{job_id}")
def api_update_job(job_id: str, payload: dict[str, Any], user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    job = _job_or_404(job_id, user)
    assignee_ids = payload.get("engineer_ids")
    if assignee_ids is not None:
        assignee_ids = _assignees_for(user, [str(item) for item in assignee_ids])
    effective = assignee_ids if assignee_ids is not None else [person["id"] for person in job.get("assignees") or []]
    data = _progress_if_assigned(_normalize_schedule(payload), effective, current_status=job["status"])
    updated = store.update_job(job_id, data, assignee_ids)
    if updated is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return updated


@app.post("/api/jobs/{job_id}/discard")
def api_discard(job_id: str, payload: dict[str, Any], user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    _job_or_404(job_id, user)
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A discard reason is required")
    return store.discard_job(job_id, reason) or {}


@app.post("/api/jobs/{job_id}/restore")
def api_restore(job_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    _job_or_404(job_id, user)
    return store.restore_job(job_id) or {}


@app.get("/api/jobs/{job_id}/messages")
def api_messages(
    job_id: str,
    limit: Optional[int] = 10,
    before: str = "",
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    job = _job_or_404(job_id, user)
    total = store.count_messages(job_id)
    messages = store.list_messages(job_id, limit=limit, before_created_at=before or None)
    for message in messages:
        message["when"] = _format_when(message.get("created_at"))
    has_more = False
    if messages and limit and len(messages) == limit:
        oldest_time = messages[0]["created_at"]
        has_more = store.has_messages_before(job_id, oldest_time)
    return {
        "job": {
            "id": job["id"],
            "work_ref": job["work_ref"],
            "site_address": job["site_address"],
            "engineers": job.get("engineers", ""),
            "status": job["status"],
            "status_label": STATUS_LABELS.get(job["status"], job["status"].title()),
        },
        "messages": messages,
        "total": total,
        "has_more": has_more,
    }


@app.post("/api/jobs/{job_id}/messages")
def api_add_message(job_id: str, payload: dict[str, Any], user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    _job_or_404(job_id, user)
    body = str(payload.get("body") or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Message text is required")
    message = store.add_message(job_id, user["id"], body)
    message["when"] = _format_when(message.get("created_at"))
    return message


@app.get("/api/chats")
def api_chats(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    threads = store.chat_threads(user)
    for thread in threads:
        thread["when"] = _format_when(thread.get("last_at"))
    return {"chats": threads}


@app.get("/api/mailbox")
def api_mailbox(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    _office_user(user)
    return store.get_mailbox() or {"configured": False, "host": "", "port": 993, "email": ""}


@app.put("/api/mailbox")
def api_save_mailbox(payload: dict[str, Any], user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    _office_user(user)
    host = str(payload.get("host") or "").strip()
    email = str(payload.get("email") or "").strip()
    if not host or not email:
        raise HTTPException(status_code=400, detail="Host and email are required")
    try:
        return store.save_mailbox(host, int(payload.get("port") or 993), email, str(payload.get("password") or ""))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _image_kind(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return ""


async def _validated_photo(upload: UploadFile) -> tuple[Optional[bytes], str, str]:
    name = Path(upload.filename or "").name
    if not name:
        return None, "", ""
    content = await upload.read()
    if len(content) > MAX_PHOTO_BYTES:
        return None, f"{name} is larger than 10 MB.", ""
    if not content:
        return None, f"{name} is empty.", ""
    kind = _image_kind(content)
    if kind not in ALLOWED_EXTENSIONS and kind != ".jpg":
        return None, f"{name} must be a JPG, PNG, or WebP image.", ""
    if not kind:
        return None, f"{name} must be a JPG, PNG, or WebP image.", ""
    return content, "", kind


def _store_photo(job_id: str, content: bytes, suffix: str) -> dict[str, str]:
    filename = f"{job_id}-{int(time.time() * 1000)}-{secrets.token_hex(3)}{suffix}"
    (PHOTO_DIR / filename).write_bytes(content)
    return store.add_photo(job_id, filename)


@app.post("/api/jobs/{job_id}/photos")
async def api_add_photo(job_id: str, file: UploadFile = File(...), user: dict[str, Any] = Depends(require_user)) -> dict[str, str]:
    job = _job_or_404(job_id, user)
    content, error, suffix = await _validated_photo(file)
    if error or content is None:
        raise HTTPException(status_code=400, detail=error or "Photo is required")
    return _store_photo(job["id"], content, suffix)


@app.get("/photos/{filename}")
def serve_photo(filename: str, user: dict[str, Any] = Depends(require_user)) -> FileResponse:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    job = store.job_for_photo(filename)
    if job is None or not store.can_access(user, job):
        raise HTTPException(status_code=404, detail="Photo not found")
    path = PHOTO_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(path)


def _login_error_redirect(request: Request, email: str, message: str, next_url: str) -> RedirectResponse:
    request.session["login_email"] = email.strip()
    request.session["login_error"] = message
    flash(request, message, "error")
    location = "/?error=1"
    if next_url:
        location += "&next=" + quote(next_url, safe="")
    return RedirectResponse(location, status_code=303)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), csrf_token: str = Form(""), next: str = Form("")) -> RedirectResponse:
    assert_csrf(request, csrf_token)
    next_url = safe_next(next, "")
    if _login_blocked(request, email):
        return _login_error_redirect(request, email, "Too many sign-in attempts. Try again in 15 minutes.", next_url)
    user = store.authenticate(email, password)
    if user is None:
        _login_failed(request, email)
        return _login_error_redirect(request, email, "Email or password is wrong.", next_url)
    _login_succeeded(request, email)
    request.session.pop("login_email", None)
    request.session.pop("login_error", None)
    response = RedirectResponse(next_url or "/dashboard", status_code=303)
    response.set_cookie("sitelog_session", make_token(user["id"]), httponly=True, samesite="lax")
    return response


@app.get("/", response_class=HTMLResponse)
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = "", next: str = "") -> HTMLResponse:
    next_url = safe_next(next, "")
    if _page_user(request):
        return RedirectResponse(next_url or "/dashboard", status_code=303)
    message = ""
    email = ""
    if error:
        message = str(request.session.get("login_error") or "Email or password is wrong.")
        email = str(request.session.get("login_email") or "")
    return TEMPLATES.TemplateResponse(request, "login.html", {
        "error": message,
        "email": email,
        "next": next_url,
        "show_demo_logins": _show_demo_logins(),
    })


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form("")) -> RedirectResponse:
    assert_csrf(request, csrf_token)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("sitelog_session")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    return TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "sections": SECTIONS,
            "counts": store.counts(user),
            "jobs": store.list_jobs(user, limit=8),
        },
    )


@app.get("/jobs", response_class=HTMLResponse)
def job_section(request: Request, status: str = "new", q: str = "", sort: str = "newest", page: str = "") -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    raw_status = request.query_params.get("status")
    raw_sort = request.query_params.get("sort")
    status = raw_status if raw_status in STATUS_LABELS or raw_status == "all" else "new"
    sort = raw_sort if raw_sort in JOB_SORTS else "newest"
    query = q.strip()
    listing = _filter_jobs(user, status, query, sort, _page_number(page) if page else 1)
    canonical = _jobs_href(status, query, sort, listing["page"])
    if raw_status != status or raw_sort != sort or request.query_params.get("q", "") != query or (page and (_page_number(page) != listing["page"] or page == "1")):
        return RedirectResponse(canonical, status_code=303)
    title = "All jobs" if status == "all" else f"{STATUS_LABELS[status]} jobs"
    list_url = _jobs_href(status, query, sort, listing["page"])
    return TEMPLATES.TemplateResponse(
        request,
        "section.html",
        {
            "user": user,
            "filters": JOB_FILTERS,
            "status": status,
            "title": title,
            "q": query,
            "sort": sort,
            "jobs": listing["jobs"],
            "counts": listing["counts"],
            "total": listing["total"],
            "page": listing["page"],
            "pages": listing["pages"],
            "list_url": list_url,
            "prev_url": _jobs_href(status, query, sort, listing["page"] - 1),
            "next_url": _jobs_href(status, query, sort, listing["page"] + 1),
        },
    )


def _posted_fields(form) -> dict[str, Any]:
    return {
        "work_ref": str(form.get("work_ref") or ""),
        "job_date": str(form.get("job_date") or ""),
        "client": str(form.get("client") or ""),
        "site_address": str(form.get("site_address") or ""),
        "arrival_time": str(form.get("arrival_time") or ""),
        "departure_time": str(form.get("departure_time") or ""),
        "issue": str(form.get("issue") or ""),
        "findings": str(form.get("findings") or ""),
        "materials": str(form.get("materials") or ""),
        "part_number": str(form.get("part_number") or ""),
        "recommended_works": str(form.get("recommended_works") or ""),
        "status": str(form.get("status") or "new"),
        "engineer_ids": [str(item) for item in form.getlist("engineer_ids") if str(item).strip()],
    }


def _field_errors(posted: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not str(posted.get("work_ref") or "").strip():
        errors["work_ref"] = "Work ref is required."
    if not str(posted.get("site_address") or "").strip():
        errors["site_address"] = "Site address is required."
    return errors


def _job_payload(posted: dict[str, Any], allow_status: bool) -> dict[str, Any]:
    payload = _normalize_schedule({
        "work_ref": posted.get("work_ref"),
        "site_address": posted.get("site_address"),
        "job_date": posted.get("job_date"),
        "client": posted.get("client"),
        "arrival_time": posted.get("arrival_time"),
        "departure_time": posted.get("departure_time"),
        "issue": posted.get("issue"),
        "findings": posted.get("findings"),
        "materials": posted.get("materials"),
        "part_number": posted.get("part_number"),
        "recommended_works": posted.get("recommended_works"),
    })
    if allow_status:
        status = posted.get("status") if posted.get("status") in {"new", "in_progress", "completed"} else "new"
        payload["status"] = status
        payload["is_complete"] = status == "completed"
    return payload


def _progress_if_assigned(
    data: dict[str, Any],
    assignee_ids: Optional[list[str]],
    *,
    current_status: str = "new",
) -> dict[str, Any]:
    """Jobs with at least one engineer move from new → in_progress."""
    payload = dict(data)
    status = payload.get("status") if payload.get("status") in {"new", "in_progress", "completed"} else current_status
    if status not in {"new", "in_progress", "completed"}:
        status = "new"
    if assignee_ids and status == "new":
        payload["status"] = "in_progress"
        payload["is_complete"] = False
    elif "status" not in payload and status in {"new", "in_progress", "completed"}:
        payload["status"] = status
    return payload


def _render_job_form(request: Request, user: dict[str, Any], job: Optional[dict[str, Any]], posted: Optional[dict[str, Any]], errors: dict[str, str]) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request, "form.html", {
        "user": user,
        "engineers": store.list_engineers(),
        "job": job,
        "posted": posted,
        "errors": errors,
    })


async def _photos_from_form(form) -> tuple[list[tuple[bytes, str]], list[str]]:
    ready: list[tuple[bytes, str]] = []
    errors: list[str] = []
    for upload in form.getlist("photos"):
        if not getattr(upload, "filename", ""):
            continue
        content, error, suffix = await _validated_photo(upload)
        if error:
            errors.append(error)
        elif content is not None:
            ready.append((content, suffix))
    return ready, errors


@app.get("/jobs/new", response_class=HTMLResponse)
def new_job_page(request: Request) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    return _render_job_form(request, user, None, None, {})


@app.post("/jobs/new")
async def create_job_page(request: Request):
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    form = await request.form()
    assert_csrf(request, str(form.get("csrf_token") or ""))
    posted = _posted_fields(form)
    errors = _field_errors(posted)
    photos, photo_errors = await _photos_from_form(form)
    if photo_errors:
        errors["photos"] = " ".join(photo_errors)
    if errors:
        return _render_job_form(request, user, None, posted, errors)
    chosen = _assignees_for(user, posted["engineer_ids"])
    existing = store.job_by_work_ref(posted["work_ref"].strip())
    if existing is not None and not store.can_access(user, existing):
        return RedirectResponse("/dashboard", status_code=303)
    job = store.save_job(_progress_if_assigned(_job_payload(posted, True), chosen), chosen)
    for content, suffix in photos:
        _store_photo(job["id"], content, suffix)
    flash(request, "Job saved.")
    return RedirectResponse(f"/jobs/{job['id']}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    job = _job_or_404(job_id, user)
    back_url = safe_next(
        request.query_params.get("next", ""),
        f"/jobs?status={job['status']}&sort=newest",
        ("/jobs", "/dashboard"),
    )
    return TEMPLATES.TemplateResponse(request, "detail.html", {
        "user": user,
        "job": job,
        "back_url": back_url,
        "back_label": _back_label(back_url),
    })


@app.get("/jobs/{job_id}/edit", response_class=HTMLResponse)
def edit_job_page(request: Request, job_id: str) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    job = _job_or_404(job_id, user)
    return _render_job_form(request, user, job, None, {})


@app.post("/jobs/{job_id}/edit")
async def save_job_page(request: Request, job_id: str):
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    job = _job_or_404(job_id, user)
    form = await request.form()
    assert_csrf(request, str(form.get("csrf_token") or ""))
    posted = _posted_fields(form)
    errors = _field_errors(posted)
    photos, photo_errors = await _photos_from_form(form)
    if photo_errors:
        errors["photos"] = " ".join(photo_errors)
    if errors:
        return _render_job_form(request, user, job, posted, errors)
    allow_status = job["status"] != "discarded"
    chosen = _assignees_for(user, posted["engineer_ids"])
    payload = _job_payload(posted, allow_status)
    if allow_status:
        payload = _progress_if_assigned(payload, chosen, current_status=job["status"])
    store.update_job(job_id, payload, chosen)
    for content, suffix in photos:
        _store_photo(job_id, content, suffix)
    remove_id = str(form.get("remove_photo") or "").strip()
    if remove_id and store.delete_photo(job_id, remove_id):
        flash(request, "Photo removed.")
        return RedirectResponse(f"/jobs/{job_id}/edit", status_code=303)
    flash(request, "Job saved.")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}/discard", response_class=HTMLResponse)
def discard_page(request: Request, job_id: str) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    job = _job_or_404(job_id, user)
    return TEMPLATES.TemplateResponse(request, "discard.html", {"user": user, "job": job})


@app.post("/jobs/{job_id}/discard")
def discard_job(request: Request, job_id: str, reason: str = Form(...), csrf_token: str = Form("")) -> RedirectResponse:
    assert_csrf(request, csrf_token)
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    job = _job_or_404(job_id, user)
    if reason.strip() and job["status"] != "discarded":
        store.discard_job(job_id, reason)
        flash(request, "Job discarded.")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/restore")
def restore_job(request: Request, job_id: str, csrf_token: str = Form("")) -> RedirectResponse:
    assert_csrf(request, csrf_token)
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    job = _job_or_404(job_id, user)
    if job["status"] == "discarded":
        store.restore_job(job_id)
        flash(request, "Job restored.")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


def _chat_page(request: Request, user: dict[str, Any], job: Optional[dict[str, Any]]) -> HTMLResponse:
    messages = store.list_messages(job["id"], limit=10) if job else []
    has_more = False
    if messages and job and len(messages) == 10:
        has_more = store.has_messages_before(job["id"], messages[0]["created_at"])
    return TEMPLATES.TemplateResponse(
        request,
        "chat.html",
        {
            "user": user,
            "job": job,
            "messages": messages,
            "has_more": has_more,
            "chats": store.chat_threads(user),
        },
    )


@app.get("/chats", response_class=HTMLResponse)
def chats_page(request: Request) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    return _chat_page(request, user, None)


@app.get("/jobs/{job_id}/chat", response_class=HTMLResponse)
def chat_page(request: Request, job_id: str) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    job = _job_or_404(job_id, user)
    return _chat_page(request, user, job)


@app.post("/jobs/{job_id}/chat")
def post_chat(request: Request, job_id: str, body: str = Form(...), csrf_token: str = Form("")) -> Any:
    assert_csrf(request, csrf_token)
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    _job_or_404(job_id, user)
    saved_message = None
    cleaned = body.strip()
    if cleaned:
        saved_message = store.add_message(job_id, user["id"], cleaned)
        if saved_message:
            saved_message["when"] = _format_when(saved_message.get("created_at"))
    if request.headers.get("accept", "").startswith("application/json") or request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JSONResponse({"status": "ok", "message": saved_message})
    return RedirectResponse(f"/jobs/{job_id}/chat", status_code=303)
 
 
@app.get("/route-planner", response_class=HTMLResponse)
def route_planner_page(
    request: Request,
    date: str = "",
    engineer_id: str = "",
    status: str = "",
    start_address: str = "HQ Office, 14 Baker Street, London NW1 6XE",
    start_job_id: str = "",
    round_trip: bool = False,
) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    engineers = store.list_engineers()
    all_jobs = store.list_jobs(user)
    depot_jobs = [job for job in all_jobs if (job.get("site_address") or "").strip()]

    start_job = None
    if start_job_id.strip():
        candidate = store.get_job(start_job_id.strip())
        if candidate is not None and store.can_access(user, candidate) and (candidate.get("site_address") or "").strip():
            start_job = candidate
            start_address = str(candidate["site_address"]).strip()

    filtered_jobs = []
    for j in all_jobs:
        if start_job is not None and j["id"] == start_job["id"]:
            continue
        if date and j.get("job_date") != date:
            continue
        if engineer_id:
            assigned_ids = [str(e.get("id")) for e in (j.get("assignees") or [])]
            if engineer_id not in assigned_ids and j.get("engineer_id") != engineer_id:
                continue
        if status:
            if j.get("status") != status:
                continue
        else:
            if j.get("status") not in {"new", "in_progress"}:
                continue
        filtered_jobs.append(j)

    route = plan_route(start_address, filtered_jobs, round_trip=round_trip)
    return TEMPLATES.TemplateResponse(
        request,
        "route_planner.html",
        {
            "user": user,
            "engineers": engineers,
            "jobs": all_jobs,
            "depot_jobs": depot_jobs,
            "filtered_jobs": filtered_jobs,
            "route": route,
            "current_date": date,
            "current_engineer_id": engineer_id,
            "current_status": status,
            "start_address": start_address,
            "start_job_id": start_job["id"] if start_job else "",
            "round_trip": round_trip,
        },
    )


@app.get("/api/route-plan")
def api_get_route_plan(
    start_address: str = "HQ Office, 14 Baker Street, London NW1 6XE",
    round_trip: bool = False,
    date: str = "",
    engineer_id: str = "",
    status: str = "",
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    all_jobs = store.list_jobs(user)
    filtered = []
    for j in all_jobs:
        if date and j.get("job_date") != date:
            continue
        if engineer_id:
            assigned_ids = [str(e.get("id")) for e in (j.get("assignees") or [])]
            if engineer_id not in assigned_ids and j.get("engineer_id") != engineer_id:
                continue
        if status:
            if j.get("status") != status:
                continue
        else:
            if j.get("status") not in {"new", "in_progress"}:
                continue
        filtered.append(j)
    return plan_route(start_address, filtered, round_trip=round_trip)


@app.post("/api/route-plan")
def api_post_route_plan(
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    start_address = str(payload.get("start_address") or "HQ Office, 14 Baker Street, London NW1 6XE")
    round_trip = bool(payload.get("round_trip", False))
    job_ids = payload.get("job_ids")
    if job_ids is not None:
        jobs = []
        for jid in job_ids:
            j = store.get_job(str(jid))
            if j and store.can_access(user, j):
                jobs.append(j)
    else:
        all_jobs = store.list_jobs(user)
        date = str(payload.get("date") or "")
        engineer_id = str(payload.get("engineer_id") or "")
        status = str(payload.get("status") or "")
        jobs = []
        for j in all_jobs:
            if date and j.get("job_date") != date:
                continue
            if engineer_id:
                assigned_ids = [str(e.get("id")) for e in (j.get("assignees") or [])]
                if engineer_id not in assigned_ids and j.get("engineer_id") != engineer_id:
                    continue
            if status:
                if j.get("status") != status:
                    continue
            else:
                if j.get("status") not in {"new", "in_progress"}:
                    continue
            jobs.append(j)
    return plan_route(start_address, jobs, round_trip=round_trip)


def _saved_mailbox_password(host: str, port: int, email: str, password: str) -> str:
    if password:
        return password
    current = store.mailbox_secret()
    if current and current["host"] == host.strip() and int(current["port"] or 0) == port and current["email"] == email.strip():
        return str(current["password"] or "")
    return ""


@app.get("/engineers", response_class=HTMLResponse)
def engineers_page(request: Request) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    _office_user(user)
    show_add = request.query_params.get("action") == "new"
    return TEMPLATES.TemplateResponse(request, "engineers.html", {
        "user": user,
        "engineers": store.list_engineers(),
        "posted": {},
        "errors": {},
        "show_add": show_add,
    })


@app.get("/engineers/new", response_class=HTMLResponse)
def new_engineer_page(request: Request) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    _office_user(user)
    return TEMPLATES.TemplateResponse(request, "engineers.html", {
        "user": user,
        "engineers": store.list_engineers(),
        "posted": {},
        "errors": {},
        "show_add": True,
    })


@app.post("/engineers")
def create_engineer_page(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    assert_csrf(request, csrf_token)
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    _office_user(user)
    posted = {"name": name, "email": email, "phone": phone, "password": password}
    try:
        engineer = store.create_engineer(name, email, phone, password)
    except ValueError as exc:
        return TEMPLATES.TemplateResponse(request, "engineers.html", {
            "user": user,
            "engineers": store.list_engineers(),
            "posted": posted,
            "errors": {"form": str(exc)},
            "show_add": True,
        }, status_code=400)
    login_url = str(request.base_url).rstrip("/") + "/"
    problem = send_login_email(
        to_name=engineer["name"],
        to_email=engineer["email"],
        password=password,
        login_url=login_url,
        phone=engineer.get("phone", phone),
        role=engineer.get("role", "engineer"),
        engineer_id=engineer.get("id", ""),
    )
    if problem:
        flash(request, f"{engineer['name']} was added, but the welcome email was not sent. {problem}", "error")
    else:
        flash(request, f"Engineer registered! Welcome email with all profile and login details was sent to {engineer['email']}.")
    return RedirectResponse("/engineers", status_code=303)


@app.get("/mailbox", response_class=HTMLResponse)
def mailbox_page(request: Request) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    _office_user(user)
    return TEMPLATES.TemplateResponse(request, "mailbox.html", {"user": user, "mailbox": store.get_mailbox()})


@app.post("/mailbox")
def save_mailbox_page(request: Request, host: str = Form(...), email: str = Form(...), port: int = Form(993), password: str = Form(""), csrf_token: str = Form("")) -> RedirectResponse:
    assert_csrf(request, csrf_token)
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    _office_user(user)
    try:
        store.save_mailbox(host, _clean_port(port), email, password)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/mailbox", status_code=303)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    flash(request, "Saved. The server checks the inbox about once a minute.")
    return RedirectResponse("/mailbox", status_code=303)


@app.post("/mailbox/test")
def test_mailbox_page(request: Request, host: str = Form(...), email: str = Form(...), port: int = Form(993), password: str = Form(""), csrf_token: str = Form("")) -> RedirectResponse:
    assert_csrf(request, csrf_token)
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    _office_user(user)
    try:
        clean_port = _clean_port(port)
        secret = _saved_mailbox_password(host, clean_port, email, password)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/mailbox", status_code=303)
    except RuntimeError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/mailbox", status_code=303)
    problem = probe_mailbox(host, clean_port, email, secret)
    if problem:
        flash(request, problem, "error")
    else:
        flash(request, "Connected to the mailbox.")
    return RedirectResponse("/mailbox", status_code=303)


@app.get("/mailbox/disconnect", response_class=HTMLResponse)
def mailbox_disconnect_page(request: Request) -> HTMLResponse:
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    _office_user(user)
    mailbox = store.get_mailbox()
    if mailbox is None:
        return RedirectResponse("/mailbox", status_code=303)
    return TEMPLATES.TemplateResponse(request, "mailbox_disconnect.html", {"user": user, "mailbox": mailbox})


@app.post("/mailbox/disconnect")
def mailbox_disconnect(request: Request, csrf_token: str = Form("")) -> RedirectResponse:
    assert_csrf(request, csrf_token)
    user = _page_user(request)
    if user is None:
        return _need_login(request)
    _office_user(user)
    store.delete_mailbox()
    flash(request, "Mailbox disconnected. Jobs already created from email stay on the board.")
    return RedirectResponse("/mailbox", status_code=303)
