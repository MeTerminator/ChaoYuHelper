"""Human-entered access captcha, using the same account's official web session."""

import base64
import re
import secrets
import time
from io import BytesIO
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image

from ..base import PlatformVerificationRequired, ProviderError

HOST = "https://mooc1.chaoxing.com"


class ChaoxingAccessVerification:
    def check_homework_verification(self, response, course):
        from ...homework import document

        try:
            document(response.text)
        except PlatformVerificationRequired:
            self.access_verification_course = dict(course)
            raise

    async def access_verification_start(self, refresh=False):
        current = getattr(self, "access_verification", None)
        if current and not refresh and current["expires"] > time.monotonic():
            return {k: current[k] for k in ("token", "image")}
        # A refreshed image invalidates previous codes, including other open tabs.
        self.access_verification = None
        page = await self.request(HOST + "/antispiderShowVerify.ac", raw=True)
        soup = BeautifulSoup(page.text, "html.parser")
        form = soup.select_one("form")
        if not form or not form.select_one('input[name="ucode"]'):
            raise ProviderError("平台未提供图片验证码，请刷新作业后重试")
        action = urljoin(str(page.url), form.get("action", ""))
        parsed = urlsplit(action)
        if (parsed.scheme, parsed.netloc, parsed.path) != ("https", "mooc1.chaoxing.com", "/html/processVerify.ac"):
            raise ProviderError("平台返回了不支持的验证码提交地址")
        method = form.get("method", "GET").upper()
        if method not in ("GET", "POST"):
            raise ProviderError("平台返回了不支持的验证码提交方式")
        fields = {node["name"]: node.get("value", "") for node in form.select('input[type="hidden"][name]')}
        if set(fields) - {"app"}:
            raise ProviderError("平台验证码表单已变化，请更新适配")
        picture = await self.request(
            HOST + "/processVerifyPng.ac",
            params={"t": secrets.randbelow(2147483647)},
            headers={"Referer": str(page.url)},
            raw=True,
        )
        if not picture.content or len(picture.content) > 512 * 1024:
            raise ProviderError("验证码图片大小异常，请刷新图片")
        try:
            with Image.open(BytesIO(picture.content)) as img:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}.get(img.format)
                if not mime or img.width * img.height > 1_000_000:
                    raise ValueError("invalid image")
                img.verify()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ProviderError("无法读取平台验证码图片，请刷新图片") from exc
        self.access_verification = {
            "token": secrets.token_urlsafe(24),
            "expires": time.monotonic() + 180,
            "image": f"data:{mime};base64," + base64.b64encode(picture.content).decode(),
            "action": action,
            "method": method,
            "fields": fields,
            "referer": str(page.url),
        }
        return {k: self.access_verification[k] for k in ("token", "image")}

    async def access_verification_submit(self, token, code):
        from ...homework import document

        if not re.fullmatch(r"[0-9a-zA-Z]{4}", str(code).strip()):
            raise ProviderError("请输入图片中的四位字母或数字")
        current = getattr(self, "access_verification", None)
        if (
            not current
            or current["expires"] <= time.monotonic()
            or not secrets.compare_digest(current["token"], str(token))
        ):
            raise ProviderError("验证码图片已过期或已在其他页面刷新，请刷新图片")
        course = getattr(self, "access_verification_course", None)
        if course is None:
            course = next(iter(self.store.get("courses", self.account.get("id", ""), [])), None)
        if course is None:
            raise ProviderError("缺少验证后的课程检查入口，请先刷新课程")
        self.access_verification = None
        fields = {**current["fields"], "ucode": code.strip()}
        await self.request(
            current["action"],
            current["method"],
            **({"params": fields} if current["method"] == "GET" else {"data": fields}),
            headers={"Referer": current["referer"]},
            raw=True,
        )
        # A 200 or redirect alone does not prove success. Recheck a read-only course
        # entry, never a task/answer URL that might begin a timed attempt.
        probe = await self.request(
            HOST + "/visit/stucoursemiddle",
            params={
                "courseid": course["courseId"],
                "clazzid": course["classId"],
                "cpi": course.get("cpi", ""),
                "ismooc2": "1",
            },
            raw=True,
        )
        try:
            soup = document(probe.text)
        except PlatformVerificationRequired as exc:
            raise ProviderError("验证码错误或已过期，请刷新图片后重新输入") from exc
        work_enc = soup.select_one("#workEnc")
        if not work_enc or not work_enc.get("value") or not soup.select_one('a[data-url*="/work/list"]'):
            raise ProviderError("验证后仍无法访问课程，请确认账号登录状态后重试")
        return True
