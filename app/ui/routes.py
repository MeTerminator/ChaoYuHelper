"""课程助手浏览器界面。"""

from nicegui import app, ui

from ..providers.base import ProviderError
from ..service import service
from .dashboard import Dashboard


@ui.page("/")
def index(section: str = "courses", error: str = ""):
    Dashboard(section=section, settings_error=error).build()


async def render_homework_route(account, course, class_id, work_id="", mode=""):
    dashboard = Dashboard()
    # Resolve page context from the account's cached courses, never from arbitrary URLs.
    accounts = {item["id"]: item for item in service.accounts("chaoxing")}
    dashboard.platform = "chaoxing"
    dashboard.account_id = account if account in accounts else ""
    if account in accounts:
        app.storage.user["platform"] = "chaoxing"
        app.storage.user["account_id"] = account
    dashboard.build(render=False)
    with dashboard.body:
        try:
            if account not in accounts:
                raise ProviderError("作业账号不存在，请返回课程重新选择账号")
            selected = next(
                (
                    item
                    for item in service.store.get("courses", account, [])
                    if str(item["courseId"]) == course and str(item["classId"]) == class_id
                ),
                None,
            )
            if selected is None:
                raise ProviderError("课程不存在，请返回课程刷新列表")
            if not mode:
                await dashboard.homework_list_page(selected)
                return
            generation = dashboard.live_generation
            provider = dashboard.provider()
            works = await provider.homework_list(selected)
            if generation != dashboard.live_generation:
                return
            work = next((item for item in works if str(item["id"]) == work_id), None)
            if work is None:
                raise ProviderError("作业不存在，请返回课程作业刷新列表")
            if mode == "review":
                await dashboard.homework_review_page(selected, work)
            else:
                dashboard.homework_start_page(selected, provider, account, work)
        except Exception as exc:
            dashboard.empty("error_outline", "无法打开课程作业", str(exc))
            ui.button("返回课程", on_click=lambda: ui.navigate.to("/")).props("flat")


@ui.page("/homework")
async def homework_page(account: str, course: str, class_id: str):
    await render_homework_route(account, course, class_id)


@ui.page("/homework/review")
async def homework_review_page(account: str, course: str, class_id: str, work_id: str):
    await render_homework_route(account, course, class_id, work_id, "review")


@ui.page("/homework/answer")
async def homework_answer_page(account: str, course: str, class_id: str, work_id: str):
    await render_homework_route(account, course, class_id, work_id, "answer")
