"""Manual check-in forms and slide captcha."""

from urllib.parse import parse_qs, urlparse

from nicegui import ui

from ..providers.base import ProviderError
from ..service import service
from .components import field, guarded, primary
from .constants import SIGN_TYPES


class SignView:
    async def sign_dialog(self, course, active, group=False):
        detail = await self.provider().sign_detail(active["id"], group)
        info = detail.get("data", detail)
        info = dict(info) if isinstance(info, dict) else {}
        if not group:
            info.update(await self.provider().active_info(active["id"]))
        settings = service.settings(self.account_id, course)
        with ui.dialog() as dialog, ui.card().classes("w-[680px] p-7 gap-4"):
            ui.label(active.get("name") or "签到").classes("text-xl font-bold")
            ui.label("提交后将显示每个账号的结果；验证码和人脸参数按账号分别处理。").classes("muted text-xs")
            selected = self.selected_accounts()
            kind = (
                ui.select(SIGN_TYPES, value=str(active.get("signType", "0")), label="签到方式")
                .props("outlined dense")
                .classes("w-full")
            )
            code = field("签到码 / 手势序列（九宫格 1–9）")
            with ui.expansion("绘制九宫格手势", icon="gesture").classes("w-full") as gesture:
                points = []
                pattern_label = ui.label("尚未选择")

                def add_point(value):
                    if value not in points:
                        points.append(value)
                        code.value = "".join(points)
                        pattern_label.text = " → ".join(points)

                with ui.grid(columns=3).classes("gap-4 mx-auto"):
                    for number in range(1, 10):
                        ui.button(str(number), on_click=lambda n=str(number): add_point(n)).props("round outline")

                def clear_pattern():
                    points.clear()
                    code.value = ""
                    pattern_label.text = "尚未选择"

                ui.button("重置", on_click=clear_pattern).props("flat dense")
            qr_value = field("二维码内容 / enc", active.get("_qr", ""))
            scan = ui.button(
                "相机扫码",
                icon="qr_code_scanner",
                on_click=lambda: self.camera_dialog(lambda value: setattr(qr_value, "value", value)),
            ).props("flat dense")
            photo = (
                ui.select(
                    settings.get("imageObjectIds") or [],
                    label="照片 Object ID（可选）",
                    with_input=True,
                    new_value_mode="add-unique",
                )
                .props("outlined dense")
                .classes("w-full")
            )
            per_account_photos = {}
            with ui.expansion("分别设置各账号的照片", icon="collections").classes("w-full") as account_photos:
                for account_id, label in self.account_options().items():
                    per_account_photos[account_id] = (
                        ui.select(
                            settings.get("imageObjectIds") or [],
                            label=label,
                            with_input=True,
                            new_value_mode="add-unique",
                        )
                        .props("outlined dense")
                        .classes("w-full")
                    )

            async def upload(e):
                async def work():
                    oid = await self.provider().upload(await e.file.read(), e.file.name)
                    photo.set_options([*photo.options, oid], value=oid)

                await guarded(work)

            photo_upload = (
                ui.upload(label="拍照 / 选择签到照片", on_upload=upload, auto_upload=True)
                .props("accept=image/* max-file-size=20971520")
                .classes("w-full")
            )
            with ui.expansion("位置信息", icon="location_on", value=True).classes("w-full") as location:
                collect_location = self.location_fields(settings.get("location"))
            use_face = ui.checkbox("使用账号已采集的人脸进行校验", value=info.get("openCheckFaceFlag") == 1)
            needs_photo = info.get("ifPhoto", info.get("ifphoto")) == 1
            use_face.set_visibility(info.get("openCheckFaceFlag") == 1)

            def update_sign_fields():
                code.set_visibility(kind.value in ("3", "5"))
                code.set_label("手势序列（九宫格 1–9）" if kind.value == "3" else "签到码")
                gesture.set_visibility(kind.value == "3")
                qr_value.set_visibility(kind.value == "2")
                scan.set_visibility(kind.value == "2")
                for control in (photo, account_photos, photo_upload):
                    control.set_visibility(needs_photo)
                location.set_visibility(kind.value == "4")

            kind.on_value_change(update_sign_fields)
            update_sign_fields()
            with ui.expansion("签到详情 / 参与列表").classes("w-full"):
                ui.label(
                    "签到方式：" + SIGN_TYPES.get(str(info.get("otherId", active.get("signType", "0"))), "由平台指定")
                )
                ui.label("需要照片：" + ("是" if info.get("ifPhoto", info.get("ifphoto")) == 1 else "否 / 未指定"))
                for key, label in [
                    ("address", "指定地址"),
                    ("locationText", "位置要求"),
                    ("locationRange", "签到范围"),
                ]:
                    if info.get(key):
                        ui.label(f"{label}：{info[key]}")

                async def attendees():
                    response = await self.provider().attendees(active["id"], group)
                    data = response.get("data") or response
                    if not isinstance(data, dict):
                        raise ProviderError("平台未返回参与列表")
                    signed = data.get("yiqianList") or data.get("signed") or []
                    unsigned = data.get("weiqianList") or data.get("unsigned") or []
                    with ui.dialog() as participants, ui.card().classes("w-[760px] p-6 gap-3"):
                        ui.label(f"参与列表 · 已签到 {len(signed)} 人 · 未签到 {len(unsigned)} 人").classes(
                            "section-title"
                        )
                        for person in signed:
                            with ui.row().classes("activity-row"):
                                ui.label(person.get("name") or str(person.get("uid", ""))).classes("grow")
                                ui.label(str(person.get("title") or "已签到")).classes("muted text-xs")
                                if person.get("title"):

                                    def apply(value=person):
                                        if value.get("longitude") and value.get("latitude"):
                                            collect_location.set(
                                                {
                                                    "address": value["title"],
                                                    "latitude": value["latitude"],
                                                    "longitude": value["longitude"],
                                                }
                                            )
                                        else:
                                            photo.set_options(
                                                list(dict.fromkeys([*photo.options, value["title"]])),
                                                value=value["title"],
                                            )
                                        participants.close()

                                    ui.button("填入签到信息", on_click=apply).props("flat dense")
                        if unsigned:
                            ui.label(
                                "未签到：" + "、".join(p.get("name") or str(p.get("uid", "")) for p in unsigned)
                            ).classes("muted")
                        if not signed and not unsigned:
                            ui.label("平台未提供可显示的参与者信息。").classes("muted")
                        ui.button("关闭参与列表", on_click=participants.close)
                    participants.open()

                ui.button("查看参与列表", on_click=lambda: guarded(attendees)).props("outline")
            results = ui.column().classes("w-full")

            async def submit():
                loc = (collect_location() or {}) if kind.value == "4" else {}
                raw = (qr_value.value or "") if kind.value == "2" else ""
                query = parse_qs(urlparse(raw).query)
                enc = query.get("enc", [raw])[0]

                async def operation(provider):
                    face = await provider.face(active["id"]) if use_face.value else {}
                    validation = ""
                    if info.get("showVCode") == 1:
                        async with self.captcha_lock:
                            validation = await self.slide_captcha(provider, active.get("url", ""))
                        if not validation:
                            raise ProviderError("已取消验证码验证")
                    params = dict(
                        code=(code.value or "") if kind.value in ("3", "5") else "",
                        enc=enc,
                        object_id=(per_account_photos.get(provider.account.get("id"), photo).value or photo.value or "")
                        if needs_photo
                        else "",
                        address=loc.get("address", ""),
                        latitude=loc.get("latitude"),
                        longitude=loc.get("longitude"),
                        group=group,
                        **face,
                    )
                    if kind.value == "0" and info.get("ifPhoto", info.get("ifphoto")) == 1 and not params["object_id"]:
                        raise ProviderError("本次签到需要照片，请先上传或选择照片")
                    try:
                        return await provider.sign(course, active["id"], kind.value, **params, validate=validation)
                    except ProviderError as exc:
                        message = str(exc)
                        if message.startswith("validate"):
                            async with self.captcha_lock:
                                validation = await self.slide_captcha(provider, active.get("url", ""))
                            if not validation:
                                raise ProviderError("已取消验证码验证")
                            return await provider.sign(
                                course,
                                active["id"],
                                kind.value,
                                **params,
                                validate=validation,
                                enc2=message.removeprefix("validate_") if "_" in message else "",
                            )
                        raise

                await self.batch(selected, operation, results)

            with ui.row().classes("w-full justify-end"):
                ui.button("关闭", on_click=dialog.close).props("flat")
                primary("提交签到", lambda: guarded(submit), "check_circle")
        dialog.open()

    async def slide_captcha(self, provider, referer):
        data = await provider.captcha_start(referer)
        image = data["imageVerificationVo"]
        with ui.dialog() as dialog, ui.card().classes("p-6"):
            ui.label("拖动滑块完成验证").classes("section-title")
            with ui.element("div").style("width:320px;height:160px;position:relative"):
                ui.image(image["shadeImage"]).style("position:absolute;width:320px;height:160px")
                piece = ui.image(image["cutoutImage"]).style("position:absolute;width:56px;height:160px;left:0")
            slider = ui.slider(min=0, max=264, value=0).classes("w-80")
            slider.on_value_change(lambda e: piece.style(f"left:{e.value}px"))

            async def verify():
                x = (slider.value * 280 / 264 - 8) * (image.get("originalWidth", 320) / 320)
                value = await provider.captcha_submit(x, data["token"], referer)
                dialog.submit(value)

            with ui.row():
                ui.button("取消", on_click=lambda: dialog.submit(None)).props("flat")
                primary("验证", lambda: guarded(verify), "check")
        return await dialog
