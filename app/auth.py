"""Database-backed administrator credentials and browser access control."""

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

COOKIE = "course_admin"
CSRF_COOKIE = "course_csrf"
SESSION_SECONDS = 12 * 60 * 60


class AdminAuth:
    def __init__(self, store):
        self.store = store
        self.attempts = defaultdict(deque)
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS administrator (
                id INTEGER PRIMARY KEY CHECK (id=1), username TEXT NOT NULL,
                salt BLOB NOT NULL, password_hash BLOB NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY, expires REAL NOT NULL)""")

    def account(self):
        with self.store.connect() as db:
            return db.execute("SELECT username, salt, password_hash FROM administrator WHERE id=1").fetchone()

    @staticmethod
    def password_hash(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)

    @staticmethod
    def validate(username, password, confirmation):
        if not username.strip() or len(username.strip()) > 64:
            raise ValueError("管理员名称需为 1–64 个字符")
        if not 8 <= len(password) <= 128:
            raise ValueError("密码需为 8–128 个字符")
        if password != confirmation:
            raise ValueError("两次输入的密码不一致")

    def setup(self, username, password, confirmation):
        self.validate(username, password, confirmation)
        salt = secrets.token_bytes(16)
        digest = self.password_hash(password, salt)
        with self.store.connect() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO administrator VALUES (1,?,?,?)", (username.strip(), salt, digest)
            ).rowcount
        if not inserted:
            raise ValueError("管理员已设置，请登录")

    def login(self, username, password):
        account = self.account()
        salt = account[1] if account else b"\0" * 16
        digest = self.password_hash(password, salt)
        if not account or not hmac.compare_digest(digest, account[2]) or username.strip() != account[0]:
            raise ValueError("管理员名称或密码错误")
        token = secrets.token_urlsafe(32)
        with self.store.connect() as db:
            # A concurrent credential change must not create a session for old credentials.
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT password_hash FROM administrator WHERE id=1").fetchone()
            if not current or not hmac.compare_digest(current[0], digest):
                raise ValueError("凭据已变更，请重新登录")
            db.execute("DELETE FROM admin_sessions WHERE expires<=?", (time.time(),))
            db.execute(
                "INSERT INTO admin_sessions VALUES (?,?)", (self.token_hash(token), time.time() + SESSION_SECONDS)
            )
        return token

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def valid(self, token):
        if not token or len(token) > 128:
            return False
        with self.store.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM admin_sessions WHERE token_hash=? AND expires>?",
                    (self.token_hash(token), time.time()),
                ).fetchone()
                is not None
            )

    def logout(self, token):
        with self.store.connect() as db:
            db.execute("DELETE FROM admin_sessions WHERE token_hash=?", (self.token_hash(token),))

    def change(self, token, current_password, username, password, confirmation):
        self.validate(username, password, confirmation)
        account = self.account()
        if not account or not hmac.compare_digest(self.password_hash(current_password, account[1]), account[2]):
            raise ValueError("当前密码错误")
        salt = secrets.token_bytes(16)
        digest = self.password_hash(password, salt)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute(
                "SELECT 1 FROM admin_sessions WHERE token_hash=? AND expires>?", (self.token_hash(token), time.time())
            ).fetchone()
            changed = (
                db.execute(
                    "UPDATE administrator SET username=?,salt=?,password_hash=? WHERE id=1 AND password_hash=?",
                    (username.strip(), salt, digest, account[2]),
                ).rowcount
                if session
                else 0
            )
            if not changed:
                raise ValueError("登录已失效，请重新登录")
            db.execute("DELETE FROM admin_sessions")

    def limited(self, key):
        now = time.monotonic()
        # Bound memory and discard old entries, including inactive clients.
        for address in list(self.attempts):
            while self.attempts[address] and self.attempts[address][0] <= now - 60:
                self.attempts[address].popleft()
            if not self.attempts[address]:
                del self.attempts[address]
        attempts = self.attempts[key]
        if len(attempts) >= 5:
            return True
        attempts.append(now)
        return False


class AdminMiddleware:
    def __init__(self, app, auth):
        self.app, self.auth = app, auth

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        public = path in ("/health", "/admin/login", "/admin/setup") or path.startswith("/api/")
        if public and scope["type"] == "http":
            return await self.app(scope, receive, send)
        token = HTTPConnection(scope).cookies.get(COOKIE, "")
        if not await run_in_threadpool(self.auth.valid, token):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            destination = "/admin/login" if self.auth.account() else "/admin/setup"
            response = (
                RedirectResponse(destination, 303)
                if scope["method"] == "GET"
                else JSONResponse({"detail": "请先登录管理员账户"}, status_code=401)
            )
            return await response(scope, receive, send)
        if scope["type"] == "websocket":
            # Persistent sockets must stop accepting events and sending data after revocation.
            closed = False

            async def close():
                nonlocal closed
                if not closed:
                    closed = True
                    await send({"type": "websocket.close", "code": 1008})

            async def authenticated_receive():
                if closed:
                    return {"type": "websocket.disconnect", "code": 1008}
                message = await receive()
                if message["type"] == "websocket.receive" and not await run_in_threadpool(self.auth.valid, token):
                    await close()
                    return {"type": "websocket.disconnect", "code": 1008}
                return message

            async def authenticated_send(message):
                if closed:
                    return
                if message["type"] == "websocket.send" and not await run_in_threadpool(self.auth.valid, token):
                    await close()
                    return
                await send(message)

            return await self.app(scope, authenticated_receive, authenticated_send)
        return await self.app(scope, receive, send)


def create_auth_router(auth):
    router = APIRouter(prefix="/admin", include_in_schema=False)

    def page(request, mode, error=""):
        # Keep forms in other open tabs usable while retaining a random CSRF secret.
        csrf = request.cookies.get(CSRF_COOKIE, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", csrf):
            csrf = secrets.token_urlsafe(32)
        titles = {"setup": "设置管理员", "login": "管理员登录", "account": "系统设置"}
        title = titles[mode]
        username = auth.account()[0] if mode == "account" else ""
        fields = ""
        if mode == "account":
            fields += '<label>当前密码<input name="current_password" type="password" autocomplete="current-password" required maxlength="128"></label>'
        fields += f'<label>管理员名称<input name="username" value="{escape(username, quote=True)}" autocomplete="username" required maxlength="64"></label>'
        label = "密码" if mode == "login" else "新密码" if mode == "account" else "密码（至少 8 位）"
        autocomplete = "current-password" if mode == "login" else "new-password"
        fields += f'<label>{label}<input name="password" type="password" autocomplete="{autocomplete}" required maxlength="128"></label>'
        if mode != "login":
            fields += '<label>确认密码<input name="confirmation" type="password" autocomplete="new-password" required maxlength="128" minlength="8"></label>'
        note = (
            "首次使用，请创建管理员账户。"
            if mode == "setup"
            else "修改后，所有设备需要重新登录。"
            if mode == "account"
            else "请登录后使用管理后台。"
        )
        action = "保存并重新登录" if mode == "account" else "创建管理员" if mode == "setup" else "登录"
        back = '<a href="/">返回后台</a>' if mode == "account" else ""
        response = HTMLResponse(
            f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · 课程助手</title>
<style>
:root{{color-scheme:light;--bg:#f3f6f5;--card:#fff;--text:#203d35;--line:#bccfc8;--link:#317868;--error:#a22}}
@media(prefers-color-scheme:dark){{:root{{color-scheme:dark;--bg:#101816;--card:#1a2521;--text:#e1e9e6;--line:#465c51;--link:#91d0b6;--error:#f2a4a4}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:16px system-ui}}
main{{box-sizing:border-box;max-width:440px;margin:8vh auto;padding:32px;background:var(--card);border-radius:18px;box-shadow:0 8px 40px #203d3510}}
h1{{font-size:26px}}p{{line-height:1.6}}label{{display:block;margin:20px 0}}
input{{box-sizing:border-box;width:100%;padding:12px;margin-top:8px;border:1px solid var(--line);border-radius:8px;font:inherit;background:var(--bg);color:var(--text)}}
input:focus-visible{{outline:2px solid var(--link);outline-offset:2px}}
button{{width:100%;background:#317868;color:white;border:0;border-radius:8px;padding:13px;font:inherit;cursor:pointer}}
a{{display:inline-block;margin-top:20px;color:var(--link)}}.error{{color:var(--error)}}
@media(max-width:480px){{main{{margin:24px 16px;padding:24px}}}}
</style></head>
<body><main><h1>{title}</h1><p>{note}</p><p class="error" role="alert">{escape(error)}</p>
<form method="post" action="/admin/{mode}"><input type="hidden" name="csrf" value="{csrf}">{fields}<button>{action}</button></form>{back}</main></body></html>''',
            status_code=400 if error else 200,
        )
        response.set_cookie(CSRF_COOKIE, csrf, httponly=True, secure=request.url.scheme == "https", samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @router.get("/{mode}")
    async def get_page(request: Request, mode: str):
        if mode not in ("setup", "login", "account"):
            return HTMLResponse("页面不存在", 404)
        if mode == "account":
            return RedirectResponse("/?section=settings", 303)
        if mode == "setup" and auth.account():
            return RedirectResponse("/admin/login", 303)
        if mode == "login" and not auth.account():
            return RedirectResponse("/admin/setup", 303)
        if mode in ("setup", "login") and auth.valid(request.cookies.get(COOKIE, "")):
            return RedirectResponse("/", 303)
        return page(request, mode)

    @router.post("/{mode}")
    async def submit(request: Request, mode: str):
        if mode not in ("setup", "login", "account", "logout"):
            return HTMLResponse("页面不存在", 404)
        form = await request.form()
        csrf = form.get("csrf", "")
        if (
            not isinstance(csrf, str)
            or not csrf
            or not secrets.compare_digest(csrf.encode(), request.cookies.get(CSRF_COOKIE, "").encode())
        ):
            return HTMLResponse("页面验证已失效，请刷新后重试", 403)
        if mode == "logout":
            auth.logout(request.cookies.get(COOKIE, ""))
            response = RedirectResponse("/admin/login", 303)
            response.delete_cookie(COOKIE)
            return response
        if auth.limited(request.client.host if request.client else "unknown"):
            return HTMLResponse("尝试次数过多，请一分钟后重试", 429)
        values = [form.get(key, "") for key in ("username", "password", "confirmation", "current_password")]
        if not all(isinstance(value, str) and len(value) <= 128 for value in values):
            return page(request, mode, "输入过长或格式错误")
        username, password, confirmation, current = values
        try:
            if mode == "setup":
                await run_in_threadpool(auth.setup, username, password, confirmation)
                return RedirectResponse("/admin/login", 303)
            if mode == "account":
                await run_in_threadpool(
                    auth.change, request.cookies.get(COOKIE, ""), current, username, password, confirmation
                )
                response = RedirectResponse("/admin/login", 303)
                response.delete_cookie(COOKIE)
                return response
            token = await run_in_threadpool(auth.login, username, password)
        except ValueError as exc:
            if mode == "account":
                return RedirectResponse("/?" + urlencode({"section": "settings", "error": str(exc)}), 303)
            return page(request, mode, str(exc))
        response = RedirectResponse("/", 303)
        response.set_cookie(
            COOKIE,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    return router
