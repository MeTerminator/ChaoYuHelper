"""Official mobile exam page displayed through an isolated Chromium webview."""

from nicegui import ui

from ..exams import HEIGHT, IMAGE_KINDS, WIDTH
from ..providers.base import ProviderError
from ..service import service
from .components import guarded


class ExamsView:
    def render_exams(self):
        self.heading("MY EXAMS", "我的考试", "使用当前账号打开学习通 APP 官方考试页面，手动作答与交卷。")
        if self.platform != "chaoxing":
            self.empty("quiz", "请切换到学习通", "我的考试目前支持学习通。")
            return
        if not self.account_id:
            self.empty("quiz", "请先添加账号", "登录学习通后即可查看考试。", ("添加账号", self.login_dialog))
            return
        account_id, generation = self.account_id, self.live_generation
        courses = service.store.get("courses", account_id, [])
        session = service.exams.sessions.get(account_id)
        with ui.row().classes("panel w-full items-center gap-3"):
            choices = {str(i): f"{c['name']} · {c.get('note', '')}" for i, c in enumerate(courses)}
            course_select = (
                ui.select(choices, value=next(iter(choices), None), label="课程")
                .props("outlined dense options-dense")
                .classes("grow")
            )
            open_button = ui.button("打开 APP 考试页面", icon="quiz")
            ui.button("预设图片", icon="photo_library", on_click=lambda: self.exam_images_dialog(account_id)).props(
                "outline"
            )
        if not courses:
            ui.label("暂无课程缓存，请先到“我的课程”刷新课程列表。").classes("muted text-sm")
            open_button.disable()
        status = ui.label("选择课程后打开考试列表；点击试卷内的开始按钮可能启动计时。").classes("muted text-sm")
        content = ui.column().classes("w-full gap-3")

        async def open_page():
            if generation != self.live_generation or self.account_id != account_id:
                return
            if course_select.value not in choices:
                raise ProviderError("请先选择课程")
            open_button.disable()
            status.set_text("正在打开官方考试页面…")
            try:
                current = await service.exams.open(account_id, courses[int(course_select.value)])
                if generation != self.live_generation or content.is_deleted:
                    return
                content.clear()
                with content:
                    self.draw_exam_webview(current, generation)
                status.set_text("已打开；文字题请先点中试卷输入框，再使用下方文字输入区。")
                open_button.set_text("恢复当前考试页面")
            except Exception:
                status.set_text("打开失败，请检查网络或重新登录账号后重试。")
                raise
            finally:
                if not open_button.is_deleted:
                    open_button.enable()

        open_button.on_click(lambda: guarded(open_page))
        if session and not session.closed:
            with content:
                self.draw_exam_webview(session, generation)
            open_button.set_text("恢复当前考试页面")
            status.set_text("已恢复当前会话；关闭或切换后台页面不会结束考试，平台计时仍会继续。")

    def exam_images_dialog(self, account_id):
        with ui.dialog() as dialog, ui.card().classes("w-[760px] max-w-full p-6 gap-4"):
            ui.label("考试预设图片").classes("section-title")
            ui.label("按账号加密保存。当前尚未接通教师端抓拍上传，保存图片不会自动上报。").classes(
                "text-amber-800 text-sm"
            )
            images = service.exams.presets(account_id)
            with ui.row().classes("w-full gap-4"):
                for kind, label in IMAGE_KINDS.items():
                    with ui.column().classes("grow w-44 gap-2"):
                        ui.label(label).classes("font-semibold")
                        preview = ui.image(images.get(kind, "")).classes("w-full h-32 rounded-lg").props("fit=contain")
                        preview.set_visibility(bool(images.get(kind)))
                        state = ui.label("已保存" if images.get(kind) else "未设置").classes("muted text-sm")

                        async def upload(e, key=kind, image=preview, caption=state):
                            async def perform():
                                service.exams.set_image(account_id, key, await e.file.read())
                                image.set_source(service.exams.presets(account_id)[key])
                                image.set_visibility(True)
                                caption.set_text("已加密保存，尚未上报")

                            await guarded(perform)

                        def remove(key=kind, image=preview, caption=state):
                            service.exams.set_image(account_id, key, None)
                            image.set_source("")
                            image.set_visibility(False)
                            caption.set_text("未设置")

                        ui.upload(label="选择图片", on_upload=upload, auto_upload=True).props(
                            "accept=image/jpeg,image/png,image/webp max-file-size=8388608"
                        ).classes("w-full")
                        ui.button("移除", on_click=remove).props("flat dense")
            ui.button("完成", on_click=dialog.close).props("flat")
        dialog.open()

    def draw_exam_webview(self, session, generation):
        active = {"polling": False, "dialog": None, "modal": None}
        account_id = session.account_id

        def current():
            return generation == self.live_generation and self.account_id == account_id and not session.closed

        async def action(kind, value=None):
            if not current():
                raise ProviderError("考试页面已切换，请重新打开")
            await session.input(kind, value)

        with ui.row().classes("panel w-full items-center gap-3"):
            ui.label(session.course["name"]).classes("font-semibold grow")
            ui.button("返回上页", icon="arrow_back", on_click=lambda: guarded(lambda: action("back"))).props("flat")

            def confirm_reload():
                with ui.dialog() as confirm, ui.card().classes("p-6 gap-4"):
                    ui.label("重新加载当前试卷？未保存到平台的答案可能丢失。")

                    async def reload_page():
                        confirm.close()
                        await action("reload")

                    ui.button("重新加载", on_click=lambda: guarded(reload_page))
                    ui.button("取消", on_click=confirm.close).props("flat")
                confirm.open()

            ui.button("刷新页面", icon="refresh", on_click=confirm_reload).props("flat")

            def confirm_close():
                with ui.dialog() as confirm, ui.card().classes("p-6 gap-4"):
                    ui.label("关闭考试会话？这不会交卷或暂停平台计时；未保存答案可能丢失。")

                    async def close_session():
                        confirm.close()
                        await service.exams.remove(account_id)
                        if current() or generation == self.live_generation:
                            self.stop_live()
                            self.render()

                    ui.button("关闭会话", on_click=lambda: guarded(close_session)).props("color=negative")
                    ui.button("继续考试", on_click=confirm.close).props("flat")
                confirm.open()

            ui.button("关闭会话", on_click=confirm_close).props("flat")
        with ui.row().classes("w-full items-start gap-5"):
            with ui.column().classes("exam-webview gap-0"):

                async def click(e):
                    await guarded(lambda: action("tap", (e.image_x, e.image_y)))

                image = (
                    ui.interactive_image(size=(WIDTH, HEIGHT), on_mouse=click, events=["click"])
                    .classes("w-full rounded-lg border cursor-pointer")
                    .props('tabindex=0 aria-label="学习通官方考试页面，点击操作试卷"')
                )

                async def key(e):
                    await guarded(lambda: action("key", e.args.get("key")))

                image.on(
                    "keydown",
                    key,
                    ["key"],
                    js_handler="""(e) => {
                    if (['Backspace','Delete','Enter','Tab','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(e.key)) {
                        e.preventDefault(); emit({key:e.key});
                    }
                }""",
                )

                async def wheel(e):
                    await guarded(lambda: action("scroll", e.args.get("deltaY", 0)))

                image.on(
                    "wheel",
                    wheel,
                    ["deltaY"],
                    throttle=0.15,
                    js_handler="""(e) => {
                    e.preventDefault(); emit({deltaY:e.deltaY});
                }""",
                )
                with ui.row().classes("w-full justify-between mt-2"):
                    ui.button("向上翻", icon="expand_less", on_click=lambda: guarded(lambda: action("scroll", -550)))
                    ui.button("向下翻", icon="expand_more", on_click=lambda: guarded(lambda: action("scroll", 550)))
            with ui.column().classes("panel grow exam-controls gap-3"):
                ui.label("作答操作").classes("font-semibold")
                ui.label("选择题直接点击页面选项；文字题先点击题内输入框，再输入文字并点击“输入到试卷”。").classes(
                    "muted text-sm"
                )
                entry = ui.textarea("输入文字 / 粘贴答案").props("outlined").classes("w-full")

                async def insert():
                    if entry.value:
                        await action("text", entry.value)
                        entry.set_value("")

                ui.button("输入到试卷", icon="keyboard", on_click=lambda: guarded(insert))
                with ui.row().classes("gap-2"):
                    for label, value in [
                        ("全选", "ControlOrMeta+A"),
                        ("删除", "Backspace"),
                        ("换行", "Enter"),
                        ("下个输入框", "Tab"),
                    ]:
                        ui.button(label, on_click=lambda v=value: guarded(lambda: action("key", v))).props(
                            "outline dense"
                        )
                ui.separator()
                ui.label("保存与交卷使用试卷内按钮，结果以平台页面提示为准。切换后台页面不暂停考试计时。").classes(
                    "muted text-sm"
                )
                connection = ui.label("正在加载页面…").classes("text-sm")
                with ui.expansion("APP 兼容状态", icon="info_outline").classes("w-full"):
                    ui.label("原生摄像头、截屏、直播及监考协议尚未接通。需要这些能力的考试可能无法进入。").classes(
                        "text-amber-800 text-sm"
                    )
                    log = ui.label().classes("whitespace-pre-line text-xs muted")

        async def poll():
            if image.is_deleted:
                timer.cancel()
                return
            if not current() or active["polling"]:
                return
            active["polling"] = True
            try:
                if active["modal"] and active["dialog"] is not session.dialog:
                    active["modal"].close()
                    active["modal"] = None
                if session.dialog and active["dialog"] is not session.dialog:
                    active["dialog"] = session.dialog
                    native = session.dialog
                    with ui.dialog().props("persistent") as dialog, ui.card().classes("p-6 gap-4 max-w-xl"):
                        ui.label("考试页面提示").classes("font-semibold")
                        ui.label(native.message).classes("whitespace-pre-wrap")
                        prompt = ui.input("输入内容", value=native.default_value) if native.type == "prompt" else None

                        async def answer(accept):
                            if session.dialog is native:
                                await session.answer_dialog(accept, prompt.value if prompt else "")
                            dialog.close()

                        ui.button("确定", on_click=lambda: guarded(lambda: answer(True)))
                        if native.type != "alert":
                            ui.button("取消", on_click=lambda: guarded(lambda: answer(False))).props("flat")
                    active["modal"] = dialog
                    dialog.open()
                if not session.dialog:
                    frame = await session.frame()
                    if current() and not image.is_deleted and frame:
                        image.set_source(frame)
                        connection.set_text("官方页面已连接")
                if not log.is_deleted:
                    log.set_text("\n".join(f"{e['time']} {e['message']}" for e in session.events) or "尚无原生能力请求")
            except Exception:
                if not connection.is_deleted:
                    connection.set_text("页面暂时无法更新，请检查网络；会话仍保留。")
            finally:
                active["polling"] = False

        timer = ui.timer(1, poll)
        self.pick_timers.append(timer)
        ui.timer(0.01, poll, once=True)
