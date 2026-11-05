"""Administrator forms, course settings and manual batch actions."""

from html import escape

from nicegui import ui

from ..auth import CSRF_COOKIE
from ..providers.base import ProviderError
from ..service import service
from .components import field, guarded, primary


class SettingsView:
    def render_settings(self):
        self.heading("SYSTEM SETTINGS", "系统设置", "管理后台管理员账户与登录密码。")
        with service.store.connect() as db:
            account = db.execute("SELECT username FROM administrator WHERE id=1").fetchone()
        username = escape(account[0] if account else "", quote=True)
        csrf = escape(self.client.request.cookies.get(CSRF_COOKIE, ""), quote=True)
        with ui.column().classes("panel w-full max-w-xl gap-3"):
            ui.label("管理员账户").classes("section-title")
            ui.label("修改后，所有设备需要重新登录。").classes("muted text-sm")
            if self.settings_error:
                ui.label(self.settings_error).classes("text-red-700").props("role=alert")
            ui.html(
                f'''<form class="system-settings-form" method="post" action="/admin/account">
<input type="hidden" name="csrf" value="{csrf}">
<label>管理员名称<input name="username" value="{username}" autocomplete="username" required maxlength="64"></label>
<label>当前密码<input name="current_password" type="password" autocomplete="current-password" required maxlength="128"></label>
<label>新密码<input name="password" type="password" autocomplete="new-password" required minlength="8" maxlength="128"></label>
<label>确认密码<input name="confirmation" type="password" autocomplete="new-password" required minlength="8" maxlength="128"></label>
<button type="submit">保存并重新登录</button></form>''',
                sanitize=False,
            ).classes("w-full")

    def selected_accounts(self):
        return (
            ui.select(
                self.account_options(),
                value=[self.account_id] if self.account_id else [],
                multiple=True,
                label="操作账号",
            )
            .props("outlined dense use-chips")
            .classes("w-full")
        )

    async def batch(self, selected, operation, result_box):
        ids = list(selected.value or [])
        if not ids:
            raise ProviderError("请选择操作账号")
        if self.busy:
            ui.notify("上一项操作尚未完成")
            return
        self.busy = True
        try:
            results = await service.batch(ids, operation)
            result_box.clear()
            with result_box:
                for result in results:
                    ui.label(f"{result['account']}：" + ("成功" if result["ok"] else result["error"])).classes(
                        "text-green-700" if result["ok"] else "text-red-700"
                    )
            ui.notify(f"完成：{sum(r['ok'] for r in results)} / {len(results)} 成功")
            return results
        finally:
            self.busy = False

    def location_fields(self, value=None):
        value = value or {}
        classroom = field("教室（可选）", value.get("classroom", ""))
        address = field("地址", value.get("address", ""))
        with ui.row().classes("w-full no-wrap"):
            latitude = (
                ui.number(
                    "纬度",
                    value=float(value["latitude"]) if value.get("latitude") is not None else None,
                    min=-90,
                    max=90,
                    precision=6,
                )
                .props("outlined dense")
                .classes("w-1/2")
            )
            longitude = (
                ui.number(
                    "经度",
                    value=float(value["longitude"]) if value.get("longitude") is not None else None,
                    min=-180,
                    max=180,
                    precision=6,
                )
                .props("outlined dense")
                .classes("w-1/2")
            )

        async def locate():
            pos = await ui.run_javascript(
                """return await new Promise(resolve => navigator.geolocation.getCurrentPosition(
                p=>resolve({lat:p.coords.latitude,lng:p.coords.longitude}),e=>resolve({error:e.message}),{enableHighAccuracy:true,timeout:15000}))""",
                timeout=20,
            )
            if pos.get("error"):
                raise ProviderError(pos["error"])
            latitude.value, longitude.value = pos["lat"], pos["lng"]

        async def choose_map():
            with ui.dialog() as dialog, ui.card().classes("w-[700px] max-w-full p-5"):
                ui.label("点击地图选择位置").classes("section-title")
                center = (
                    latitude.value if latitude.value is not None else 31.23,
                    longitude.value if longitude.value is not None else 121.47,
                )
                map_view = ui.leaflet(center=center, zoom=15).classes("w-full h-96")
                marker = map_view.marker(latlng=center)
                coords = [*center]

                def clicked(e):
                    coords[:] = [e.args["latlng"]["lat"], e.args["latlng"]["lng"]]
                    marker.move(*coords)

                map_view.on("map-click", clicked)

                def apply():
                    latitude.value, longitude.value = coords
                    dialog.close()

                primary("使用此位置", apply, "check")
            dialog.open()

        with ui.row():
            ui.button("获取当前位置", icon="my_location", on_click=lambda: guarded(locate)).props("outline dense")
            ui.button("地图选点", icon="map", on_click=choose_map).props("flat dense")

        def collect():
            if latitude.value is None and longitude.value is None and not address.value:
                return None
            if latitude.value is None or longitude.value is None or not address.value:
                raise ProviderError("地址、纬度和经度需要完整填写")
            return dict(
                classroom=classroom.value, address=address.value, latitude=latitude.value, longitude=longitude.value
            )

        def apply_location(value):
            classroom.value = value.get("classroom", "")
            address.value = value.get("address", "")
            latitude.value, longitude.value = value.get("latitude"), value.get("longitude")

        collect.set = apply_location
        return collect

    def settings_dialog(self, course):
        settings = service.settings(self.account_id, course)
        images = list(settings.get("imageObjectIds") or [])
        with ui.dialog() as dialog, ui.card().classes("w-[600px] p-7 gap-4"):
            ui.label("课程设置").classes("text-xl font-bold")
            ui.label(course["name"]).classes("muted text-sm")
            location = self.location_fields(settings.get("location"))
            ui.separator()
            ui.label("签到照片").classes("section-title")
            image_box = ui.row().classes("w-full")

            def show_images():
                image_box.clear()
                with image_box:
                    for image in list(images):
                        with ui.column().classes("gap-1"):
                            url = (
                                image
                                if image.startswith("https:")
                                else f"https://p.ananas.chaoxing.com/star4/{image}/origin.jpg"
                            )
                            ui.image(url).classes("w-20 h-20 rounded-lg")

                            def remove(value=image):
                                images.remove(value)
                                show_images()

                            ui.button("移除", on_click=remove).props("flat dense size=sm")

            show_images()

            async def upload(e):
                async def perform():
                    content = await e.file.read()
                    if len(content) > 20 * 1024 * 1024:
                        raise ProviderError("图片请控制在 20 MB 以内")
                    images.append(await self.provider().upload(content, e.file.name))
                    show_images()

                await guarded(perform)

            ui.upload(label="拍照 / 上传图片", on_upload=upload, auto_upload=True).props(
                "accept=image/* max-file-size=20971520"
            ).classes("w-full")

            def save():
                try:
                    service.save_settings(self.account_id, course, {"location": location(), "imageObjectIds": images})
                    dialog.close()
                    ui.notify("设置已保存", type="positive")
                except Exception as exc:
                    ui.notify(str(exc), type="negative")

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat")
                primary("保存设置", save, "save")
        dialog.open()
