"""All-course homework overview for one account."""

from nicegui import ui

from ..homework_summary import SUBMITTED, load_summary
from ..service import service
from .access_verification import render_access_verification


class MyHomeworkView:
    def render_my_homework(self):
        self.heading("MY HOMEWORK", "我的作业", "汇总当前学习通账号各科作业，集中查看进度与作答记录。")
        if self.platform != "chaoxing":
            self.empty("assignment", "请切换到学习通", "我的作业目前支持学习通课程。")
            return
        if not self.account_id:
            self.empty("assignment", "请先添加账号", "登录学习通后即可汇总各科作业。", ("添加账号", self.login_dialog))
            return
        generation, account_id = self.live_generation, self.account_id
        with ui.row().classes("w-full items-center gap-3"):
            refresh = ui.button("刷新全部作业", icon="refresh")
            progress = ui.label("正在读取课程与各科作业…").classes("muted text-sm")
        content = ui.column().classes("w-full gap-4")
        loading = False

        async def load():
            nonlocal loading
            if loading or content.is_deleted or generation != self.live_generation:
                return
            loading = True
            refresh.disable()
            progress.set_text("正在读取课程与各科作业…")
            try:
                summary = await load_summary(service, account_id)
                if content.is_deleted or generation != self.live_generation:
                    return
                content.clear()
                with content:
                    self.draw_homework_summary(summary, on_verified=load)
                if summary.get("verification_required"):
                    message = "学习通要求验证码，请在下方输入后继续"
                elif summary.get("from_cache"):
                    message = "已显示缓存数据"
                else:
                    message = "部分课程读取失败" if summary["errors"] else "读取完成"
                remaining = summary.get("retry_after", 0)
                if remaining and not summary.get("verification_required"):
                    message += f"（{remaining} 秒后可再次请求平台）"
                progress.set_text(message)
            except Exception as exc:
                if not content.is_deleted and generation == self.live_generation:
                    progress.set_text("读取失败，请刷新重试")
                    content.clear()
                    with content:
                        self.empty("error_outline", "无法汇总作业", str(exc))
            finally:
                loading = False
                if not refresh.is_deleted:
                    refresh.enable()

        refresh.on_click(load)
        ui.timer(0.01, load, once=True)

    def draw_homework_summary(self, summary, on_verified=None):
        works, courses, errors = summary["works"], summary["courses"], summary["errors"]
        with ui.row().classes("w-full gap-4"):
            for label, value in [
                ("显示作业", len(works)),
                ("未提交 / 其他状态", sum(w["status"] not in SUBMITTED for w in works)),
                ("已提交 / 已完成", sum(w["status"] in SUBMITTED for w in works)),
                ("成功读取课程", f"{len(courses) - len(errors)} / {len(courses)}"),
            ]:
                with ui.column().classes("panel stat gap-1"):
                    ui.label(label).classes("muted text-sm")
                    ui.label(str(value)).classes("stats-value")
        if summary.get("notice"):
            ui.label(summary["notice"]).classes("text-amber-800 text-sm")
        if summary.get("verification_required") and on_verified:
            render_access_verification(self, on_verified)
        if errors:
            with ui.expansion(f"{len(errors)} 门课程读取失败，当前汇总不完整", icon="warning").classes("panel w-full"):
                for error in errors:
                    ui.label(f"{error['course']['name']}：{error['error']}").classes("text-sm")

        def course_key(c):
            return f"{c['courseId']}:{c['classId']}"

        with ui.row().classes("panel w-full items-center gap-3"):
            search = (
                ui.input(placeholder="搜索作业名、课程名…")
                .props("outlined dense clearable debounce=200")
                .classes("grow")
            )
            subject = (
                ui.select(
                    {"": "全部科目", **{course_key(c): f"{c['name']} · {c.get('note', '')}" for c in courses}},
                    value="",
                    label="科目",
                )
                .props("outlined dense options-dense")
                .classes("w-64")
            )
            status = (
                ui.select({"": "全部状态", **{w["status"]: w["status"] for w in works}}, value="", label="作业状态")
                .props("outlined dense")
                .classes("w-44")
            )
        rows = ui.column().classes("w-full gap-3")

        def draw():
            rows.clear()
            query = (search.value or "").strip().casefold()
            visible = [
                w
                for w in works
                if (
                    query in (w["name"] + " " + w["course"]["name"]).casefold()
                    and (not subject.value or course_key(w["course"]) == subject.value)
                    and (not status.value or w["status"] == status.value)
                )
            ]
            with rows:
                if not visible:
                    self.empty(
                        "assignment",
                        "暂无匹配作业" if works else "暂未读取到作业",
                        "可调整筛选条件或刷新全部作业。"
                        if works
                        else ("请先处理上方课程读取错误后重试。" if errors else "当前账号的课程暂无作业。"),
                    )
                    return
                ui.label(f"共 {len(visible)} 项作业").classes("muted text-sm")
                panels = []
                for work in visible:
                    course, completed = work["course"], work["status"] in SUBMITTED
                    with ui.column().classes("panel w-full gap-2") as panel:
                        panels.append(panel)
                        with ui.row().classes("w-full items-center gap-3"):
                            ui.icon("assignment_turned_in" if completed else "assignment", size="24px").classes(
                                "text-teal-700"
                            )
                            ui.label(work["name"]).classes("grow font-semibold homework-list-title")
                            ui.label(work["status"]).classes("pill")
                            ui.button(
                                "作答记录" if completed else "作答",
                                on_click=lambda c=course, w=work, done=completed: self.homework_navigate(
                                    c, "review" if done else "answer", w
                                ),
                            ).props("outline")
                        ui.button(
                            course["name"], icon="menu_book", on_click=lambda c=course: self.homework_navigate(c)
                        ).props("flat dense")
                        for line in work.get("details", []):
                            ui.label(line).classes("muted text-sm")
                self.paginate_panels(panels, 10, "作业汇总")

        search.on_value_change(draw)
        subject.on_value_change(draw)
        status.on_value_change(draw)
        draw()
