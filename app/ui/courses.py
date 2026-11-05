"""Course browsing and activity dispatch."""

from datetime import datetime

from nicegui import ui

from ..providers.base import ProviderError
from ..providers.chaoxing import text
from ..service import service
from .components import guarded, primary
from .constants import PLATFORMS, SIGN_TYPES, TYPES


class CoursesView:
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
                with ui.column().classes("course-card gap-0"):
                    with ui.row().classes(f"course-art course-art-{index % 4} w-full items-center justify-between"):
                        ui.icon(["menu_book", "architecture", "science", "language"][index % 4], size="32px")
                        ui.label("正在上课" if course.get("ongoing") else PLATFORMS[self.platform]).classes("pill")
                    with ui.column().classes("p-5 w-full gap-2"):
                        ui.label(course["name"]).classes("course-name")
                        ui.label(f"{course.get('teacher', '')} · {course.get('note', '')}").classes("muted text-xs")
                        with ui.row().classes("course-actions w-full items-center mt-3 gap-2"):
                            ui.button(
                                "课程设置",
                                icon="tune",
                                on_click=lambda c=course: self.settings_dialog(c),
                            ).props("flat dense").classes("course-action-btn")
                            if self.platform == "chaoxing":
                                ui.button(
                                    "课程作业",
                                    icon="assignment",
                                    on_click=lambda c=course: self.homework_navigate(c),
                                ).props("outline dense").classes("course-action-btn")
                            primary(
                                "进入课堂" if self.platform == "rainClassroom" else "查看活动",
                                lambda c=course: guarded(lambda: self.open_course(c)),
                                "arrow_forward",
                            ).props("dense").classes("course-action-btn")

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
        self.set_navigation([("我的课程", lambda: self.navigate("courses")), (course["name"], None)])
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
            counts = {key: sum(a["type"] == key for a in activities) for key in TYPES}
            with ui.row().classes("w-full items-center justify-between flex-nowrap"):
                ongoing = ui.switch("仅看进行中", value=False).classes("shrink-0")
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
                    .style("width: 240px; max-width: 55%")
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
