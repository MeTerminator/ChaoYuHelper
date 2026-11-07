"""Account-isolated official exam webviews, kept alive across UI navigation.

The platform owns questions, autosave, timing and submission. We render its H5
page in Chromium instead of reconstructing an unverified exam submission API.
"""

import asyncio
import base64
import time
from collections import deque
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from PIL import Image, UnidentifiedImageError

from .providers.base import ProviderError

WIDTH, HEIGHT = 480, 800
IMAGE_KINDS = {"front": "前置摄像头", "rear": "后置摄像头", "screen": "截屏"}
BRIDGE = Path(__file__).with_name("static").joinpath("exam-bridge.js")


def official_url(value):
    uri = urlparse(str(value))
    host = uri.hostname or ""
    if uri.scheme != "https" or not host.endswith(".chaoxing.com") or uri.username or uri.password:
        raise ProviderError("考试页面只允许学习通官方 HTTPS 地址")
    try:
        port = uri.port
    except ValueError as exc:
        raise ProviderError("考试页面端口无效") from exc
    if port not in (None, 443):
        raise ProviderError("考试页面不支持自定义端口")
    return str(value)


def exam_list_url(course):
    return "https://mooc1-api.chaoxing.com/mooc-ans/exam/phone/task-list?" + urlencode(
        {"courseId": course["courseId"], "classId": course["classId"], "cpi": course.get("cpi", "")}
    )


def image_data(content):
    if not content or len(content) > 8 * 1024 * 1024:
        raise ProviderError("预设图片不能为空且不能超过 8 MB")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.width * image.height > 24_000_000:
                raise ProviderError("预设图片分辨率过大，最多 2400 万像素")
            image.load()
            image = image.convert("RGB")
            image.thumbnail((1920, 1920))
            output = BytesIO()
            image.save(output, format="JPEG", quality=90)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ProviderError("无法读取图片，请选择有效的 JPEG、PNG 或 WebP 图片") from exc
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()


class ExamSession:
    def __init__(self, manager, account_id, context):
        self.manager, self.account_id, self.context = manager, account_id, context
        self.page = None
        self.lock = asyncio.Lock()
        self.events = deque(maxlen=30)
        self.dialog = None
        self.closed = False
        self.created = time.time()
        self.course = None
        self.pending_urls = deque(maxlen=8)

    def event(self, message):
        # Never store signed URLs, cookies or bridge payloads in public logs.
        self.events.append({"time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"), "message": message})

    async def bridge(self, source, name, payload):
        if not self.page or source["page"] != self.page or source["frame"] != self.page.main_frame:
            return
        data = payload if isinstance(payload, dict) else {}
        if name == "CLIENT_OPEN_URL":
            try:
                self.pending_urls.append(official_url(data.get("webUrl", "")))
            except ProviderError:
                self.event("已阻止非官方页面跳转")
        elif name in ("CLIENT_EXIT_LEVEL", "CLIENT_CLOSE_WEBVIEW"):
            self.event("页面请求返回；可使用上方返回按钮")
        elif name in ("CLIENT_FACE_COLLECTION", "CLIENT_SCREEN_MONITOR", "CLIENT_SCREEN_RECORDER"):
            if str(data.get("enable", "1")) != "0":
                self.event(f"{name}：原生监考协议尚未接通，未上报成功")
        elif name not in {
            "CLIENT_CUSTOM_LEFTBTN",
            "CLIENT_CUSTOM_RIGHTBTN",
            "CLIENT_CUSTOM_MENU",
            "CLIENT_TOOLBAR",
            "CLIENT_TITLE",
            "CLIENT_REFRESH_STATUS",
            "CLIENT_SEGMENT_CONTROL",
        }:
            # Names are bounded and sanitized: page content is untrusted.
            safe_name = "".join(c for c in str(name) if c.isascii() and (c.isalnum() or c == "_"))[:70]
            self.event(f"未支持的 APP 命令：{safe_name}")

    async def on_dialog(self, dialog):
        if self.dialog:
            await dialog.dismiss()
            return
        self.dialog = dialog

    async def navigate(self, url):
        async with self.lock:
            if self.dialog:
                raise ProviderError("请先处理考试页面的确认提示")
            await self.page.goto(official_url(url), wait_until="domcontentloaded", timeout=45000)

    async def frame(self):
        # A pending JS dialog may hold the click operation. Let the next UI poll
        # handle it rather than waiting behind the click and deadlocking.
        if self.lock.locked():
            return None
        async with self.lock:
            if self.closed or not self.page or self.page.is_closed():
                raise ProviderError("考试会话已结束，请重新打开考试页面")
            if self.pending_urls and not self.dialog:
                url = self.pending_urls.popleft()
                try:
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    self.event("页面跳转失败，可尝试返回或刷新")
            if self.dialog:
                return None
            content = await self.page.screenshot(type="jpeg", quality=75, timeout=10000)
            return "data:image/jpeg;base64," + base64.b64encode(content).decode()

    async def input(self, kind, value):
        async with self.lock:
            if self.dialog:
                raise ProviderError("请先处理考试页面的确认提示")
            if kind == "tap":
                x, y = value
                if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                    await self.page.touchscreen.tap(x, y)
            elif kind == "text":
                text = str(value)
                if len(text) > 20000:
                    raise ProviderError("一次输入最多 20000 字，请分段输入")
                editable = await self.page.evaluate("""() => {
                    let element = document.activeElement;
                    try {
                        while (element?.tagName === 'IFRAME' && element.contentDocument) {
                            element = element.contentDocument.activeElement;
                        }
                    } catch (_) { return false; }
                    return !!element && !element.disabled && !element.readOnly &&
                        (element.isContentEditable || element.tagName === 'TEXTAREA' ||
                         (element.tagName === 'INPUT' &&
                          ['text','password','search','email','number','tel','url'].includes(element.type)));
                }""")
                if not editable:
                    raise ProviderError("请先点击试卷中的文字输入框，再输入文字")
                await self.page.keyboard.insert_text(text)
            elif kind == "key" and value in {
                "Backspace",
                "Delete",
                "Enter",
                "Tab",
                "ArrowLeft",
                "ArrowRight",
                "ArrowUp",
                "ArrowDown",
                "Home",
                "End",
                "ControlOrMeta+A",
            }:
                await self.page.keyboard.press(value)
            elif kind == "scroll":
                await self.page.mouse.move(WIDTH / 2, HEIGHT / 2)
                await self.page.mouse.wheel(0, max(-HEIGHT, min(HEIGHT, int(value))))
            elif kind == "back":
                await self.page.go_back(wait_until="domcontentloaded")
            elif kind == "reload":
                await self.page.reload(wait_until="domcontentloaded")

    async def answer_dialog(self, accept, prompt=""):
        # Playwright's dialog can be pending while a click holds the input lock.
        dialog, self.dialog = self.dialog, None
        if dialog:
            if accept:
                await dialog.accept(prompt)
            else:
                await dialog.dismiss()

    async def close(self):
        self.closed = True
        if self.dialog:
            await self.answer_dialog(False)
        await self.context.close()


