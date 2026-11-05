"""Dashboard composition and lifecycle."""

import asyncio
from html import escape
from urllib.parse import urlencode

from nicegui import app, ui

from ..auth import CSRF_COOKIE
from ..service import service
from .accounts import AccountsView
from .activities import ActivitiesView
from .capture import CaptureView
from .classroom import ClassroomView
from .components import primary
from .constants import PLATFORMS, SERVERS
from .courses import CoursesView
from .history import HistoryView
from .homework import HomeworkView
from .im_conversations import ConversationsView
from .im_events import MessageEventsView
from .im_rendering import MessagesView
from .my_homework import MyHomeworkView
from .presentation import PresentationView
from .settings import SettingsView
from .sign import SignView


class Dashboard(
    AccountsView,
    CoursesView,
    SettingsView,
    HomeworkView,
    MyHomeworkView,
    SignView,
    ActivitiesView,
    CaptureView,
    ClassroomView,
    PresentationView,
    ConversationsView,
    MessageEventsView,
    MessagesView,
    HistoryView,
):
    def __init__(self, section="courses", settings_error=""):
        self.platform = app.storage.user.get("platform", "chaoxing")
        self.server = app.storage.user.get("server", "yuketang")
        self.account_id = app.storage.user.get("account_id", "")
        self.course = None
        self.section = (
            section if section in ("courses", "homework", "accounts", "groups", "history", "settings") else "courses"
        )
        self.settings_error = settings_error
        self.query = ""
        self.only_ongoing = False
        self.live_task = None
        self.live_clock = None
        self.live_generation = 0
        self.live = {}
        self.busy = False
        self.im_groups = {}
        self.im_history = {}
        self.im_unread = {}
        self.im_selected = None
        self.im_query = ""
        self.im_status = "未连接"
        self.im_request = 0
        self.im_receipts = {}
        self.im_profiles = {}
        self.im_pages = {}
        self.im_drafts = {}
        self.im_user_id = ""
        self.im_filter = "all"
        self.im_sending = False
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

    def build(self, render=True):
        ui.colors(primary="#317868", secondary="#748a85", positive="#317868")
        ui.dark_mode(value=None)
        ui.add_css((__import__("pathlib").Path(__file__).resolve().parents[1] / "static/style.css").read_text())
        self.client.on_disconnect(self.stop_live)
        self.im_event_account = None
        self.im_event_sequence = 0
        self.im_was_connected = False
        self.im_connecting = None
        self.im_polling = False
        ui.timer(0.5, self.poll_im)
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
                ("homework", "我的作业", "assignment"),
                ("accounts", "账号管理", "manage_accounts"),
                ("groups", "群聊与消息", "forum"),
                ("history", "操作记录", "history"),
                ("settings", "系统设置", "settings"),
            ]:
                self.nav_buttons[key] = (
                    ui.button(title, icon=icon, on_click=lambda k=key: self.navigate(k))
                    .props("flat no-caps color=white")
                    .classes("nav-button" + (" active" if key == self.section else ""))
                )
        with ui.header().classes("topbar items-center gap-4"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense")
            self.page_navigation = (
                ui.element("nav").props('aria-label="页面导航"').classes("page-navigation items-center")
            )
            ui.space()
            csrf = escape(self.client.request.cookies.get(CSRF_COOKIE, ""), quote=True)
            ui.html(
                f'<form method="post" action="/admin/logout"><input type="hidden" name="csrf" value="{csrf}">'
                '<button type="submit" style="color:var(--accent);cursor:pointer">退出登录</button></form>',
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
        if render:
            self.render()

    def set_navigation(self, items):
        self.page_navigation.clear()
        with self.page_navigation:
            for index, (label, callback) in enumerate(items):
                if index:
                    ui.icon("chevron_right", size="16px").classes("muted")
                if callback:
                    ui.button(label, on_click=callback).props("flat dense").classes("navigation-link")
                else:
                    ui.label(label).props('aria-current="page"').classes("navigation-current")

    def navigate(self, section):
        if self.client.request.url.path.startswith("/homework"):
            ui.navigate.to("/?" + urlencode({"section": section}))
            return
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
        self.im_request += 1
        self.im_groups.clear()
        self.im_receipts.clear()
        self.im_profiles.clear()
        self.im_pages.clear()
        self.im_drafts.clear()
        self.im_user_id = ""
        self.im_history.clear()
        self.im_unread.clear()
        self.im_selected = None
        self.im_status = "未连接"
        self.account_id, self.course = value or "", None
        if self.section != "homework":
            self.section = "courses"
        app.storage.user["account_id"] = self.account_id
        if self.client.request.url.path.startswith("/homework"):
            ui.navigate.to("/")
            return
        self.render()

    def heading(self, eyebrow, title, subtitle):
        with ui.column().classes("gap-1"):
            ui.label(eyebrow).classes("eyebrow")
            ui.label(title).classes("page-title")
            ui.label(subtitle).classes("muted text-sm")

    def render(self):
        titles = {
            "courses": "我的课程",
            "homework": "我的作业",
            "accounts": "账号管理",
            "groups": "群聊与消息",
            "history": "操作记录",
            "settings": "系统设置",
        }
        self.set_navigation(
            [("我的课程", lambda: self.navigate("courses")), (titles[self.section], None)]
            if self.section != "courses"
            else [("我的课程", None)]
        )
        for key, button in self.nav_buttons.items():
            button.classes(add="active" if key == self.section else "", remove="" if key == self.section else "active")
        self.body.clear()
        with self.body:
            if self.section == "courses":
                self.render_courses()
            elif self.section == "homework":
                self.render_my_homework()
            elif self.section == "accounts":
                self.render_accounts()
            elif self.section == "groups":
                self.render_groups()
            elif self.section == "history":
                self.render_history()
            elif self.section == "settings":
                self.render_settings()

    def empty(self, icon, title, description, action=None):
        with ui.column().classes("panel empty"):
            ui.icon(icon, size="48px").classes("text-gray-300 mb-2")
            ui.label(title).classes("text-lg font-semibold")
            ui.label(description).classes("muted text-sm mb-3")
            if action:
                primary(action[0], action[1], "add")
