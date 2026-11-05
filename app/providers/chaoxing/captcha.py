"""Slide captcha negotiation and verification."""

import json
import re
import uuid

from ...crypto import (
    CAPTCHA_ID,
    dumps,
    md5,
    ms,
)
from ..base import ProviderError


class ChaoxingCaptcha:
    async def captcha_start(self, referer):
        async def jsonp(url, params):
            r = await self.request(url, params={"callback": "cx_captcha_function", **params}, raw=True)
            match = re.search(r"cx_captcha_function\((.*)\)", r.text, re.S)
            if not match:
                raise ProviderError("验证码响应解析失败")
            return json.loads(match[1])

        self.captcha_jsonp = jsonp
        self.captcha_time = ms()
        conf = await jsonp(
            "https://captcha.chaoxing.com/captcha/get/conf", {"captchaId": CAPTCHA_ID, "_": self.captcha_time}
        )
        service_time = conf["t"]
        key = md5(f"{service_time}{uuid.uuid4()}")
        token = f"{md5(f'{service_time}{CAPTCHA_ID}slide{key}')}:{service_time + 300000}"
        self.captcha_iv = md5(f"{CAPTCHA_ID}slide{self.captcha_time}{uuid.uuid4()}")
        return await jsonp(
            "https://captcha.chaoxing.com/captcha/get/verification/image",
            {
                "captchaId": CAPTCHA_ID,
                "type": "slide",
                "version": "1.1.20",
                "captchaKey": key,
                "token": token,
                "referer": referer,
                "iv": self.captcha_iv,
                "_": self.captcha_time + 1,
            },
        )

    async def captcha_submit(self, x, token, referer):
        r = await self.request(
            "https://captcha.chaoxing.com/captcha/check/verification/result",
            params={
                "callback": "cx_captcha_function",
                "captchaId": CAPTCHA_ID,
                "type": "slide",
                "token": token,
                "textClickArr": dumps([{"x": round(x)}]),
                "coordinate": "[]",
                "runEnv": "10",
                "version": "1.1.20",
                "t": "a",
                "iv": self.captcha_iv,
                "_": self.captcha_time + 2,
            },
            headers={"Referer": referer},
            raw=True,
        )
        match = re.search(r"cx_captcha_function\((.*)\)", r.text, re.S)
        d = json.loads(match[1]) if match else {}
        if d.get("error") == 0 and d.get("result"):
            return json.loads(d["extraData"])["validate"]
        raise ProviderError("验证未通过，请重新拖动滑块")
