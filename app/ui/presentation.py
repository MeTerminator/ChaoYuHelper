"""Presentation loading, slide navigation and file downloads."""

from io import BytesIO

from nicegui import ui

from ..media import image_extension
from ..providers.base import ProviderError
from .components import guarded, rich


class PresentationView:
    async def load_presentation(self, presentation_id):
        if self.live.get("pres_id") == presentation_id:
            return
        generation = self.live_generation
        presentation = await self.live_provider.presentation(presentation_id, lesson_id=self.course["lessonId"])
        if generation != self.live_generation:
            return
        self.live["presentation"] = presentation
        self.live["pres_id"], self.live["index"] = presentation_id, 0

    def draw_slide(self):
        self.slide_region.clear()
        presentation = self.live.get("presentation") or {}
        slides = presentation.get("slides") or []
        with self.slide_region:
            if not slides:
                ui.label("等待教师展示 PPT…").classes("muted")
                return
            index = min(self.live["index"], len(slides) - 1)
            slide = slides[index]
            ui.label(presentation.get("title", "课堂 PPT")).classes("section-title")
            url = slide.get("coverAlt") or slide.get("cover") or slide.get("thumbnail")
            if url:
                image = ui.image(url).props("fit=contain").classes("w-full max-h-[65vh] bg-white")
                ui.button("全屏查看", icon="fullscreen").props("flat").on(
                    "click", js_handler=f"() => document.getElementById('c{image.id}')?.requestFullscreen?.()"
                )
            if slide.get("note"):
                rich(slide["note"])
            with ui.row().classes("w-full items-center justify-center"):

                def move(offset):
                    self.live["index"] = max(0, min(len(slides) - 1, index + offset))
                    self.draw_slide()

                ui.button(icon="chevron_left", on_click=lambda: move(-1)).props("flat round")
                ui.label(f"{index + 1} / {len(slides)}")
                ui.button(icon="chevron_right", on_click=lambda: move(1)).props("flat round")
                ui.button(
                    "保存本页", icon="download", on_click=lambda: guarded(lambda: self.download_slide(url, index + 1))
                ).props("flat").set_enabled(bool(url))
            with ui.row().classes("w-full flex-nowrap overflow-x-auto"):
                for i, s in enumerate(slides):
                    with ui.column().classes("w-20 shrink-0 cursor-pointer") as thumb:
                        thumb.on("click", lambda e, n=i: self.jump_slide(n))
                        ui.image(s.get("thumbnail") or s.get("coverAlt") or s.get("cover") or "").classes(
                            "w-20 h-14 rounded"
                        )
                        ui.label(str(i + 1)).classes("text-xs muted")
            if slide.get("problem"):
                self.rain_problem(slide["problem"])

    def jump_slide(self, index):
        self.live["index"] = index
        self.draw_slide()

    async def download_slide(self, url, number):
        import httpx

        if not url or not url.startswith("https://"):
            raise ProviderError("PPT 图片地址无效")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        extension = image_extension(response.content)
        ui.download(response.content, f"{number:03d}.{extension}")

    async def download_slides(self):
        import zipfile

        import httpx

        slides = self.live.get("presentation", {}).get("slides") or []
        if not slides:
            raise ProviderError("没有可下载的 PPT")
        missing = [
            str(i + 1)
            for i, slide in enumerate(slides)
            if not (slide.get("coverAlt") or slide.get("cover") or slide.get("thumbnail"))
        ]
        if missing:
            raise ProviderError("平台未提供这些页面的图片，无法完整下载 PPT：" + "、".join(missing))
        output = BytesIO()
        count = 0
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for i, slide in enumerate(slides):
                    url = slide.get("coverAlt") or slide.get("cover") or slide.get("thumbnail")
                    if url:
                        if not url.startswith("https://"):
                            raise ProviderError("PPT 图片地址无效")
                        r = await client.get(url)
                        r.raise_for_status()
                        extension = image_extension(r.content)
                        archive.writestr(f"{i + 1:03d}.{extension}", r.content)
                        count += 1
        if not count:
            raise ProviderError("平台没有提供可下载的 PPT 图片")
        ui.download(output.getvalue(), "课堂PPT.zip")
