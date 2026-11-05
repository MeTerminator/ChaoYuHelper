"""Rain Classroom live events, timeline and manual answers."""

import asyncio
import time

from nicegui import background_tasks, ui

from ..activity import (
    rain_answer_timing,
    slide_index,
    update_problem_timing,
)
from ..providers.base import ProviderError
from ..service import service
from .components import field, guarded, options_for, primary, rich


class ClassroomView:
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
