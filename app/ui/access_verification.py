"""Account-bound access captcha input; the user reads and enters the image code."""

from nicegui import ui

from ..homework_summary import start_verification, submit_verification
from ..service import service


def render_access_verification(dashboard, on_verified, *, autoload=True):
    generation, account_id = dashboard.live_generation, dashboard.account_id
    state = {"token": "", "busy": False}
    with ui.column().classes("panel w-full gap-3") as panel:
        ui.label("学习通访问验证").classes("text-lg font-semibold")
        ui.label("请输入图片中的四位字母或数字，通过后会自动继续读取作业。").classes("muted text-sm")
        with ui.row().classes("items-center gap-3"):
            picture = ui.image().props("fit=contain no-transition").style("width: 156px; height: 66px")
            refresh = ui.button("换一张", icon="refresh").props("flat")
        code = (
            ui.input("图片验证码", placeholder="四位字母或数字")
            .props("outlined dense maxlength=4 autocomplete=off")
            .classes("w-64")
        )
        submit = ui.button("验证并继续", icon="verified_user").classes("primary-btn")
        status = ui.label("正在加载验证码…" if autoload else "点击“换一张”加载验证码").classes("muted text-sm")

    def active():
        return not panel.is_deleted and generation == dashboard.live_generation and account_id == dashboard.account_id

    def busy(value):
        state["busy"] = value
        for element in (refresh, submit, code):
            element.set_enabled(not value)

    async def load(refresh_image=False):
        if state["busy"] or not active():
            return
        busy(True)
        try:
            challenge = await start_verification(service, account_id, refresh_image)
            if not active():
                return
            state["token"] = challenge["token"]
            picture.set_source(challenge["image"])
            code.set_value("")
            status.set_text("看不清可点击“换一张”；请勿与其他页面同时刷新验证码。")
        except Exception as exc:
            if active():
                state["token"] = ""
                status.set_text(str(exc))
        finally:
            if active():
                busy(False)

    async def verify():
        if state["busy"] or not active():
            return
        if not state["token"]:
            status.set_text("请先点击“换一张”加载验证码")
            return
        busy(True)
        status.set_text("正在验证…")
        try:
            await submit_verification(service, account_id, state["token"], code.value or "")
            if active():
                status.set_text("验证通过，正在继续读取作业…")
                await on_verified()
        except Exception as exc:
            if active():
                status.set_text(str(exc))
        finally:
            if active():
                busy(False)

    refresh.on_click(lambda: load(True))
    submit.on_click(verify)
    code.on("keydown.enter", verify)
    if autoload:
        ui.timer(0.01, load, once=True)
