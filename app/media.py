from io import BytesIO

from PIL import Image, UnidentifiedImageError

from .providers.base import ProviderError


def image_extension(content):
    try:
        with Image.open(BytesIO(content)) as image:
            return {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}.get(image.format, "img")
    except (UnidentifiedImageError, OSError) as exc:
        raise ProviderError("平台返回的 PPT 图片无法读取") from exc
