"""Account management and interactive platform login."""

from nicegui import app, ui

from ..providers.base import ProviderError
from ..service import service
from .components import field, guarded, primary
from .constants import PLATFORMS, SERVERS


class AccountsView:
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
                    await service.exams.remove(account["id"])
                    await service.im.remove(account["id"])
                    await service.im_images.remove(account["id"])
                    existing = service.providers.pop(account["id"], None)
                    service.providers[account["id"]] = provider
                    if provider.platform == "chaoxing":
                        service.im.maintain(account["id"])
                    if existing:
                        await existing.close()
                    dialog.close()
                    self.account_id = account["id"]
                    app.storage.user["account_id"] = account["id"]
                    self.account_select.set_options(self.account_options(), value=account["id"])
                    await self.change_account(account["id"])
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
