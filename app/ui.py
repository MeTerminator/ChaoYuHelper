"""课程助手浏览器界面。"""

import asyncio
import json
import time
from datetime import datetime
from html import escape
from io import BytesIO
from urllib.parse import parse_qs, urlparse

from nicegui import app, background_tasks, ui

from .activity import (
    PickUpdates,
    rain_answer_timing,
    reference_answers,
    slide_index,
    topic_id,
    update_problem_timing,
    validate_answers,
)
from .auth import CSRF_COOKIE
from .media import image_extension
from .providers.base import ProviderError
from .providers.chaoxing import text
from .service import service

PLATFORMS = {"chaoxing": "学习通", "rainClassroom": "雨课堂"}
SERVERS = {"yuketang": "雨课堂", "pro": "荷塘雨课堂", "changjiang": "长江雨课堂", "huanghe": "黄河雨课堂"}
TYPES = {
    2: ("签到", "fact_check"),
    74: ("签退", "logout"),
    54: ("定时签到", "schedule"),
    4: ("抢答", "front_hand"),
    5: ("主题讨论", "forum"),
    11: ("选人", "people"),
    14: ("问卷", "assignment"),
    19: ("作业", "assignment_turned_in"),
    23: ("评分", "star_outline"),
    42: ("随堂练习", "edit_note"),
    43: ("投票", "poll"),
}
SIGN_TYPES = {"0": "普通签到", "2": "二维码签到", "3": "手势签到", "4": "位置签到", "5": "签到码签到"}


def rich(content):
    return ui.html(str(content or ""), sanitize=True).classes("question-content w-full")


def field(label, value="", password=False):
    return (
        ui.input(label, value=value, password=password, password_toggle_button=password)
        .props("outlined dense")
        .classes("w-full")
    )


def primary(label, callback, icon=None):
    return ui.button(label, on_click=callback, icon=icon).classes("primary-btn")


async def guarded(operation):
    # Event callbacks can delete the button/slot which invoked them during a render.
    # Keep subsequent notifications and dialogs bound to the stable client root.
    client = ui.context.client
    with client:
        try:
            return await operation()
        except Exception as exc:
            ui.notify(str(exc), type="negative", timeout=9000, close_button=True)
            return None


def options_for(q, rain=False):
    options = q.get("options" if rain else "answer") or []
    result = {}
    for index, option in enumerate(options):
        key = str(option.get("key" if rain else "name", chr(65 + index)))
        result[key] = f"{key} · {text(option.get('value' if rain else 'content', ''))}"
    return result


