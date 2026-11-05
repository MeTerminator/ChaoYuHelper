"""Homework list, review and manual answer pages."""

from urllib.parse import urlencode

from nicegui import ui

from ..providers.base import ProviderError
from ..service import service
from .components import field, guarded, homework_rich, primary


class HomeworkView:
    def homework_navigate(self, course, mode="", work=None):
        params = {"account": self.account_id, "course": course["courseId"], "class_id": course["classId"]}
        if work is not None:
            params["work_id"] = work["id"]
        ui.navigate.to("/homework" + (f"/{mode}" if mode else "") + "?" + urlencode(params))

    async def homework_dialog(self, course):
        self.homework_navigate(course)

    def homework_heading(self, course, title, subtitle, back_to_list=False):
        items = [("我的课程", lambda: ui.navigate.to("/")), ("我的作业", lambda: ui.navigate.to("/?section=homework"))]
        if back_to_list:
            items.append(("课程作业", lambda: self.homework_navigate(course)))
        items.append((title, None))
        self.set_navigation(items)
        self.body.clear()
        with self.body:
            ui.button(
                "返回课程作业" if back_to_list else "返回课程",
                icon="arrow_back",
                on_click=(lambda: self.homework_navigate(course)) if back_to_list else (lambda: ui.navigate.to("/")),
            ).props("flat")
            ui.button("我的作业汇总", icon="assignment", on_click=lambda: ui.navigate.to("/?section=homework")).props(
                "flat"
            )
            self.heading("COURSE HOMEWORK", title, subtitle)

    async def homework_list_page(self, course):
        generation = self.live_generation
        works = await self.provider().homework_list(course)
        if generation != self.live_generation:
            return
        self.homework_heading(course, "课程作业", course["name"])
        account = service.store.get("accounts", self.account_id, {})
        submitted_statuses = ("已完成", "待批阅", "已提交")
        submitted = sum(w["status"] in submitted_statuses for w in works)
        with self.body:
            with ui.row().classes("w-full gap-4"):
                for label, value in [
                    ("全部作业", len(works)),
                    ("已提交 / 已完成", submitted),
                    ("其他状态", len(works) - submitted),
                ]:
                    with ui.column().classes("panel stat gap-1"):
                        ui.label(label).classes("muted text-sm")
                        ui.label(str(value)).classes("stats-value")
            with ui.row().classes("panel w-full items-center justify-between gap-4"):
                with ui.column().classes("gap-1"):
                    ui.label(course["name"]).classes("font-semibold")
                    ui.label(" · ".join(v for v in [course.get("teacher"), course.get("note")] if v)).classes(
                        "muted text-sm"
                    )
                    ui.label(f"当前作答账号：{account.get('name', '')} · {account.get('uid', '')}").classes(
                        "muted text-sm"
                    )
                ui.button(
                    "刷新作业", icon="refresh", on_click=lambda: guarded(lambda: self.homework_list_page(course))
                ).props("outline")
            if not works:
                self.empty("assignment", "该课程暂无作业", "老师发布作业后，可点击刷新作业查看。")
            panels = []
            descriptions = {
                "已完成": "作业已完成，可查看自己的答案及老师已开放的批阅信息。",
                "待批阅": "已提交，等待老师批阅。",
                "已提交": "已提交，可查看作答记录。",
            }
            for work in works:
                with ui.column().classes("panel w-full gap-3") as panel:
                    panels.append(panel)
                    completed = work["status"] in submitted_statuses
                    with ui.row().classes("w-full items-center gap-3"):
                        ui.icon("assignment_turned_in" if completed else "assignment", size="24px").classes(
                            "text-teal-700"
                        )
                        ui.label(work["name"]).classes("grow font-semibold homework-list-title")
                        ui.label(work["status"]).classes("pill")
                        ui.button(
                            "作答记录" if completed else "作答",
                            on_click=lambda w=work, done=completed: self.homework_navigate(
                                course, "review" if done else "answer", w
                            ),
                        ).props("outline")
                    ui.label(descriptions.get(work["status"], "请查看平台作业要求，确认开放时间后进入作答。")).classes(
                        "muted text-sm"
                    )
                    for line in work.get("details", []):
                        ui.label(line).classes("text-sm")
                    if completed:
                        with ui.expansion("详细信息", icon="info_outline").classes(
                            "w-full homework-details"
                        ) as expansion:
                            detail_box = ui.column().classes("w-full gap-2")

                        async def load_details(e, w=work, box=detail_box, state={"loaded": False, "loading": False}):
                            if not e.value or state["loaded"] or state["loading"]:
                                return
                            state["loading"] = True
                            generation = self.live_generation
                            box.clear()
                            with box:
                                ui.label("正在加载作业信息…").classes("muted text-sm")
                            try:
                                detail = await self.provider().homework_review(w)
                                if generation != self.live_generation or box.is_deleted:
                                    return
                                box.clear()
                                with box:
                                    ui.label(f"题量：{len(detail['questions'])} 题").classes("text-sm")
                                    for line in detail.get("details", []):
                                        ui.label(line).classes("text-sm")
                                    ui.label("答案、得分和解析请进入作答记录查看，以老师开放的信息为准。").classes(
                                        "muted text-sm"
                                    )
                                state["loaded"] = True
                            except Exception as exc:
                                if not box.is_deleted:
                                    box.clear()
                                    with box:
                                        ui.label(f"加载失败：{exc}。收起后重新展开可重试。").classes(
                                            "text-red-700 text-sm"
                                        )
                            finally:
                                state["loading"] = False

                        expansion.on_value_change(load_details)
            self.paginate_panels(panels, 10, "作业列表")
            ui.label("作业按当前账号独立作答；参考答案和解析以老师开放范围为准。").classes("muted text-sm")

    async def homework_review_page(self, course, work):
        generation = self.live_generation
        account_id = self.account_id
        detail = await self.provider().homework_review(work)
        if generation != self.live_generation:
            return
        self.homework_heading(course, "作答记录", f"{course['name']} · {work['name']}", True)
        with self.body:
            with ui.row().classes("panel w-full gap-4"):
                ui.label(f"共 {len(detail['questions'])} 题").classes("font-semibold")
                ui.label(f"作业状态：{work['status']}").classes("text-sm")
                for line in detail.get("details", []):
                    ui.label(line).classes("muted text-sm")
            panels = []
            for question in detail["questions"]:
                with ui.column().classes("panel w-full gap-3") as panel:
                    homework_rich(question, account_id)
                    panels.append(panel)
            if not panels:
                self.empty("description", "暂无作答记录", "平台尚未开放可查看的题目或记录。")
            self.paginate_panels(panels, 5, "作答记录")

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

    def homework_start_page(self, course, provider, account_id, work):
        self.homework_heading(course, "作业作答", f"{course['name']} · {work['name']}", True)
        with self.body, ui.column().classes("panel w-full gap-4"):
            ui.label("进入作答可能启动平台的限时作业计时。请准备好后开始。")

            async def start():
                if self.account_id != account_id:
                    raise ProviderError("账号已切换，请重新打开作业")
                generation = self.live_generation
                start_button.disable()
                try:
                    questions = await provider.homework_open(work)
                    if generation != self.live_generation:
                        return
                    self.homework_answer_page(course, provider, account_id, work, questions)
                finally:
                    if not start_button.is_deleted:
                        start_button.enable()

            start_button = primary("开始作答", lambda: guarded(start), "edit_note")

    def homework_answer_page(self, course, provider, account_id, work, questions):
        key = f"{account_id}:{work['id']}"
        draft = service.store.get("homework-drafts", key, {})
        self.homework_heading(course, "作业作答", f"{course['name']} · {work['name']}", True)
        with self.body:
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
                    homework_rich(question["content"], account_id, base=question["base_url"])
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
                                    homework_rich(media, account_id, base=question["base_url"])
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
                    ui.button("查看作答记录", on_click=lambda: self.homework_navigate(course, "review", work)).props(
                        "outline"
                    )

            with ui.row():

                def leave():
                    service.store.put("homework-drafts", key, answers())
                    self.homework_navigate(course)

                ui.button("保存草稿并返回", on_click=leave).props("flat")
                ui.button("保存本地草稿", on_click=save, icon="save").props("outline")
                submit_button = primary("提交作业", lambda: guarded(submit), "send")
                if any(not q["supported"] for q in questions):
                    submit_button.disable()
