"""Camera access and QR scanning."""

from io import BytesIO
from urllib.parse import parse_qs, urlparse

from nicegui import ui

from ..providers.base import ProviderError
from .components import field, guarded, primary


class CaptureView:
    def camera_dialog(self, callback):
        import base64

        import zxingcpp
        from PIL import Image

        with ui.dialog() as dialog, ui.card().classes("w-[520px] p-6"):
            ui.label("相机扫码").classes("section-title")
            ui.html(
                '<video id="course-camera" autoplay playsinline style="width:100%;border-radius:12px"></video>',
                sanitize=False,
            )
            status = ui.label("启动后请将二维码对准镜头；也可以上传二维码照片。").classes("muted")
            running = {"value": False}

            def decode(data):
                image = Image.open(BytesIO(data))
                result = zxingcpp.read_barcode(image)
                return result.text if result else ""

            async def uploaded(e):
                async def work():
                    result = decode(await e.file.read())
                    if not result:
                        raise ProviderError("图片中未识别到二维码，请使用清晰原图")
                    callback(result)
                    dialog.close()

                await guarded(work)

            ui.upload(label="上传二维码图片 / 拍照", on_upload=uploaded, auto_upload=True).props(
                "accept=image/* max-file-size=20971520"
            ).classes("w-full")

            async def capture():
                if not running["value"]:
                    return
                frame = await ui.run_javascript("""return (()=>{
                    const v=document.getElementById('course-camera');
                    if(!v?.videoWidth)return null;
                    const c=document.createElement('canvas');c.width=v.videoWidth;c.height=v.videoHeight;
                    c.getContext('2d').drawImage(v,0,0);return c.toDataURL('image/jpeg',.8).split(',')[1];})()""")
                if frame:
                    result = decode(base64.b64decode(frame))
                    if result:
                        running["value"] = False
                        callback(result)
                        dialog.close()

            timer = ui.timer(0.6, lambda: guarded(capture), active=False)

            async def start():
                result = await ui.run_javascript(
                    """return await (async()=>{
                    try{
                        window.courseCameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});
                        const video=document.getElementById('course-camera');video.srcObject=window.courseCameraStream;
                        await video.play();return {ok:true};
                    }catch(e){return {error:e.message}}})()""",
                    timeout=20,
                )
                if result.get("error"):
                    status.text = result["error"]
                else:
                    running["value"] = True
                    timer.activate()

            async def stop():
                running["value"] = False
                timer.deactivate()
                await ui.run_javascript("window.courseCameraStream?.getTracks().forEach(t=>t.stop())")

            dialog.on("hide", stop)
            primary("启动相机", lambda: guarded(start), "videocam")
            ui.button("关闭", on_click=dialog.close).props("flat")
        dialog.open()

    def scan_dialog(self):
        with ui.dialog() as dialog, ui.card().classes("w-[560px] p-6 gap-4"):
            ui.label("扫描课堂二维码").classes("text-xl font-bold")
            selected = self.selected_accounts()
            value = field("粘贴二维码内容 / URL")
            ui.button(
                "使用相机",
                icon="qr_code_scanner",
                on_click=lambda: self.camera_dialog(lambda v: setattr(value, "value", v)),
            ).props("outline")
            results = ui.column()

            async def submit():
                if not value.value:
                    raise ProviderError("请扫描或填写二维码内容")
                if self.platform == "rainClassroom":
                    await self.batch(selected, lambda p: p.scan(value.value), results)
                else:
                    params = parse_qs(urlparse(value.value).query)
                    aid = (params.get("activeId") or params.get("id") or [""])[0]
                    if not aid:
                        raise ProviderError("未找到活动 ID，请从课程活动中打开签到")
                    dialog.close()
                    await self.sign_dialog(
                        {"courseId": (params.get("courseId") or [""])[0], "classId": ""},
                        {
                            "id": aid,
                            "type": 2,
                            "name": "二维码签到",
                            "signType": "2",
                            "url": value.value,
                            "_qr": value.value,
                        },
                    )

            primary("打开 / 提交扫码", lambda: guarded(submit), "qr_code")
            ui.button("关闭", on_click=dialog.close).props("flat")
        dialog.open()