class Exams:
    def __init__(self, service):
        self.service = service
        self.sessions = {}
        self.lock = asyncio.Lock()
        self.playwright = None
        self.browser = None

    def account(self, account_id):
        account = self.service.store.get("accounts", account_id, {})
        if account.get("platform") != "chaoxing":
            raise ProviderError("考试目前仅支持学习通账号")
        return account

    def presets(self, account_id):
        self.account(account_id)
        return self.service.store.get("exam-images", account_id, {})

    def set_image(self, account_id, kind, content):
        if kind not in IMAGE_KINDS:
            raise ProviderError("未知预设图片类型")
        images = self.presets(account_id)
        if content is None:
            images.pop(kind, None)
        else:
            images[kind] = image_data(content)
        self.service.store.put("exam-images", account_id, images)

    async def open(self, account_id, course):
        self.account(account_id)
        courses = self.service.store.get("courses", account_id, [])
        selected = next(
            (
                c
                for c in courses
                if str(c["courseId"]) == str(course["courseId"]) and str(c["classId"]) == str(course["classId"])
            ),
            None,
        )
        if selected is None:
            raise ProviderError("请先在我的课程刷新当前账号的课程列表")
        async with self.lock:
            self.account(account_id)
            current = self.sessions.get(account_id)
            if current and not current.closed and current.page and not current.page.is_closed():
                # Never navigate an ongoing attempt when the UI is reconstructed.
                return current
            if current:
                await current.close()
                self.sessions.pop(account_id, None)
            from playwright.async_api import async_playwright

            if not self.browser or not self.browser.is_connected():
                if self.playwright:
                    await self.playwright.stop()
                self.playwright = await async_playwright().start()
                try:
                    self.browser = await self.playwright.chromium.launch(headless=True)
                except Exception as exc:
                    await self.playwright.stop()
                    self.playwright = None
                    raise ProviderError("无法启动考试浏览器，请先执行 uv run playwright install chromium") from exc
            provider = self.service.provider(account_id)
            context = await self.browser.new_context(
                viewport={"width": WIDTH, "height": HEIGHT},
                device_scale_factor=1,
                is_mobile=True,
                has_touch=True,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                user_agent=provider.client.headers["User-Agent"],
                accept_downloads=False,
                service_workers="block",
            )
            session = ExamSession(self, account_id, context)
            session.course = dict(selected)
            try:
                cookies = []
                for cookie in provider.client.cookies.jar:
                    domain = cookie.domain.lstrip(".")
                    if domain != "chaoxing.com" and not domain.endswith(".chaoxing.com"):
                        continue
                    item = {
                        "name": cookie.name,
                        "value": cookie.value,
                        "domain": cookie.domain,
                        "path": cookie.path or "/",
                        "secure": cookie.secure,
                    }
                    if cookie.expires and cookie.expires > time.time():
                        item["expires"] = cookie.expires
                    elif cookie.expires:
                        continue
                    cookies.append(item)
                await context.add_cookies(cookies)

                async def guard(route):
                    try:
                        official_url(route.request.url)
                    except (ProviderError, ValueError):
                        await route.abort()
                    else:
                        await route.continue_()

                await context.route("**/*", guard)
                await context.expose_binding("courseExamBridge", session.bridge)
                await context.add_init_script(path=str(BRIDGE))
                session.page = await context.new_page()
                session.page.on("dialog", session.on_dialog)

                async def popup(page):
                    if page != session.page:
                        # H5 navigation must stay inside the one visible webview.
                        try:
                            if page.url != "about:blank":
                                session.pending_urls.append(official_url(page.url))
                        finally:
                            await page.close()

                context.on("page", popup)
                await session.navigate(exam_list_url(selected))
            except Exception:
                await session.close()
                raise
            self.sessions[account_id] = session
            return session

    async def remove(self, account_id):
        async with self.lock:
            session = self.sessions.pop(account_id, None)
            if session:
                await session.close()

    async def close(self):
        for account_id in list(self.sessions):
            await self.remove(account_id)
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
