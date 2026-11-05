"""Account-scoped IM connections owned by the server, independent of web clients."""

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .im import control_update, message_session, message_time, session_key, visible_message
from .im_media import message_images, profile_images
from .providers.base import ProviderError

SDK_PATH = Path(__file__).parent / "static/im.js"
ORIGIN = "https://course-helper.invalid/"


@dataclass
class Connection:
    task: asyncio.Task | None = None
    page: object = None
    warmup: asyncio.Task | None = None
    access_token: str = ""
    connected: bool = False
    user_id: str = ""
    status: str = "消息服务正在连接"
    attempted: asyncio.Event = field(default_factory=asyncio.Event)
    events: deque = field(default_factory=lambda: deque(maxlen=1000))
    sequence: int = 0


class MessageService:
    def __init__(self, service):
        self.service = service
        self.connections = {}
        self.runtime = None
        self.browser = None
        self.browser_lock = asyncio.Lock()
        self.supervisor = None
        self.closing = False

    async def start(self):
        if self.supervisor is None:
            self.closing = False
            self.reconcile()
            self.supervisor = asyncio.create_task(self._supervise())

    def reconcile(self):
        for account in self.service.accounts("chaoxing"):
            self.maintain(account["id"])

    async def _supervise(self):
        while True:
            self.reconcile()
            await asyncio.sleep(15)

    def maintain(self, account_id):
        if self.closing:
            raise ProviderError("消息服务正在关闭")
        if self.service.provider(account_id).platform != "chaoxing":
            raise ProviderError("消息服务仅支持学习通账号")
        state = self.connections.setdefault(account_id, Connection())
        if state.task is None or state.task.done():
            state.attempted.clear()
            state.task = asyncio.create_task(self._run(account_id, state))
        return state

    async def ensure(self, account_id):
        state = self.maintain(account_id)
        try:
            await asyncio.wait_for(state.attempted.wait(), timeout=45)
        except TimeoutError as exc:
            raise ProviderError("消息服务正在连接，后端会继续重试") from exc
        if not state.connected:
            raise ProviderError(state.status)
        return state

    def snapshot(self, account_id, after=0):
        state = self.connections.get(account_id)
        if not state:
            return {"status": "未连接", "connected": False, "user_id": "", "sequence": 0, "events": []}
        return {"status": state.status, "connected": state.connected, "user_id": state.user_id,
                "sequence": state.sequence, "events": [e for seq, e in state.events if seq > after]}

    def _status(self, state, value, connected=False):
        state.status = value
        state.connected = connected

    async def _browser(self):
        async with self.browser_lock:
            if self.runtime is None:
                from playwright.async_api import async_playwright

                self.runtime = await async_playwright().start()
            if self.browser is None or not self.browser.is_connected():
                self.browser = await self.runtime.chromium.launch(headless=True)
            return self.browser

    async def _run(self, account_id, state):
        delay = 2
        while not self.closing:
            context = None
            page = None
            try:
                self._status(state, "消息服务正在连接")
                credentials = await self.service.provider(account_id).im_session()
                state.user_id = str(credentials["userName"])
                state.access_token = credentials["accessToken"]
                browser = await self._browser()
                context = await browser.new_context(
                    storage_state=self.service.store.get("im-browser", account_id, None))
                page = await context.new_page()
                state.page = page
                # An isolated synthetic origin gives the SDK persistent device storage without
                # visiting an external page or exposing an unauthenticated application route.
                await page.route(ORIGIN, lambda route: route.fulfill(
                    status=200, content_type="text/html", body="<!doctype html><title>IM worker</title>"))
                await page.goto(ORIGIN)
                await page.expose_function("emitEvent", lambda name, data: self._event(account_id, state, name, data))
                await page.add_script_tag(path=str(SDK_PATH))
                result = await asyncio.wait_for(page.evaluate(
                    "account => window.courseIM.connect(account)", {**credentials, "accountId": account_id}), 40)
                if result.get("error"):
                    raise ProviderError(result["error"])
                self.service.store.put("im-browser", account_id, await context.storage_state())
                self._status(state, "消息服务已连接", True)
                state.attempted.set()
                state.warmup = asyncio.create_task(self._warm_images(account_id, state))
                state.warmup.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                delay = 2
                while not self.closing:
                    await asyncio.sleep(3)
                    status = await asyncio.wait_for(page.evaluate("window.courseIM.status()"), 10)
                    if not status.get("connected"):
                        raise ProviderError("消息服务连接已断开")
                    # Refresh credentials before expiry without interrupting the shared socket.
                    if status.get("tokenExpiring"):
                        credentials = await self.service.provider(account_id).im_session()
                        result = await asyncio.wait_for(page.evaluate(
                            "token => window.courseIM.renewToken(token)", credentials["accessToken"]), 30)
                        if result.get("error"):
                            raise ProviderError(result["error"])
                        state.access_token = credentials["accessToken"]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # SDK/provider errors are user-facing; browser exceptions can contain credentials.
                detail = str(exc) if isinstance(exc, ProviderError) else "连接运行环境异常，请检查网络或执行 uv run playwright install chromium"
                self._status(state, f"{detail}；{delay} 秒后自动重连")
                state.attempted.set()
            finally:
                if state.warmup:
                    state.warmup.cancel()
                    await asyncio.gather(state.warmup, return_exceptions=True)
                    state.warmup = None
                state.connected = False
                state.page = None
                if context:
                    with suppress(Exception):
                        self.service.store.put("im-browser", account_id,
                                               await asyncio.wait_for(context.storage_state(), 5))
                    with suppress(Exception):
                        await asyncio.wait_for(page.evaluate("window.courseIM?.close()"), 3)
                    with suppress(Exception):
                        await asyncio.wait_for(context.close(), 10)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    def _event(self, account_id, state, name, data):
        if self.connections.get(account_id) is not state or self.closing:
            return
        if name == "course-im-status":
            status = str(data.get("status", "未连接"))
            connected = state.connected if "错误" in status else status == "消息服务已连接"
            self._status(state, status, connected)
            return
        if name not in ("course-im-message", "course-im-control"):
            return
        message = {**data, "_account_id": account_id}
        self.cache_message(account_id, message, state.user_id)
        state.sequence += 1
        state.events.append((state.sequence, {"name": name, "message": message}))

    def cache_message(self, account_id, message, user_id):
        store = self.service.store
        key = message_session(message, user_id)
        if visible_message(message) and key:
            mid = str(message.get("serverMsgId") or message.get("id") or message.get("mid") or uuid4().hex)
            store.put("im-messages", f"{account_id}:{key}:{mid}",
                      {"account_id": account_id, "message": message, "session": key})
            kind, target = key.split(":", 1)
            record = store.get("im-conversations", f"{account_id}:{key}", {})
            conversation = record.get("conversation") or {"id": target, "chatType": kind}
            if message_time(message) >= message_time(conversation.get("lastMessage") or {}):
                conversation["lastMessage"] = message
            store.put("im-conversations", f"{account_id}:{key}",
                      {"account_id": account_id, "key": key, "conversation": conversation})
        self.service.im_images.register_message(account_id, message)
        self.service.im_images.schedule(account_id, message_images(message))
        event = control_update(message, user_id)
        self._cache_control(account_id, event, message, user_id)

    async def _warm_images(self, account_id, state):
        """Walk available history in the background; never mark history as newly received."""
        images, store = self.service.im_images, self.service.store
        urls = profile_images(store.get("accounts", account_id, {}))
        conversations = {}
        for row in store.all("im-messages").values():
            if row.get("account_id") == account_id:
                message = row["message"]
                images.register_message(account_id, message)
                urls.extend(message_images(message))
                key = row.get("session") or message_session(message, state.user_id)
                if key:
                    conversations[key] = key.split(":", 1)
        for row in store.all("im-conversations").values():
            if row.get("account_id") == account_id:
                conversation = row["conversation"]
                urls.extend(profile_images(conversation))
                urls.extend(message_images(conversation.get("lastMessage") or {}))
                key = session_key(conversation.get("chatType"), conversation.get("id"))
                if key:
                    conversations[key] = key.split(":", 1)
        await images.prefetch(account_id, urls)
        results = await asyncio.gather(self.call(account_id, "conversations"), self.call(account_id, "groups"),
                                       return_exceptions=True)
        for index, result in enumerate(results):
            if isinstance(result, Exception):
                continue
            urls = []
            for item in result.get("data", []):
                kind = "groupChat" if index == 1 else item.get("conversationType") or item.get("chatType")
                target = str(item.get("groupId") or item.get("groupid") or item.get("conversationId")
                             or item.get("id") or "")
                key = session_key(kind, target)
                if key:
                    conversations[key] = (kind, target)
                urls.extend(message_images(item.get("lastMessage") or {}))
            await images.prefetch(account_id, urls)
        provider = self.service.provider(account_id)
        async def profiles(users=(), groups=()):
            try:
                result = await provider.im_profiles(users=users, groups=groups)
                await images.prefetch(account_id, [url for profile in result.values() for url in profile_images(profile)])
            except Exception:
                # Avatar failures must not prevent preloading message images.
                return
        await profiles(users=[target for kind, target in conversations.values() if kind == "singleChat"],
                       groups=[target for kind, target in conversations.values() if kind == "groupChat"])
        limit = asyncio.Semaphore(2)
        async def history(kind, target):
            async with limit:
                cursor, seen = -1, set()
                while state.connected and self.connections.get(account_id) is state:
                    try:
                        result = await self.call(account_id, "historyPage", target, kind, cursor)
                    except Exception:
                        return
                    if result.get("error"):
                        return
                    urls, users = [], set()
                    for raw in result.get("data", []):
                        message = {**raw, "chatType": kind, "_account_id": account_id}
                        message.setdefault("to", target)
                        if message_images(message):
                            self.cache_message(account_id, message, state.user_id)
                            urls.extend(message_images(message))
                        users.add(str(message.get("from") or "").split("/")[0])
                    await images.prefetch(account_id, urls)
                    await profiles(users=users - {""})
                    next_cursor = result.get("cursor")
                    if result.get("isLast") or next_cursor is None or str(next_cursor) in seen:
                        return
                    seen.add(str(next_cursor))
                    cursor = next_cursor
                    await asyncio.sleep(0.1)
        await asyncio.gather(*(history(*conversation) for conversation in conversations.values()))

    def _cache_control(self, account_id, event, message, user_id):
        key = event.get("key")
        if not key:
            return
        store, kind = self.service.store, event["kind"]
        prefix = f"{account_id}:{key}"
        if kind == "local-read":
            store.put("im-read", prefix, max(store.get("im-read", prefix, 0), event["time"]))
        elif kind == "recall":
            store.put("im-recalls", f"{prefix}:{event['mid']}",
                      {"sender": event["sender"], "confirmed": message.get("type") == "recall"})
        else:
            if kind == "peer-read":
                receipts = {event["mid"]: {"read": True}}
            elif kind == "group-read":
                receipts = {str(mid): {"count": count} for mid, count in event["counts"].items()
                            if isinstance(count, int) and count >= 0}
            elif kind == "peer-channel":
                receipts = {str(row["message"].get("serverMsgId") or row["message"].get("id") or ""): {"read": True}
                            for row in store.all("im-messages").values()
                            if row.get("account_id") == account_id and row.get("session") == key
                            and str(row["message"].get("from") or "").split("/")[0] == user_id}
            else:
                return
            for mid, receipt in receipts.items():
                if mid:
                    previous = store.get("im-receipts", f"{prefix}:{mid}", {})
                    if "count" in receipt:
                        receipt["count"] = max(previous.get("count", 0), receipt["count"])
                    store.put("im-receipts", f"{prefix}:{mid}", {**previous, **receipt})

    async def call(self, account_id, method, *args):
        if method not in ("groups", "conversations", "historyPage", "sendText"):
            raise ProviderError("不支持的消息操作")
        state = await self.ensure(account_id)
        page = state.page
        if page is None:
            raise ProviderError("消息服务正在重连")
        result = await asyncio.wait_for(page.evaluate(
            "({method,args}) => window.courseIM[method](...args)", {"method": method, "args": args}), 60)
        if method == "sendText" and result.get("data"):
            self._event(account_id, state, "course-im-message", result["data"])
        return result

    async def remove(self, account_id):
        state = self.connections.get(account_id)
        if state and state.task:
            state.task.cancel()
            await asyncio.gather(state.task, return_exceptions=True)
        if self.connections.get(account_id) is state:
            self.connections.pop(account_id, None)

    async def close(self):
        self.closing = True
        if self.supervisor:
            self.supervisor.cancel()
            await asyncio.gather(self.supervisor, return_exceptions=True)
            self.supervisor = None
        await asyncio.gather(*(self.remove(key) for key in list(self.connections)))
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.runtime:
            await self.runtime.stop()
            self.runtime = None