class Dashboard:
    def __init__(self, section="courses", settings_error=""):
        self.platform = app.storage.user.get("platform", "chaoxing")
        self.server = app.storage.user.get("server", "yuketang")
        self.account_id = app.storage.user.get("account_id", "")
        self.course = None
        self.section = section if section in ("courses", "accounts", "groups", "history", "settings") else "courses"
        self.settings_error = settings_error
        self.query = ""
        self.only_ongoing = False
        self.live_task = None
        self.live_clock = None
        self.live_generation = 0
        self.live = {}
        self.busy = False
        self.im_messages = []
        self.captcha_lock = asyncio.Lock()
        self.pick_timers = []
        self.client = ui.context.client

    def account_options(self):
        return {a["id"]: f"{a['name']} · {a['uid']}" for a in service.accounts(self.platform, self.server)}

    def provider(self):
        return service.provider(self.account_id)

    def stop_live(self):
        self.live_generation += 1
        if self.live_clock:
            self.live_clock.cancel()
            self.live_clock = None
        for timer in self.pick_timers:
            timer.cancel()
        self.pick_timers.clear()
        if self.live_task:
            self.live_task.cancel()
            self.live_task = None

    def build(self):
        ui.colors(primary="#317868", secondary="#748a85", positive="#317868")
        ui.add_css((__import__("pathlib").Path(__file__).parent / "static/style.css").read_text())
        ui.add_head_html('<script src="/assets/im.js"></script>')
        self.client.on_disconnect(self.stop_live)
        ui.on("course-im-message", lambda e: self.handle_im(e.args))
        ui.on("course-im-status", lambda e: ui.notify(str(e.args.get("status", "状态更新"))))
        self.nav_buttons = {}
        with (
            ui.left_drawer(value=None, top_corner=True, bottom_corner=True)
            .classes("sidebar flex flex-col")
            .props("width=244 breakpoint=1000") as drawer
        ):
            with ui.row().classes("items-center gap-3 mb-5"):
                ui.icon("school", size="30px").classes("text-white")
                ui.label("课程助手").classes("brand")
            for key, title, icon in [
                ("courses", "我的课程", "grid_view"),
                ("accounts", "账号管理", "manage_accounts"),
                ("groups", "群聊与消息", "forum"),
                ("history", "操作记录", "history"),
                ("settings", "系统设置", "settings"),
            ]:
                self.nav_buttons[key] = (
                    ui.button(title, icon=icon, on_click=lambda k=key: self.navigate(k))
                    .props("flat no-caps color=white")
                    .classes("nav-button")
                )
        with ui.header().classes("topbar items-center gap-4"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense")
            ui.label("工作空间 / 课程管理").classes("text-sm muted breadcrumb")
            ui.space()
            csrf = escape(self.client.request.cookies.get(CSRF_COOKIE, ""), quote=True)
            ui.html(
                f'<form method="post" action="/admin/logout"><input type="hidden" name="csrf" value="{csrf}">'
                '<button type="submit" style="color:#317868;cursor:pointer">退出登录</button></form>',
                sanitize=False,
            )
            ui.select(PLATFORMS, value=self.platform, on_change=lambda e: self.change_platform(e.value)).props(
                "outlined dense options-dense"
            ).classes("w-28")
            self.server_select = (
                ui.select(SERVERS, value=self.server, on_change=lambda e: self.change_server(e.value))
                .props("outlined dense")
                .classes("w-36")
            )
            self.server_select.set_visibility(self.platform == "rainClassroom")
            options = self.account_options()
            if self.account_id not in options:
                self.account_id = next(iter(options), "")
            self.account_select = (
                ui.select(
                    options,
                    value=self.account_id or None,
                    label="当前账号",
                    on_change=lambda e: self.change_account(e.value),
                )
                .props("outlined dense")
                .classes("w-52 header-account")
            )
        self.body = ui.column().classes("w-full gap-6")
        self.render()

    def navigate(self, section):
        self.stop_live()
        self.section, self.course = section, None
        self.render()

    async def change_platform(self, value):
        self.stop_live()
        self.platform = value
        app.storage.user["platform"] = value
        self.server_select.set_visibility(value == "rainClassroom")
        await self.reset_accounts()

    async def change_server(self, value):
        self.stop_live()
        self.server = value
        app.storage.user["server"] = value
        await self.reset_accounts()

    async def reset_accounts(self):
        options = self.account_options()
        self.account_select.set_options(options, value=next(iter(options), None))
        await self.change_account(next(iter(options), ""))

    async def change_account(self, value):
        self.stop_live()
        self.account_id, self.course, self.section = value or "", None, "courses"
        app.storage.user["account_id"] = self.account_id
        await ui.run_javascript("window.courseIM?.close()")
        self.render()

    def heading(self, eyebrow, title, subtitle):
        with ui.column().classes("gap-1"):
            ui.label(eyebrow).classes("eyebrow")
            ui.label(title).classes("page-title")
            ui.label(subtitle).classes("muted text-sm")

    def render(self):
        for key, button in self.nav_buttons.items():
            button.classes(add="active" if key == self.section else "", remove="" if key == self.section else "active")
        self.body.clear()
        with self.body:
            if self.section == "courses":
                self.render_courses()
            elif self.section == "accounts":
                self.render_accounts()
            elif self.section == "groups":
                self.render_groups()
            elif self.section == "history":
                self.render_history()
            elif self.section == "settings":
                self.render_settings()

    def render_settings(self):
        self.heading("SYSTEM SETTINGS", "系统设置", "管理后台管理员账户与登录密码。")
        with service.store.connect() as db:
            account = db.execute("SELECT username FROM administrator WHERE id=1").fetchone()
        username = escape(account[0] if account else "", quote=True)
        csrf = escape(self.client.request.cookies.get(CSRF_COOKIE, ""), quote=True)
        with ui.column().classes("panel w-full max-w-xl gap-3"):
            ui.label("管理员账户").classes("section-title")
            ui.label("修改后，所有设备需要重新登录。").classes("muted text-sm")
            if self.settings_error:
                ui.label(self.settings_error).classes("text-red-700").props('role=alert')
            ui.html(
                f'''<form class="system-settings-form" method="post" action="/admin/account">
<input type="hidden" name="csrf" value="{csrf}">
<label>管理员名称<input name="username" value="{username}" autocomplete="username" required maxlength="64"></label>
<label>当前密码<input name="current_password" type="password" autocomplete="current-password" required maxlength="128"></label>
<label>新密码<input name="password" type="password" autocomplete="new-password" required minlength="8" maxlength="128"></label>
<label>确认密码<input name="confirmation" type="password" autocomplete="new-password" required minlength="8" maxlength="128"></label>
<button type="submit">保存并重新登录</button></form>''',
                sanitize=False,
            ).classes("w-full")

    def empty(self, icon, title, description, action=None):
        with ui.column().classes("panel empty"):
            ui.icon(icon, size="48px").classes("text-gray-300 mb-2")
            ui.label(title).classes("text-lg font-semibold")
            ui.label(description).classes("muted text-sm mb-3")
            if action:
                primary(action[0], action[1], "add")

    def render_courses(self):
        with ui.row().classes("w-full items-center justify-between"):
            self.heading("YOUR LEARNING SPACE", "我的课程", "将课程与课堂互动，放在一个清晰的工作空间。")
            with ui.row().classes("gap-2"):
                ui.button("扫码", icon="qr_code_scanner", on_click=lambda: self.scan_dialog()).props("outline")
                primary("刷新课程", lambda: guarded(self.refresh_courses), "refresh")
        courses = service.store.get("courses", self.account_id, []) if self.account_id else []
        with ui.row().classes("w-full gap-4"):
            for label, value, icon, hint in [
                ("已加入课程", len(courses), "auto_stories", PLATFORMS[self.platform]),
                ("当前账号", len(self.account_options()), "group", "多账号协同操作"),
                (
                    "课堂状态",
                    sum(bool(c.get("ongoing")) for c in courses) if self.platform != "chaoxing" else "—",
                    "sensors",
                    "手动刷新查看最新状态",
                ),
            ]:
                with ui.row().classes("panel stat items-center justify-between"):
                    with ui.column().classes("gap-1"):
                        ui.label(label).classes("muted text-xs")
                        ui.label(str(value)).classes("stats-value")
                        ui.label(hint).classes("muted text-xs")
                    ui.icon(icon, size="26px").classes("text-teal-700 bg-teal-50 p-3 rounded-xl")
        with ui.row().classes("w-full items-center gap-4"):
            ui.label("全部课程").classes("section-title")
            ui.space()
            if self.platform == "rainClassroom":
                ui.switch(
                    "仅看正在上课", value=self.only_ongoing, on_change=lambda e: self.filter_courses(ongoing=e.value)
                )
            ui.input(
                placeholder="搜索课程、教师…", value=self.query, on_change=lambda e: self.filter_courses(query=e.value)
            ).props("outlined dense clearable debounce=200").classes("w-64")
        self.course_grid = ui.element("div").classes("grid-courses")
        self.draw_courses(courses)

    def filter_courses(self, query=None, ongoing=None):
        if query is not None:
            self.query = query or ""
        if ongoing is not None:
            self.only_ongoing = ongoing
        self.draw_courses(service.store.get("courses", self.account_id, []))

    def draw_courses(self, courses):
        self.course_grid.clear()
        courses = [
            c
            for c in courses
            if self.query.lower() in (c["name"] + c.get("teacher", "")).lower()
            and (not self.only_ongoing or self.platform == "chaoxing" or c.get("ongoing"))
        ]
        colors = [("#e6f1ec", "#317868"), ("#e9eef9", "#526da7"), ("#f6eee5", "#aa8052"), ("#eeeaf5", "#8a72a5")]
        with self.course_grid:
            if not courses:
                with ui.column().classes("col-span-full w-full"):
                    self.empty(
                        "auto_stories",
                        "课程将在这里汇集",
                        "添加账号后点击“刷新课程”，查看你的课程与课堂活动。"
                        if not self.account_id
                        else "暂无匹配课程，请刷新或调整筛选条件。",
                        ("添加账号", self.login_dialog) if not self.account_id else None,
                    )
            for index, course in enumerate(courses):
                bg, fg = colors[index % len(colors)]
                with ui.column().classes("course-card gap-0"):
                    with (
                        ui.row()
                        .classes("course-art w-full items-center justify-between")
                        .style(f"background:{bg};color:{fg}")
                    ):
                        ui.icon(["menu_book", "architecture", "science", "language"][index % 4], size="32px")
                        ui.label("正在上课" if course.get("ongoing") else PLATFORMS[self.platform]).classes("pill")
                    with ui.column().classes("p-5 w-full gap-2"):
                        ui.label(course["name"]).classes("course-name")
                        ui.label(f"{course.get('teacher', '')} · {course.get('note', '')}").classes("muted text-xs")
                        with ui.row().classes("w-full items-center mt-3"):
                            ui.button("课程设置", icon="tune", on_click=lambda c=course: self.settings_dialog(c)).props(
                                "flat dense size=sm"
                            )
                            ui.space()
                            primary(
                                "进入课堂" if self.platform == "rainClassroom" else "查看活动",
                                lambda c=course: guarded(lambda: self.open_course(c)),
                                "arrow_forward",
                            )

    async def refresh_courses(self):
        if not self.account_id:
            return self.login_dialog()
        if self.busy:
            return
        self.busy = True
        try:
            await service.courses(self.account_id)
            self.render()
            ui.notify("课程已更新", type="positive")
        finally:
            self.busy = False

    def render_accounts(self):
        with ui.row().classes("w-full items-center justify-between"):
            self.heading("ACCOUNT CENTER", "账号管理", "账号按平台与服务器隔离，支持选择多个账号执行课堂操作。")
            primary("添加账号", self.login_dialog, "add")
        accounts = service.accounts()
        if not accounts:
            self.empty(
                "person_add",
                "添加你的第一个账号",
                "支持密码、短信、二维码和 Cookie 登录。",
                ("添加账号", self.login_dialog),
            )
        for account in accounts:
            with ui.row().classes("panel w-full items-center gap-4"):
                ui.avatar(icon="person", color="teal-1", text_color="teal-8")
                with ui.column().classes("gap-1"):
                    ui.label(account["name"]).classes("font-semibold")
                    ui.label(
                        f"{PLATFORMS[account['platform']]} · {SERVERS[account['server']] if account['platform'] != 'chaoxing' else account.get('school', '')} · UID {account['uid']}"
                    ).classes("muted text-xs")
                ui.space()
                ui.button(
                    "刷新资料", icon="refresh", on_click=lambda a=account: guarded(lambda: self.refresh_profile(a))
                ).props("flat")
                ui.button("移除", icon="delete_outline", on_click=lambda a=account: self.remove_dialog(a)).props(
                    "flat color=negative"
                )

    async def refresh_profile(self, account):
        p = service.provider(account["id"])
        p.account.update(await p.profile())
        p.save()
        self.render()
        ui.notify("账号资料已刷新", type="positive")

    def remove_dialog(self, account):
        with ui.dialog() as dialog, ui.card().classes("p-6"):
            ui.label(f"移除账号“{account['name']}”？").classes("text-lg font-semibold")
            ui.label("这将清除本地登录会话和课程缓存。").classes("muted")

            async def remove():
                await service.delete_account(account["id"])
                dialog.close()
                await self.reset_accounts()
                self.navigate("accounts")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button("移除", on_click=remove, color="negative")
        dialog.open()

    def login_dialog(self):
        provider = service.new_login(self.platform, self.server)
        with ui.dialog() as dialog, ui.card().classes("w-[520px] p-7 gap-4"):
            ui.label(f"添加{PLATFORMS[self.platform]}账号").classes("text-xl font-bold")
            ui.label(SERVERS[self.server] if self.platform != "chaoxing" else "账号密码不会保存在本地").classes(
                "muted text-xs"
            )
            mode = ui.toggle(
                {
                    "password": "密码",
                    "sms": "短信",
                    "qr": "二维码",
                    **({"wechat": "微信"} if self.platform != "chaoxing" else {}),
                    "cookie": "Cookie",
                },
                value="password",
            )
            region = ui.column().classes("w-full gap-3")

            async def finish(account):
                if account:
                    existing = service.providers.pop(account["id"], None)
                    if existing:
                        await existing.close()
                    service.providers[account["id"]] = provider
                    dialog.close()
                    self.account_id = account["id"]
                    app.storage.user["account_id"] = account["id"]
                    self.account_select.set_options(self.account_options(), value=account["id"])
                    self.navigate("accounts")
                    ui.notify("账号已添加", type="positive")

            async def captcha():
                return await self.tencent_captcha()

            def draw():
                region.clear()
                with region:
                    if mode.value in ("qr", "wechat"):
                        qr_box = ui.column().classes("w-full items-center")
                        state = {}

                        async def create():
                            state.clear()
                            state.update(
                                await (provider.wechat_start() if mode.value == "wechat" else provider.qr_start())
                            )
                            qr_box.clear()
                            with qr_box:
                                image = state.get("image", "")
                                if image:
                                    ui.image(image).classes("w-52 h-52")
                                else:
                                    ui.label("平台未返回二维码，请刷新")

                        async def check():
                            if not state:
                                raise ProviderError("请先生成二维码")
                            account = await (
                                provider.wechat_check(state) if mode.value == "wechat" else provider.qr_check(state)
                            )
                            if account:
                                await finish(account)
                            else:
                                ui.notify("尚未完成授权或二维码已超时，请扫码后重试")

                        ui.label("使用对应平台 APP 扫码，授权后点击“检查授权”。").classes("muted text-sm")
                        with ui.row():
                            primary("生成 / 刷新", lambda: guarded(create), "qr_code")
                            primary("检查授权", lambda: guarded(check), "check")
                    elif mode.value == "cookie":
                        cookie = (
                            ui.textarea("Cookie", placeholder="name=value; name2=value2")
                            .props("outlined")
                            .classes("w-full")
                        )

                        async def import_cookie():
                            provider.import_cookies(cookie.value or "")
                            await finish(await provider.finish_login())

                        primary("验证并导入", lambda: guarded(import_cookie), "login")
                    else:
                        username = field("手机号 / 邮箱" if self.platform != "chaoxing" else "手机号 / 账号")
                        password = field("短信验证码" if mode.value == "sms" else "密码", password=mode.value != "sms")
                        web = ui.checkbox("使用网页登录接口（推荐）", value=True) if mode.value == "password" else None
                        if mode.value == "sms":

                            async def send():
                                if not username.value:
                                    raise ProviderError("请先填写手机号")
                                args = await captcha() if self.platform != "chaoxing" else {}
                                if args is None:
                                    return
                                await provider.send_code(username.value, **args)
                                ui.notify("验证码已发送", type="positive")

                            ui.button("发送验证码", on_click=lambda: guarded(send)).props("outline")

                        async def login():
                            if not username.value or not password.value:
                                raise ProviderError("请填写账号和密码 / 验证码")
                            args = await captcha() if self.platform != "chaoxing" else {}
                            if args is None:
                                return
                            await finish(
                                await provider.login(
                                    username.value,
                                    password.value,
                                    mode="web" if web and web.value else mode.value,
                                    **args,
                                )
                            )

                        primary("登录并添加", lambda: guarded(login), "login")

            mode.on_value_change(lambda: draw())
            draw()
            ui.button("取消", on_click=dialog.close).props("flat").classes("self-end")

        async def cleanup():
            if provider not in service.providers.values():
                await provider.close()

        dialog.on("hide", cleanup)
        dialog.open()

    async def tencent_captcha(self):
        result = await ui.run_javascript(
            """return await new Promise(async resolve => {
            if (!window.TencentCaptcha) {
                const script=document.createElement('script'); script.src='https://turing.captcha.qcloud.com/TCaptcha.js';
                script.onerror=()=>resolve({error:'验证码组件加载失败，请检查网络'});
                document.head.appendChild(script); await new Promise(r=>script.onload=r);
            }
            new TencentCaptcha('2091064951', r=>resolve(r.ret===0?{ticket:r.ticket,rand:r.randstr}:null)).show();
        })""",
            timeout=180,
        )
        if result and result.get("error"):
            raise ProviderError(result["error"])
        return result

    def selected_accounts(self):
        return (
            ui.select(
                self.account_options(),
                value=[self.account_id] if self.account_id else [],
                multiple=True,
                label="操作账号",
            )
            .props("outlined dense use-chips")
            .classes("w-full")
        )

    async def batch(self, selected, operation, result_box):
        ids = list(selected.value or [])
        if not ids:
            raise ProviderError("请选择操作账号")
        if self.busy:
            ui.notify("上一项操作尚未完成")
            return
        self.busy = True
        try:
            results = await service.batch(ids, operation)
            result_box.clear()
            with result_box:
                for result in results:
                    ui.label(f"{result['account']}：" + ("成功" if result["ok"] else result["error"])).classes(
                        "text-green-700" if result["ok"] else "text-red-700"
                    )
            ui.notify(f"完成：{sum(r['ok'] for r in results)} / {len(results)} 成功")
            return results
        finally:
            self.busy = False

    def location_fields(self, value=None):
        value = value or {}
        classroom = field("教室（可选）", value.get("classroom", ""))
        address = field("地址", value.get("address", ""))
        with ui.row().classes("w-full no-wrap"):
            latitude = (
                ui.number(
                    "纬度",
                    value=float(value["latitude"]) if value.get("latitude") is not None else None,
                    min=-90,
                    max=90,
                    precision=6,
                )
                .props("outlined dense")
                .classes("w-1/2")
            )
            longitude = (
                ui.number(
                    "经度",
                    value=float(value["longitude"]) if value.get("longitude") is not None else None,
                    min=-180,
                    max=180,
                    precision=6,
                )
                .props("outlined dense")
                .classes("w-1/2")
            )

        async def locate():
            pos = await ui.run_javascript(
                """return await new Promise(resolve => navigator.geolocation.getCurrentPosition(
                p=>resolve({lat:p.coords.latitude,lng:p.coords.longitude}),e=>resolve({error:e.message}),{enableHighAccuracy:true,timeout:15000}))""",
                timeout=20,
            )
            if pos.get("error"):
                raise ProviderError(pos["error"])
            latitude.value, longitude.value = pos["lat"], pos["lng"]

        async def choose_map():
            with ui.dialog() as dialog, ui.card().classes("w-[700px] max-w-full p-5"):
                ui.label("点击地图选择位置").classes("section-title")
                center = (
                    latitude.value if latitude.value is not None else 31.23,
                    longitude.value if longitude.value is not None else 121.47,
                )
                map_view = ui.leaflet(center=center, zoom=15).classes("w-full h-96")
                marker = map_view.marker(latlng=center)
                coords = [*center]

                def clicked(e):
                    coords[:] = [e.args["latlng"]["lat"], e.args["latlng"]["lng"]]
                    marker.move(*coords)

                map_view.on("map-click", clicked)

                def apply():
                    latitude.value, longitude.value = coords
                    dialog.close()

                primary("使用此位置", apply, "check")
            dialog.open()

        with ui.row():
            ui.button("获取当前位置", icon="my_location", on_click=lambda: guarded(locate)).props("outline dense")
            ui.button("地图选点", icon="map", on_click=choose_map).props("flat dense")

        def collect():
            if latitude.value is None and longitude.value is None and not address.value:
                return None
            if latitude.value is None or longitude.value is None or not address.value:
                raise ProviderError("地址、纬度和经度需要完整填写")
            return dict(
                classroom=classroom.value, address=address.value, latitude=latitude.value, longitude=longitude.value
            )

        def apply_location(value):
            classroom.value = value.get("classroom", "")
            address.value = value.get("address", "")
            latitude.value, longitude.value = value.get("latitude"), value.get("longitude")

        collect.set = apply_location
        return collect

    def settings_dialog(self, course):
        settings = service.settings(self.account_id, course)
        images = list(settings.get("imageObjectIds") or [])
        with ui.dialog() as dialog, ui.card().classes("w-[600px] p-7 gap-4"):
            ui.label("课程设置").classes("text-xl font-bold")
            ui.label(course["name"]).classes("muted text-sm")
            location = self.location_fields(settings.get("location"))
            ui.separator()
            ui.label("签到照片").classes("section-title")
            image_box = ui.row().classes("w-full")

            def show_images():
                image_box.clear()
                with image_box:
                    for image in list(images):
                        with ui.column().classes("gap-1"):
                            url = (
                                image
                                if image.startswith("https:")
                                else f"https://p.ananas.chaoxing.com/star4/{image}/origin.jpg"
                            )
                            ui.image(url).classes("w-20 h-20 rounded-lg")

                            def remove(value=image):
                                images.remove(value)
                                show_images()

                            ui.button("移除", on_click=remove).props("flat dense size=sm")

            show_images()

            async def upload(e):
                async def perform():
                    content = await e.file.read()
                    if len(content) > 20 * 1024 * 1024:
                        raise ProviderError("图片请控制在 20 MB 以内")
                    images.append(await self.provider().upload(content, e.file.name))
                    show_images()

                await guarded(perform)

            ui.upload(label="拍照 / 上传图片", on_upload=upload, auto_upload=True).props(
                "accept=image/* max-file-size=20971520"
            ).classes("w-full")

            def save():
                try:
                    service.save_settings(self.account_id, course, {"location": location(), "imageObjectIds": images})
                    dialog.close()
                    ui.notify("设置已保存", type="positive")
                except Exception as exc:
                    ui.notify(str(exc), type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat")
                primary("保存设置", save, "save")
        dialog.open()

    async def open_course(self, course):
        if self.platform == "rainClassroom":
            if not course.get("lessonId"):
                raise ProviderError("该课程当前没有正在进行的课堂，请刷新课程列表")
            return await self.open_live(course)
        generation = self.live_generation
        activities = await self.provider().activities(course)
        if generation != self.live_generation:
            return
        self.course = course
        self.body.clear()
        with self.body:
            ui.button("返回课程", icon="arrow_back", on_click=lambda: self.navigate("courses")).props("flat")
            with ui.row().classes("w-full items-center justify-between"):
                self.heading(
                    "CLASSROOM ACTIVITIES", course["name"], f"{course.get('teacher', '')} · {course.get('note', '')}"
                )
                with ui.row():
                    ui.button(
                        "课程作业", icon="assignment", on_click=lambda: guarded(lambda: self.homework_dialog(course))
                    ).props("outline")
                    ui.button("课程设置", icon="tune", on_click=lambda: self.settings_dialog(course)).props("outline")
                    primary("刷新活动", lambda: guarded(lambda: self.open_course(course)), "refresh")
            ongoing = ui.switch("仅看进行中", value=False)
            counts = {key: sum(a["type"] == key for a in activities) for key in TYPES}
            category = (
                ui.select(
                    {
                        0: f"全部活动（{len(activities)}）",
                        **{key: f"{label}（{counts[key]}）" for key, (label, _) in TYPES.items()},
                    },
                    value=0,
                    label="活动功能",
                )
                .props("outlined dense")
                .classes("w-full")
            )
            rows = ui.column().classes("w-full gap-3")

            def draw():
                rows.clear()
                visible = [
                    a
                    for a in activities
                    if (not ongoing.value or a["ongoing"]) and (not category.value or a["type"] == category.value)
                ]
                with rows:
                    if not visible:
                        self.empty("event_available", "暂无课堂活动", "可以稍后点击刷新，检查老师发布的新活动。")
                    for active in visible:
                        kind, icon = TYPES.get(active["type"], (f"活动 {active['type']}", "open_in_new"))
                        if active["type"] in (2, 74, 54):
                            kind = SIGN_TYPES.get(active["signType"], "签到")
                        with ui.row().classes("activity-row"):
                            ui.icon(icon, size="24px").classes("text-teal-700 bg-teal-50 p-3 rounded-xl")
                            with ui.column().classes("gap-1 grow"):
                                ui.label(active["name"] or kind).classes("font-semibold")
                                stamp = active.get("startTime", 0)
                                time = datetime.fromtimestamp(stamp / 1000).strftime("%m-%d %H:%M") if stamp else ""
                                ui.label(f"{kind} · {time} · {text(active.get('description', ''))}").classes(
                                    "muted text-xs"
                                )
                            ui.label("进行中" if active["ongoing"] else "已结束").classes("pill")
                            ui.button(
                                "查看",
                                icon="chevron_right",
                                on_click=lambda a=active: guarded(lambda: self.open_activity(course, a)),
                            ).props("flat")

            ongoing.on_value_change(draw)
            category.on_value_change(draw)
            draw()

    async def open_activity(self, course, active, group=False):
        typ = active["type"]
        if typ in (2, 74, 54):
            return await self.sign_dialog(course, active, group)
        if typ in (42, 43, 14):
            return await self.questions_dialog(course, active)
        if typ == 23:
            return await self.evaluate_dialog(course, active)
        if typ == 5:
            return await self.topic_dialog(active)
        if typ == 11:
            return await self.pick_dialog(active)
        if typ == 19:
            return await self.homework_dialog(course)
        if active.get("url"):
            ui.navigate.to(active["url"], new_tab=True)
        else:
            raise ProviderError("原应用未提供该活动的专用操作，请在平台 APP 中查看")

    async def homework_dialog(self, course):
        provider = self.provider()
        account_id = self.account_id
        generation = self.live_generation
        works = await provider.homework_list(course)
        if generation != self.live_generation:
            return
        with ui.dialog() as dialog, ui.card().classes("w-[850px] p-7 gap-4"):
            ui.label("课程作业").classes("text-xl font-bold")
            if not works:
                ui.label("该课程暂无作业").classes("muted")
            panels = []
            for work in works:
                with ui.row().classes("activity-row w-full items-center") as panel:
                    panels.append(panel)
                    ui.label(work["name"]).classes("grow font-semibold")
                    ui.label(work["status"]).classes("pill")

                    async def show(w=work):
                        detail = await provider.homework_review(w)
                        with ui.dialog() as reader, ui.card().classes("w-[950px] p-7 gap-5"):
                            ui.label(w["name"]).classes("text-xl font-bold")
                            question_panels = []
                            for question in detail["questions"]:
                                with ui.column().classes("w-full") as question_panel:
                                    rich(question)
                                    question_panels.append(question_panel)
                            self.paginate_panels(question_panels, 5, "作答记录")
                            ui.button("关闭详情", on_click=reader.close).props("flat")
                        reader.open()

                    if work["status"] in ("已完成", "待批阅", "已提交"):
                        ui.button("作答记录", on_click=lambda w=work: guarded(lambda: show(w))).props("flat")
                    else:
                        ui.button(
                            "作答", on_click=lambda w=work: self.homework_start_dialog(provider, account_id, w)
                        ).props("outline")
            self.paginate_panels(panels, 10, "作业列表")
            ui.label("作业按当前账号独立作答；参考答案和解析以老师开放范围为准。").classes("muted text-sm")
            ui.button("关闭", on_click=dialog.close).props("flat")
        dialog.open()

    def paginate_panels(self, panels, size, label):
        if not panels:
            return
        total = (len(panels) + size - 1) // size
        with ui.row().classes("w-full items-center justify-center"):
            caption = ui.label().classes("muted text-xs")
            pager = ui.pagination(1, total, value=1, direction_links=True).props("boundary-links")
            pager.set_visibility(total > 1)

            def draw():
                index = int(pager.value or 1) - 1
                caption.text = f"{label} · 第 {index + 1}/{total} 页 · 共 {len(panels)} 项"
                for i, panel in enumerate(panels):
                    panel.set_visibility(index * size <= i < (index + 1) * size)

            pager.on_value_change(draw)
            draw()

    def homework_start_dialog(self, provider, account_id, work):
        with ui.dialog() as confirmation, ui.card().classes("w-[550px] p-7 gap-4"):
            ui.label(work["name"]).classes("section-title")
            ui.label("进入作答可能启动平台的限时作业计时。请准备好后开始。")

            async def start():
                if self.account_id != account_id:
                    raise ProviderError("账号已切换，请重新打开作业")
                questions = await provider.homework_open(work)
                confirmation.close()
                self.homework_answer_dialog(provider, account_id, work, questions)

            ui.button("取消", on_click=confirmation.close).props("flat")
            primary("开始作答", lambda: guarded(start), "edit_note")
        confirmation.open()

    def homework_answer_dialog(self, provider, account_id, work, questions):
        key = f"{account_id}:{work['id']}"
        draft = service.store.get("homework-drafts", key, {})
        with ui.dialog() as dialog, ui.card().classes("w-[900px] p-7 gap-4"):
            ui.label(work["name"]).classes("text-xl font-bold")
            account = service.store.get("accounts", account_id)
            selected = (
                ui.select({account_id: account["name"]}, value=[account_id], multiple=True, label="操作账号")
                .props("outlined")
                .classes("w-full")
                .disable()
            )
            widgets = {}
            file_widgets, keep_widgets, panels = {}, {}, []
            stored_files = provider.homework_files(work["id"])
            for i, question in enumerate(questions):
                qid, typ = question["id"], question["type"]
                value = draft.get(qid, question.get("initial"))
                with ui.column().classes("panel w-full gap-3") as panel:
                    panels.append(panel)
                    ui.label(f"第 {i + 1} 题").classes("font-semibold")
                    rich(question["content"])
                    if not question["supported"]:
                        ui.label("此题型暂不支持，不能在本应用提交此作业。").classes("text-red-700")
                    elif typ in (0, 1, 3):
                        widgets[qid] = (
                            ui.select(question["options"], multiple=True, value=value or [], label="选择答案")
                            .props("outlined use-chips")
                            .classes("w-full")
                            if typ == 1
                            else ui.radio(question["options"], value=value)
                        )
                    elif typ == 2 or question.get("slots"):
                        values = value or []
                        widgets[qid] = [
                            field(f"第 {n + 1} 空", values[n] if n < len(values) else "")
                            for n in range(len(question["editors"]))
                        ]
                    elif typ in (11, 13):
                        values = value or []
                        widgets[qid] = [
                            ui.select(
                                control["options"],
                                value=(values[n] or None) if n < len(values) else None,
                                label=control["label"],
                            )
                            .props("outlined")
                            .classes("w-full")
                            for n, control in enumerate(question["dropdowns"])
                        ]
                    else:
                        widgets[qid] = (
                            ui.textarea("填写作业答案", value=value or "").props("outlined").classes("w-full")
                        )
                        if question.get("existing_media"):
                            with ui.expansion("平台已有的图片 / 附件").classes("w-full"):
                                for media in question["existing_media"]:
                                    rich(media)
                            keep_widgets[qid] = ui.checkbox(
                                "保留平台已有的图片 / 附件", value=(draft.get("_keep_existing") or {}).get(qid, True)
                            )
                        choices = {f["object_id"]: f["name"] for f in stored_files.get(qid, [])}
                        file_widgets[qid] = (
                            ui.select(
                                choices,
                                value=(draft.get("_attachments") or {}).get(qid, []),
                                multiple=True,
                                label="答题附件",
                            )
                            .props("outlined use-chips")
                            .classes("w-full")
                        )

                        async def upload(e, question_id=qid, control=file_widgets[qid]):
                            async def perform():
                                if self.account_id != account_id:
                                    raise ProviderError("账号已切换，请重新打开作业")
                                file = await provider.homework_upload(
                                    work["id"], question_id, await e.file.read(), e.file.name
                                )
                                if control.is_deleted:
                                    return
                                options = dict(control.options)
                                options[file["object_id"]] = file["name"]
                                control.set_options(
                                    options, value=list(dict.fromkeys([*(control.value or []), file["object_id"]]))
                                )
                                ui.notify("附件已上传并加入本题，可从附件选择框移除")

                            await guarded(perform)

                        ui.upload(label="上传图片 / 文件附件", on_upload=upload, auto_upload=True).props(
                            "max-file-size=52428800"
                        ).classes("w-full")
            self.paginate_panels(panels, 5, "作业题目")
            results = ui.column().classes("w-full")

            def answers():
                return {
                    qid: [w.value or "" for w in widget] if isinstance(widget, list) else widget.value
                    for qid, widget in widgets.items()
                } | {
                    "_attachments": {qid: control.value or [] for qid, control in file_widgets.items()},
                    "_keep_existing": {qid: control.value for qid, control in keep_widgets.items()},
                }

            def save():
                service.store.put("homework-drafts", key, answers())
                ui.notify("已在本地加密保存草稿；尚未提交平台")

            async def submit():
                if self.account_id != account_id:
                    raise ProviderError("账号已切换，请重新打开作业")
                data = answers()
                service.store.put("homework-drafts", key, data)
                result = await self.batch(selected, lambda p: p.homework_submit(work["id"], data), results)
                if result and all(r["ok"] for r in result):
                    service.store.delete("homework-drafts", key)
                    submit_button.disable()

            with ui.row():
                ui.button("关闭作答", on_click=dialog.close).props("flat")
                ui.button("保存本地草稿", on_click=save, icon="save").props("outline")
                submit_button = primary("提交作业", lambda: guarded(submit), "send")
                if any(not q["supported"] for q in questions):
                    submit_button.disable()
        dialog.open()

    async def sign_dialog(self, course, active, group=False):
        detail = await self.provider().sign_detail(active["id"], group)
        info = detail.get("data", detail)
        info = dict(info) if isinstance(info, dict) else {}
        if not group:
            info.update(await self.provider().active_info(active["id"]))
        settings = service.settings(self.account_id, course)
        with ui.dialog() as dialog, ui.card().classes("w-[680px] p-7 gap-4"):
            ui.label(active.get("name") or "签到").classes("text-xl font-bold")
            ui.label("提交后将显示每个账号的结果；验证码和人脸参数按账号分别处理。").classes("muted text-xs")
            selected = self.selected_accounts()
            kind = (
                ui.select(SIGN_TYPES, value=active.get("signType", "0"), label="签到方式")
                .props("outlined dense")
                .classes("w-full")
            )
            code = field("签到码 / 手势序列（九宫格 1–9）")
            with ui.expansion("绘制九宫格手势", icon="gesture").classes("w-full"):
                points = []
                pattern_label = ui.label("尚未选择")

                def add_point(value):
                    if value not in points:
                        points.append(value)
                        code.value = "".join(points)
                        pattern_label.text = " → ".join(points)

                with ui.grid(columns=3).classes("gap-4 mx-auto"):
                    for number in range(1, 10):
                        ui.button(str(number), on_click=lambda n=str(number): add_point(n)).props("round outline")

                def clear_pattern():
                    points.clear()
                    code.value = ""
                    pattern_label.text = "尚未选择"

                ui.button("重置", on_click=clear_pattern).props("flat dense")
            qr_value = field("二维码内容 / enc", active.get("_qr", ""))
            ui.button(
                "相机扫码",
                icon="qr_code_scanner",
                on_click=lambda: self.camera_dialog(lambda value: setattr(qr_value, "value", value)),
            ).props("flat dense")
            photo = (
                ui.select(
                    settings.get("imageObjectIds") or [],
                    label="照片 Object ID（可选）",
                    with_input=True,
                    new_value_mode="add-unique",
                )
                .props("outlined dense")
                .classes("w-full")
            )
            per_account_photos = {}
            with ui.expansion("分别设置各账号的照片", icon="collections").classes("w-full"):
                for account_id, label in self.account_options().items():
                    per_account_photos[account_id] = (
                        ui.select(
                            settings.get("imageObjectIds") or [],
                            label=label,
                            with_input=True,
                            new_value_mode="add-unique",
                        )
                        .props("outlined dense")
                        .classes("w-full")
                    )

            async def upload(e):
                async def work():
                    oid = await self.provider().upload(await e.file.read(), e.file.name)
                    photo.set_options([*photo.options, oid], value=oid)

                await guarded(work)

            ui.upload(label="拍照 / 选择签到照片", on_upload=upload, auto_upload=True).props(
                "accept=image/* max-file-size=20971520"
            ).classes("w-full")
            with ui.expansion("位置信息", icon="location_on", value=kind.value == "4").classes("w-full"):
                collect_location = self.location_fields(settings.get("location"))
            use_face = ui.checkbox("使用账号已采集的人脸进行校验", value=info.get("openCheckFaceFlag") == 1)
            with ui.expansion("签到详情 / 参与列表").classes("w-full"):
                ui.label(
                    "签到方式：" + SIGN_TYPES.get(str(info.get("otherId", active.get("signType", "0"))), "由平台指定")
                )
                ui.label("需要照片：" + ("是" if info.get("ifPhoto", info.get("ifphoto")) == 1 else "否 / 未指定"))
                for key, label in [
                    ("address", "指定地址"),
                    ("locationText", "位置要求"),
                    ("locationRange", "签到范围"),
                ]:
                    if info.get(key):
                        ui.label(f"{label}：{info[key]}")

                async def attendees():
                    response = await self.provider().attendees(active["id"], group)
                    data = response.get("data") or response
                    if not isinstance(data, dict):
                        raise ProviderError("平台未返回参与列表")
                    signed = data.get("yiqianList") or data.get("signed") or []
                    unsigned = data.get("weiqianList") or data.get("unsigned") or []
                    with ui.dialog() as participants, ui.card().classes("w-[760px] p-6 gap-3"):
                        ui.label(f"参与列表 · 已签到 {len(signed)} 人 · 未签到 {len(unsigned)} 人").classes(
                            "section-title"
                        )
                        for person in signed:
                            with ui.row().classes("activity-row"):
                                ui.label(person.get("name") or str(person.get("uid", ""))).classes("grow")
                                ui.label(str(person.get("title") or "已签到")).classes("muted text-xs")
                                if person.get("title"):

                                    def apply(value=person):
                                        if value.get("longitude") and value.get("latitude"):
                                            collect_location.set(
                                                {
                                                    "address": value["title"],
                                                    "latitude": value["latitude"],
                                                    "longitude": value["longitude"],
                                                }
                                            )
                                        else:
                                            photo.set_options(
                                                list(dict.fromkeys([*photo.options, value["title"]])),
                                                value=value["title"],
                                            )
                                        participants.close()

                                    ui.button("填入签到信息", on_click=apply).props("flat dense")
                        if unsigned:
                            ui.label(
                                "未签到：" + "、".join(p.get("name") or str(p.get("uid", "")) for p in unsigned)
                            ).classes("muted")
                        if not signed and not unsigned:
                            ui.label("平台未提供可显示的参与者信息。").classes("muted")
                        ui.button("关闭参与列表", on_click=participants.close)
                    participants.open()

                ui.button("查看参与列表", on_click=lambda: guarded(attendees)).props("outline")
            results = ui.column().classes("w-full")

            async def submit():
                loc = collect_location() or {}
                raw = qr_value.value or ""
                query = parse_qs(urlparse(raw).query)
                enc = query.get("enc", [raw])[0]

                async def operation(provider):
                    face = await provider.face(active["id"]) if use_face.value else {}
                    validation = ""
                    if info.get("showVCode") == 1:
                        async with self.captcha_lock:
                            validation = await self.slide_captcha(provider, active.get("url", ""))
                        if not validation:
                            raise ProviderError("已取消验证码验证")
                    params = dict(
                        code=code.value or "",
                        enc=enc,
                        object_id=per_account_photos.get(provider.account.get("id"), photo).value or photo.value or "",
                        address=loc.get("address", ""),
                        latitude=loc.get("latitude"),
                        longitude=loc.get("longitude"),
                        group=group,
                        **face,
                    )
                    if kind.value == "0" and info.get("ifPhoto", info.get("ifphoto")) == 1 and not params["object_id"]:
                        raise ProviderError("本次签到需要照片，请先上传或选择照片")
                    try:
                        return await provider.sign(course, active["id"], kind.value, **params, validate=validation)
                    except ProviderError as exc:
                        message = str(exc)
                        if message.startswith("validate"):
                            async with self.captcha_lock:
                                validation = await self.slide_captcha(provider, active.get("url", ""))
                            if not validation:
                                raise ProviderError("已取消验证码验证")
                            return await provider.sign(
                                course,
                                active["id"],
                                kind.value,
                                **params,
                                validate=validation,
                                enc2=message.removeprefix("validate_") if "_" in message else "",
                            )
                        raise

                await self.batch(selected, operation, results)

            with ui.row().classes("w-full justify-end"):
                ui.button("关闭", on_click=dialog.close).props("flat")
                primary("提交签到", lambda: guarded(submit), "check_circle")
        dialog.open()

    async def slide_captcha(self, provider, referer):
        data = await provider.captcha_start(referer)
        image = data["imageVerificationVo"]
        with ui.dialog() as dialog, ui.card().classes("p-6"):
            ui.label("拖动滑块完成验证").classes("section-title")
            with ui.element("div").style("width:320px;height:160px;position:relative"):
                ui.image(image["shadeImage"]).style("position:absolute;width:320px;height:160px")
                piece = ui.image(image["cutoutImage"]).style("position:absolute;width:56px;height:160px;left:0")
            slider = ui.slider(min=0, max=264, value=0).classes("w-80")
            slider.on_value_change(lambda e: piece.style(f"left:{e.value}px"))

            async def verify():
                x = (slider.value * 280 / 264 - 8) * (image.get("originalWidth", 320) / 320)
                value = await provider.captcha_submit(x, data["token"], referer)
                dialog.submit(value)

            with ui.row():
                ui.button("取消", on_click=lambda: dialog.submit(None)).props("flat")
                primary("验证", lambda: guarded(verify), "check")
        return await dialog

    async def questions_dialog(self, course, active):
        data = await self.provider().questions(active)
        questions = data.get("questionlist") or []
        if not questions:
            raise ProviderError("平台未返回题目")
        with ui.dialog() as dialog, ui.card().classes("w-[820px] p-7 gap-4"):
            ui.label(active.get("name") or TYPES[active["type"]][0]).classes("text-xl font-bold")
            selected = self.selected_accounts()
            already = bool(data.get("pptUserAttend")) or any(q.get("studentAnswer") is not None for q in questions)
            if already:
                selected.value = []
                ui.label("当前账号已参与，请选择仍需提交的账号。").classes("text-amber-700")
            widgets = {}
            for index, question in enumerate(questions):
                qid, typ = str(question["id"]), int(question.get("type", 0))
                with ui.column().classes("panel w-full gap-3"):
                    ui.label(f"第 {index + 1} 题" + (" · 必答" if question.get("ismust") == 1 else "")).classes(
                        "font-semibold"
                    )
                    rich(question.get("content") or question.get("name") or question.get("title", ""))
                    opts = options_for(question)
                    if opts and typ in (0, 1, 3, 16):
                        if typ == 1:
                            widgets[qid] = (
                                ui.select(opts, multiple=True, value=[], label="选择答案")
                                .props("outlined use-chips")
                                .classes("w-full")
                            )
                        else:
                            widgets[qid] = ui.radio(opts).classes("w-full")
                        for opt in question.get("answer") or []:
                            if "<img" in str(opt.get("content", "")):
                                rich(f"<b>{opt.get('name', '')}</b> {opt.get('content', '')}")
                    elif typ in (2, 9, 10):
                        widgets[qid] = [
                            field(f"第 {i + 1} 空") for i in range(max(1, len(question.get("answer") or [])))
                        ]
                    else:
                        widgets[qid] = ui.textarea("填写答案").props("outlined").classes("w-full")
                        question["_images"] = []

                        async def upload(e, q=question):
                            async def work():
                                oid = await self.provider().upload(await e.file.read(), e.file.name)
                                q["_images"].append(
                                    {
                                        "name": e.file.name,
                                        "objectid": oid,
                                        "suffix": e.file.name.rsplit(".", 1)[-1],
                                        "type": "1",
                                        "preview": "",
                                        "thumbnail": "",
                                    }
                                )
                                ui.notify("答题图片已上传")

                            await guarded(work)

                        ui.upload(label="上传答题图片", on_upload=upload, auto_upload=True).props(
                            "accept=image/* max-file-size=20971520"
                        ).classes("w-full")

            def fill_reference(only_empty=False):
                references = reference_answers(questions)
                for qid, value in references.items():
                    widget = widgets[qid]
                    if isinstance(widget, list):
                        for w, v in zip(widget, value):
                            if not only_empty or not w.value:
                                w.value = v
                    elif not only_empty or not widget.value:
                        widget.value = value

            if active["type"] == 42:
                ui.button("填入平台参考答案", on_click=fill_reference, icon="auto_fix_high").props("outline")
            results = ui.column().classes("w-full")

            async def submit():
                answers = {
                    qid: [w.value or "" for w in widget] if isinstance(widget, list) else widget.value
                    for qid, widget in widgets.items()
                }
                validate_answers(active, questions, answers)
                await self.batch(selected, lambda p: p.submit_questions(course, active, questions, answers), results)

            with ui.row().classes("w-full justify-end"):
                ui.button("关闭", on_click=dialog.close).props("flat")
                if active["type"] == 42:

                    async def auto_submit():
                        fill_reference(only_empty=True)
                        await submit()

                    primary("填入参考答案并提交", lambda: guarded(auto_submit), "auto_fix_high")
                primary("提交答案", lambda: guarded(submit), "send")
        dialog.open()

    async def evaluate_dialog(self, course, active):
        detail = await self.provider().active_info(active["id"])
        with ui.dialog() as dialog, ui.card().classes("w-[600px] p-7 gap-4"):
            ui.label("课堂评分").classes("text-xl font-bold")
            selected = self.selected_accounts()
            norms = detail.get("normList") or []
            scores = [
                ui.number(
                    n.get("content") or n.get("name") or n.get("normName") or f"评分项 {i + 1}",
                    min=0,
                    max=float(n.get("score") or 100),
                    precision=0,
                )
                .props("outlined")
                .classes("w-full")
                for i, n in enumerate(norms)
            ]
            total = (
                ui.number("总分", min=0, max=float(detail.get("score") or 100), precision=0)
                .props("outlined")
                .classes("w-full")
            )
            comment = ui.textarea("评语（可选）").props("outlined").classes("w-full")
            results = ui.column()

            async def submit():
                if scores and any(s.value is None for s in scores):
                    raise ProviderError("请填写所有分项评分")
                value = sum(int(s.value) for s in scores) if scores else total.value
                if value is None:
                    raise ProviderError("请填写评分")
                await self.batch(
                    selected,
                    lambda p: p.evaluate(
                        course, active["id"], int(value), comment.value or "", [int(s.value) for s in scores]
                    ),
                    results,
                )

            with ui.row().classes("w-full justify-end"):
                ui.button("关闭", on_click=dialog.close).props("flat")
                primary("提交评分", lambda: guarded(submit), "star")
        dialog.open()

    async def topic_dialog(self, active):
        data = await self.provider().topic(topic_id(active))
        with ui.dialog() as dialog, ui.card().classes("w-[700px] p-7 gap-4"):
            ui.label(data.get("title") or active.get("name") or "主题讨论").classes("text-xl font-bold")
            rich(data.get("content", ""))
            selected = self.selected_accounts()
            for reply in data.get("replies", []):
                with ui.column().classes("panel w-full gap-1"):
                    ui.label((reply.get("creator") or {}).get("name") or reply.get("name") or "参与者").classes(
                        "text-xs muted"
                    )
                    rich(reply.get("content", ""))
            content = ui.textarea("发表回复").props("outlined").classes("w-full")
            anonymous = ui.checkbox("匿名回复")
            anonymous.set_visibility(data.get("userAuth", {}).get("operationAuth", {}).get("canAnonymousAddReply") == 1)
            results = ui.column()

            async def submit():
                if not content.value or not content.value.strip():
                    raise ProviderError("请填写回复内容")
                await self.batch(selected, lambda p: p.reply(data["uuid"], content.value, anonymous.value), results)

            with ui.row().classes("w-full justify-end"):
                ui.button("关闭", on_click=dialog.close).props("flat")
                primary("发表回复", lambda: guarded(submit), "send")
        dialog.open()

    async def browser_notification(self, title, body):
        await ui.run_javascript(
            "if ('Notification' in window && Notification.permission === 'granted') "
            + f"new Notification({json.dumps(title)}, {{body:{json.dumps(body)}}});"
        )

    async def pick_dialog(self, active):
        provider = self.provider()
        generation = self.live_generation
        tracker = PickUpdates(a["uid"] for a in service.accounts(self.platform, self.server))
        data = await provider.pick(active["id"])
        tracker.update(data.get("userListChoose", []))
        busy = False
        with ui.dialog() as dialog, ui.card().classes("w-[550px] p-7"):
            ui.label("选人结果").classes("text-xl font-bold")
            ui.label("窗口打开期间每 3 秒检查一次；关闭窗口即停止。").classes("muted")
            status = ui.label().classes("text-xs muted")
            users_box = ui.column().classes("w-full")

            def draw(users):
                users_box.clear()
                with users_box:
                    if not users:
                        ui.label("尚未选人").classes("muted")
                    for user in users:
                        uid = str(user.get("userId", ""))
                        selected = uid in tracker.accounts
                        ui.label(
                            f"{user.get('name', uid)} · 分数 {user.get('userScore', '—')}"
                            + (" · 你的账号已被选中" if selected else "")
                        ).classes("text-teal-700" if selected else "")

            async def refresh():
                nonlocal busy
                if busy or generation != self.live_generation or not dialog.value:
                    return
                busy = True
                try:
                    latest = await provider.pick(active["id"])
                    if generation != self.live_generation or not dialog.value:
                        return
                    users = latest.get("userListChoose", [])
                    picked = tracker.update(users)
                    draw(users)
                    status.text = "更新于 " + datetime.now().strftime("%H:%M:%S")
                    if picked:
                        names = "、".join(str(u.get("name") or u["userId"]) for u in picked)
                        ui.notify("你的账号被选中：" + names, type="warning", timeout=0, close_button=True)
                        await self.browser_notification("选人活动", "你的账号被选中：" + names)
                except Exception as exc:
                    status.text = "更新失败：" + str(exc)
                finally:
                    busy = False

            draw(data.get("userListChoose", []))
            with ui.row():
                ui.button("关闭", on_click=dialog.close).props("flat")
                ui.button(
                    "启用桌面通知",
                    on_click=lambda: ui.run_javascript(
                        "'Notification' in window ? Notification.requestPermission() : 'unsupported'"
                    ),
                ).props("outline")
                primary("刷新结果", refresh, "refresh")
            timer = ui.timer(3, refresh)
            self.pick_timers.append(timer)

            def stop():
                timer.cancel()
                if timer in self.pick_timers:
                    self.pick_timers.remove(timer)

            dialog.on("hide", stop)
        dialog.open()

    def camera_dialog(self, callback):
        import base64

        import zxingcpp
        from PIL import Image

        with ui.dialog() as dialog, ui.card().classes("w-[520px] p-6"):
            ui.label("相机扫码").classes("section-title")
            ui.html(
                '<video id="course-camera" autoplay playsinline style="width:100%;border-radius:12px"></video>',
                sanitize=False,
            )
            status = ui.label("启动后请将二维码对准镜头；也可以上传二维码照片。").classes("muted")
            running = {"value": False}

            def decode(data):
                image = Image.open(BytesIO(data))
                result = zxingcpp.read_barcode(image)
                return result.text if result else ""

            async def uploaded(e):
                async def work():
                    result = decode(await e.file.read())
                    if not result:
                        raise ProviderError("图片中未识别到二维码，请使用清晰原图")
                    callback(result)
                    dialog.close()

                await guarded(work)

            ui.upload(label="上传二维码图片 / 拍照", on_upload=uploaded, auto_upload=True).props(
                "accept=image/* max-file-size=20971520"
            ).classes("w-full")

            async def capture():
                if not running["value"]:
                    return
                frame = await ui.run_javascript("""return (()=>{
                    const v=document.getElementById('course-camera');
                    if(!v?.videoWidth)return null;
                    const c=document.createElement('canvas');c.width=v.videoWidth;c.height=v.videoHeight;
                    c.getContext('2d').drawImage(v,0,0);return c.toDataURL('image/jpeg',.8).split(',')[1];})()""")
                if frame:
                    result = decode(base64.b64decode(frame))
                    if result:
                        running["value"] = False
                        callback(result)
                        dialog.close()

            timer = ui.timer(0.6, lambda: guarded(capture), active=False)

            async def start():
                result = await ui.run_javascript(
                    """return await (async()=>{
                    try{
                        window.courseCameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});
                        const video=document.getElementById('course-camera');video.srcObject=window.courseCameraStream;
                        await video.play();return {ok:true};
                    }catch(e){return {error:e.message}}})()""",
                    timeout=20,
                )
                if result.get("error"):
                    status.text = result["error"]
                else:
                    running["value"] = True
                    timer.activate()

            async def stop():
                running["value"] = False
                timer.deactivate()
                await ui.run_javascript("window.courseCameraStream?.getTracks().forEach(t=>t.stop())")

            dialog.on("hide", stop)
            primary("启动相机", lambda: guarded(start), "videocam")
            ui.button("关闭", on_click=dialog.close).props("flat")
        dialog.open()

    def scan_dialog(self):
        with ui.dialog() as dialog, ui.card().classes("w-[560px] p-6 gap-4"):
            ui.label("扫描课堂二维码").classes("text-xl font-bold")
            selected = self.selected_accounts()
            value = field("粘贴二维码内容 / URL")
            ui.button(
                "使用相机",
                icon="qr_code_scanner",
                on_click=lambda: self.camera_dialog(lambda v: setattr(value, "value", v)),
            ).props("outline")
            results = ui.column()

            async def submit():
                if not value.value:
                    raise ProviderError("请扫描或填写二维码内容")
                if self.platform == "rainClassroom":
                    await self.batch(selected, lambda p: p.scan(value.value), results)
                else:
                    params = parse_qs(urlparse(value.value).query)
                    aid = (params.get("activeId") or params.get("id") or [""])[0]
                    if not aid:
                        raise ProviderError("未找到活动 ID，请从课程活动中打开签到")
                    dialog.close()
                    await self.sign_dialog(
                        {"courseId": (params.get("courseId") or [""])[0], "classId": ""},
                        {
                            "id": aid,
                            "type": 2,
                            "name": "二维码签到",
                            "signType": "2",
                            "url": value.value,
                            "_qr": value.value,
                        },
                    )

            primary("打开 / 提交扫码", lambda: guarded(submit), "qr_code")
            ui.button("关闭", on_click=dialog.close).props("flat")
        dialog.open()

    async def open_live(self, course):
        previous = self.live if self.course and self.course.get("lessonId") == course.get("lessonId") else {}
        self.stop_live()
        generation = self.live_generation
        provider = self.provider()
        await provider.checkin(course["lessonId"])
        if generation != self.live_generation:
            return
        self.live_provider = provider
        self.course = course
        self.live = {
            "presentation": {},
            "pres_id": "",
            "index": 0,
            "timeline": [],
            "connected": False,
            "follow": True,
            "unlocked": previous.get("unlocked", {}),
            "finished": False,
            "drafts": previous.get("drafts", {}),
        }
        self.body.clear()
        with self.body:
            ui.button("返回课程", icon="arrow_back", on_click=lambda: self.navigate("courses")).props("flat")
            with ui.row().classes("w-full justify-between items-center"):
                self.heading("LIVE CLASSROOM", course["name"], "查看整份 PPT、课堂时间线和题目。答案由你填写并提交。")
                self.live_status = ui.label("正在连接课堂…").classes("pill")
            self.live_accounts = self.selected_accounts()
            with ui.row().classes("w-full items-center gap-3"):
                ui.switch("跟随教师翻页", value=True, on_change=lambda e: self.live.update(follow=e.value))
                ui.button("重新连接", on_click=lambda: guarded(lambda: self.open_live(course)), icon="refresh").props(
                    "outline"
                )
                ui.button("位置签到", on_click=self.rain_location_dialog, icon="location_on").props("outline")
                ui.button("下载全部 PPT", on_click=lambda: guarded(self.download_slides), icon="download").props(
                    "outline"
                )
            self.slide_region = ui.column().classes("panel w-full gap-4")
            self.live_result = ui.column().classes("w-full")
            with ui.expansion("课堂时间线", icon="timeline").classes("panel w-full"):
                self.timeline_region = ui.column().classes("w-full")
            self.draw_slide()
            self.live_clock = ui.timer(1, self.update_live_countdown)

        async def receive(data):
            if generation != self.live_generation:
                return
            with self.client:
                self.live["connected"] = True
                self.live_status.text = "课堂已连接"
                op = data.get("op")
                if op == "hello" and data.get("message") == "lesson finished":
                    self.live_status.text = "课堂已结束"
                    self.live["finished"] = True
                    return
                if data.get("timeline"):
                    self.live["timeline"].extend(data["timeline"])
                    for event in data["timeline"]:
                        update_problem_timing(self.live["unlocked"], event)
                    for event in reversed(data["timeline"]):
                        if event.get("pres"):
                            data.setdefault("presentation", event["pres"])
                            data.setdefault("slideindex", event.get("si", 1))
                            break
                if op in ("hello", "showpresentation") and data.get("presentation"):
                    await self.load_presentation(str(data["presentation"]))
                    if generation != self.live_generation:
                        return
                if op in ("hello", "showpresentation", "slide", "slidenav") and self.live["follow"]:
                    index = slide_index(data)
                    if index is not None:
                        self.live["index"] = index
                update_problem_timing(self.live["unlocked"], data)
                if op == "lessonfinished":
                    self.live["finished"] = True
                    self.live_status.text = "课堂已结束"
                if op in ("locationcheckinext", "locationcheckinstart"):
                    ui.notify("课堂发起了位置签到，请手动提交", timeout=10000)
                if op not in ("hello", "ping", "pong"):
                    self.live["timeline"].append(data)
                if op in (
                    "hello",
                    "showpresentation",
                    "slide",
                    "slidenav",
                    "unlockproblem",
                    "extendtime",
                    "lessonfinished",
                ):
                    self.draw_slide()
                self.draw_timeline()

        async def listen():
            try:
                await provider.listen(course["lessonId"], receive)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                with self.client:
                    self.live_status.text = "连接已断开，请重新连接"
                    ui.notify(str(exc), type="negative")

        self.live_task = background_tasks.create(listen())

    async def load_presentation(self, presentation_id):
        if self.live.get("pres_id") == presentation_id:
            return
        generation = self.live_generation
        presentation = await self.live_provider.presentation(presentation_id, lesson_id=self.course["lessonId"])
        if generation != self.live_generation:
            return
        self.live["presentation"] = presentation
        self.live["pres_id"], self.live["index"] = presentation_id, 0

    def draw_slide(self):
        self.slide_region.clear()
        presentation = self.live.get("presentation") or {}
        slides = presentation.get("slides") or []
        with self.slide_region:
            if not slides:
                ui.label("等待教师展示 PPT…").classes("muted")
                return
            index = min(self.live["index"], len(slides) - 1)
            slide = slides[index]
            ui.label(presentation.get("title", "课堂 PPT")).classes("section-title")
            url = slide.get("coverAlt") or slide.get("cover") or slide.get("thumbnail")
            if url:
                image = ui.image(url).props("fit=contain").classes("w-full max-h-[65vh] bg-white")
                ui.button("全屏查看", icon="fullscreen").props("flat").on(
                    "click", js_handler=f"() => document.getElementById('c{image.id}')?.requestFullscreen?.()"
                )
            if slide.get("note"):
                rich(slide["note"])
            with ui.row().classes("w-full items-center justify-center"):

                def move(offset):
                    self.live["index"] = max(0, min(len(slides) - 1, index + offset))
                    self.draw_slide()

                ui.button(icon="chevron_left", on_click=lambda: move(-1)).props("flat round")
                ui.label(f"{index + 1} / {len(slides)}")
                ui.button(icon="chevron_right", on_click=lambda: move(1)).props("flat round")
                ui.button(
                    "保存本页", icon="download", on_click=lambda: guarded(lambda: self.download_slide(url, index + 1))
                ).props("flat").set_enabled(bool(url))
            with ui.row().classes("w-full flex-nowrap overflow-x-auto"):
                for i, s in enumerate(slides):
                    with ui.column().classes("w-20 shrink-0 cursor-pointer") as thumb:
                        thumb.on("click", lambda e, n=i: self.jump_slide(n))
                        ui.image(s.get("thumbnail") or s.get("coverAlt") or s.get("cover") or "").classes(
                            "w-20 h-14 rounded"
                        )
                        ui.label(str(i + 1)).classes("text-xs muted")
            if slide.get("problem"):
                self.rain_problem(slide["problem"])

    def jump_slide(self, index):
        self.live["index"] = index
        self.draw_slide()

    def rain_problem(self, problem):
        typ = int(problem.get("problemType", 0))
        ui.separator()
        ui.label("课堂题目").classes("section-title")
        self.live_problem_id = str(problem["problemId"])
        self.problem_countdown = ui.label().classes("text-sm text-amber-700")
        self.update_live_countdown()
        rich(problem.get("body", ""))
        options = options_for(problem, True)
        draft = self.live["drafts"].setdefault(
            str(problem["problemId"]), {"answer": None, "blanks": {}, "images": [], "retry": False}
        )
        controls = []
        if typ in (1, 2, 3, 6):
            multiple = typ == 2 or (typ == 3 and problem.get("pollingCount", 1) > 1)
            answer = (
                ui.select(options, multiple=True, value=draft["answer"] or [], label="选择答案")
                .props("outlined")
                .classes("w-full")
                if multiple
                else ui.radio(options, value=draft["answer"])
            )
            answer.bind_value(draft, "answer")
        elif typ == 4:
            controls = [
                field(f"第 {i + 1} 空", draft["blanks"].setdefault(str(i), "")).bind_value(draft["blanks"], str(i))
                for i in range(max(1, len(problem.get("blanks") or [])))
            ]
            answer = None
        else:
            answer = (
                ui.textarea("填写答案", value=draft["answer"] or "")
                .props("outlined")
                .classes("w-full")
                .bind_value(draft, "answer")
            )
        images = draft["images"]
        course = dict(self.course)
        live = self.live
        provider_for_upload = self.live_provider
        if typ == 5:

            async def upload(e):
                async def work():
                    images.append(await provider_for_upload.upload(await e.file.read(), e.file.name))
                    ui.notify("图片已上传")

                await guarded(work)

            ui.upload(label="上传答题图片", on_upload=upload, auto_upload=True).props(
                "accept=image/* max-file-size=20971520"
            )
        retry = ui.checkbox("补交 / 重试（使用课堂题目时间）").bind_value(draft, "retry")
        ui.label("倒计时结束后，提交会自动使用补交接口；是否接受由平台决定。").classes("muted text-xs")

        async def submit():
            use_retry, timing = rain_answer_timing(live["unlocked"], problem, retry.value)
            if live.get("finished") and not use_retry:
                raise ProviderError("课堂已结束，如需补交请勾选补交 / 重试")
            value = [c.value for c in controls] if controls else answer.value
            if (not value or (isinstance(value, list) and not all(value))) and not images:
                raise ProviderError("请先填写答案")

            async def operation(provider):
                await provider.ensure_lesson(course["lessonId"])
                return await provider.answer(
                    problem["problemId"],
                    typ,
                    options=value if isinstance(value, list) else [value],
                    content=value if isinstance(value, str) else "",
                    image_urls=images,
                    retry=use_retry,
                    time=timing,
                    lesson_id=course["lessonId"],
                )

            await self.batch(self.live_accounts, operation, self.live_result)

        primary("提交课堂答案", lambda: guarded(submit), "send")

    def update_live_countdown(self):
        label = getattr(self, "problem_countdown", None)
        if label is None or label.is_deleted:
            return
        timing = self.live.get("unlocked", {}).get(getattr(self, "live_problem_id", ""), {})
        if self.live.get("finished"):
            label.text = "课堂已结束；可按平台规则使用补交 / 重试"
        elif "deadline" in timing:
            remaining = max(0, int(timing["deadline"] - time.time()))
            label.text = (
                f"答题剩余 {remaining // 60:02d}:{remaining % 60:02d}"
                if remaining
                else "答题时间已到，可按平台规则使用补交 / 重试"
            )
        else:
            label.text = "等待教师开放题目；补交是否允许以平台返回为准"

    def draw_timeline(self):
        self.timeline_region.clear()
        with self.timeline_region:
            for event in self.live.get("timeline", [])[-150:]:
                with ui.row().classes("w-full items-center"):
                    ui.icon("circle", size="8px").classes("text-teal-600")
                    ui.label(str(event.get("type") or event.get("op") or "课堂事件")).classes("text-sm")
                    ui.label(str(event.get("message") or event.get("name") or event.get("event") or "")).classes(
                        "muted text-xs"
                    )
                    if event.get("pres"):

                        async def jump(e=event):
                            await self.load_presentation(str(e["pres"]))
                            if e.get("prob"):
                                slides = self.live["presentation"].get("slides") or []
                                self.live["index"] = next(
                                    (
                                        i
                                        for i, s in enumerate(slides)
                                        if (s.get("problem") or {}).get("problemId") == e["prob"]
                                    ),
                                    0,
                                )
                            else:
                                self.live["index"] = max(0, int(e.get("si", 1)) - 1)
                            self.draw_slide()

                        ui.button("查看", on_click=lambda f=jump: guarded(f)).props("flat dense")

    async def download_slide(self, url, number):
        import httpx

        if not url or not url.startswith("https://"):
            raise ProviderError("PPT 图片地址无效")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        extension = image_extension(response.content)
        ui.download(response.content, f"{number:03d}.{extension}")

    async def download_slides(self):
        import zipfile

        import httpx

        slides = self.live.get("presentation", {}).get("slides") or []
        if not slides:
            raise ProviderError("没有可下载的 PPT")
        missing = [
            str(i + 1)
            for i, slide in enumerate(slides)
            if not (slide.get("coverAlt") or slide.get("cover") or slide.get("thumbnail"))
        ]
        if missing:
            raise ProviderError("平台未提供这些页面的图片，无法完整下载 PPT：" + "、".join(missing))
        output = BytesIO()
        count = 0
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for i, slide in enumerate(slides):
                    url = slide.get("coverAlt") or slide.get("cover") or slide.get("thumbnail")
                    if url:
                        if not url.startswith("https://"):
                            raise ProviderError("PPT 图片地址无效")
                        r = await client.get(url)
                        r.raise_for_status()
                        extension = image_extension(r.content)
                        archive.writestr(f"{i + 1:03d}.{extension}", r.content)
                        count += 1
        if not count:
            raise ProviderError("平台没有提供可下载的 PPT 图片")
        ui.download(output.getvalue(), "课堂PPT.zip")

    def rain_location_dialog(self):
        course = dict(self.course)
        selected = self.live_accounts
        with ui.dialog() as dialog, ui.card().classes("w-[600px] p-6 gap-4"):
            ui.label("雨课堂位置签到").classes("section-title")
            location = self.location_fields(service.settings(self.account_id, course).get("location"))
            result = ui.column()

            async def submit():
                loc = location()
                if not loc:
                    raise ProviderError("请填写完整位置信息")

                async def operation(provider):
                    await provider.ensure_lesson(course["lessonId"])
                    return await provider.location_checkin(
                        course["lessonId"], loc["latitude"], loc["longitude"], loc["address"]
                    )

                await self.batch(selected, operation, result)

            primary("提交位置签到", lambda: guarded(submit), "check")
            ui.button("关闭", on_click=dialog.close).props("flat")
        dialog.open()

    def render_groups(self):
        self.heading("MESSAGES & GROUPS", "群聊与消息", "连接学习通即时消息，查看已加入群聊与群聊签到。")
        if self.platform != "chaoxing" or not self.account_id:
            self.empty("forum", "请选择学习通账号", "群聊功能使用学习通的环信即时通讯服务。")
            return
        with ui.row():
            primary("连接消息服务", lambda: guarded(self.connect_im), "sensors")
            ui.button("刷新群聊", icon="refresh", on_click=lambda: guarded(self.load_groups)).props("outline")
            ui.button(
                "启用桌面通知",
                icon="notifications",
                on_click=lambda: ui.run_javascript("Notification.requestPermission()"),
            ).props("flat")
        ui.label("环信 Web 接入受平台设备策略限制；连接状态会直接显示，群聊签到也可通过活动 ID 打开。").classes(
            "muted text-xs"
        )
        with ui.row().classes("panel w-full items-center"):
            active_id = field("群聊签到活动 ID")

            async def direct():
                if not (active_id.value or "").isdigit():
                    raise ProviderError("请填写有效活动 ID")
                await self.sign_dialog(
                    {"courseId": "", "classId": ""},
                    {"id": active_id.value, "type": 2, "name": "群聊签到", "signType": "0"},
                    group=True,
                )

            primary("打开签到", lambda: guarded(direct), "open_in_new")
        self.group_region = ui.column().classes("w-full gap-3")
        self.message_region = ui.column().classes("w-full gap-3")

    async def connect_im(self):
        account = self.provider().account
        im = account.get("imAccount") or {}
        if not im.get("userName") or not im.get("password"):
            raise ProviderError("缺少 IM 会话，请刷新账号资料或重新登录")
        args = json.dumps({**im, "accountId": self.account_id})
        result = await ui.run_javascript(f"return await window.courseIM.connect({args})", timeout=40)
        if result.get("error"):
            raise ProviderError(result["error"])
        ui.notify("消息服务已连接", type="positive")
        await self.load_groups()

    async def load_groups(self):
        result = await ui.run_javascript("return await window.courseIM.groups()", timeout=40)
        if result.get("error"):
            raise ProviderError(result["error"])
        self.group_region.clear()
        with self.group_region:
            for group in result.get("data", []):
                gid = str(group.get("groupid") or group.get("id"))
                with ui.row().classes("activity-row"):
                    ui.icon("groups", size="24px").classes("text-teal-700")
                    ui.label(group.get("groupname") or group.get("name") or gid).classes("grow")
                    ui.button("签到消息", on_click=lambda g=gid: guarded(lambda: self.group_history(g))).props(
                        "outline"
                    )

    async def group_history(self, group_id):
        result = await ui.run_javascript(f"return await window.courseIM.history({json.dumps(group_id)})", timeout=60)
        if result.get("error"):
            ui.notify("云端历史读取失败，将显示本地已接收消息：" + result["error"], type="warning")
        self.message_region.clear()
        with self.message_region:
            messages = result.get("data", [])
            local = [
                r["message"]
                for r in service.store.all("im-messages").values()
                if r["account_id"] == self.account_id and str(r["message"].get("to", "")) == group_id
            ]
            messages = list({str(m.get("id") or i): m for i, m in enumerate(local + messages)}.values())
            ui.label(f"群聊消息 · {len(messages)} 条").classes("section-title")
            for message in messages:
                self.draw_im_message(message)
            if not messages:
                ui.label("暂无可读取的历史消息，平台可能未开放漫游权限。").classes("muted")

    def handle_im(self, message):
        if message.get("_account_id") != self.account_id:
            return
        key = f"{self.account_id}:{message.get('id') or __import__('uuid').uuid4().hex}"
        service.store.put("im-messages", key, {"account_id": self.account_id, "message": message})
        self.im_messages.append(message)
        self.im_messages = self.im_messages[-200:]
        if self.section == "groups":
            with self.message_region:
                self.draw_im_message(message)
        activity = (message.get("ext") or {}).get("attachment", {})
        if isinstance(activity, str):
            try:
                activity = json.loads(activity)
            except ValueError:
                activity = {}
        if isinstance(activity, dict) and activity.get("att_chat_course"):
            ui.notify("收到新的课堂活动", timeout=8000)

    def draw_im_message(self, message):
        ext = message.get("ext") or message.get("attributes") or {}
        attachment = ext.get("attachment") or {}
        if isinstance(attachment, str):
            try:
                attachment = json.loads(attachment)
            except ValueError:
                attachment = {}
        info = attachment.get("att_chat_course") or attachment.get("att_sign") or {}
        with ui.column().classes("panel w-full gap-2"):
            ui.label(str(message.get("from", "课堂消息"))).classes("muted text-xs")
            ui.label(str(info.get("title") or message.get("msg") or "活动消息"))
            if info.get("aid"):

                async def open_message():
                    ci = info.get("courseInfo") or {}
                    course = {"courseId": str(ci.get("courseid", "")), "classId": str(ci.get("classid", ""))}
                    active = {
                        "id": str(info["aid"]),
                        "type": int(info.get("atype", 2)),
                        "name": info.get("title", ""),
                        "url": info.get("url", ""),
                        "signType": "0",
                    }
                    await self.open_activity(course, active, group=not bool(course["courseId"]))

                primary("打开活动", lambda: guarded(open_message), "open_in_new")

    def render_history(self):
        self.heading("ACTIVITY LOG", "操作记录", "查看手动提交的逐账号结果。记录保存在后端服务中。")
        rows = list(service.store.all("history").values())[-200:][::-1]
        if not rows:
            self.empty("history", "还没有操作记录", "完成签到、答题或其他提交后，结果会显示在这里。")
        for row in rows:
            with ui.row().classes("activity-row"):
                ui.icon("check_circle" if row["ok"] else "error_outline").classes(
                    "text-teal-700" if row["ok"] else "text-red-600"
                )
                ui.label(row["account"]).classes("font-semibold")
                ui.label("操作成功" if row["ok"] else row.get("error", "")).classes("grow text-sm")
                ui.label(datetime.fromtimestamp(row["time"]).strftime("%m-%d %H:%M:%S")).classes("muted text-xs")


@ui.page("/")
def index(section: str = "courses", error: str = ""):
    Dashboard(section=section, settings_error=error).build()
