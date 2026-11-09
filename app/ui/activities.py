"""Questions, evaluation, discussion and picked-student dialogs."""

import json
from datetime import datetime

from nicegui import ui

from ..activity import (
    PickUpdates,
    reference_answers,
    topic_id,
    validate_answers,
)
from ..ai_client import get_ai_config, solve_questions
from ..providers.base import ProviderError
from ..service import service
from .components import field, guarded, options_for, primary, rich
from .constants import TYPES


class ActivitiesView:
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

            async def ai_solve():
                ai_config = get_ai_config(service.store)
                if not ai_config.get("api_key"):
                    ui.notify("未配置 AI API KEY，请先前往「系统设置」配置", type="warning")
                    return
                ui.notify("正在请求 AI 解题，请稍候...", type="info")
                answers = await solve_questions(
                    ai_config, questions, course_name=course.get("name", ""), active_name=active.get("name", "")
                )
                for qid, val in answers.items():
                    widget = widgets.get(qid)
                    if widget is None:
                        continue
                    if isinstance(widget, list):
                        for w, v in zip(widget, val):
                            w.value = str(v)
                    else:
                        widget.value = val
                ui.notify("AI 作答已填入表单", type="positive")

            with ui.row().classes("gap-2"):
                if active["type"] == 42:
                    ui.button("填入平台参考答案", on_click=fill_reference, icon="auto_fix_high").props("outline")
                if active["type"] in (42, 43, 14):
                    ui.button("AI 解答并填入", on_click=lambda: guarded(ai_solve), icon="psychology").props("outline")
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
