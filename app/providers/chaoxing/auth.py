"""Learning-platform login and profile protocols."""

import json
import uuid
from urllib.parse import urlencode

from ...crypto import (
    APP_KEY,
    WEB_KEY,
    aes,
    des_decrypt,
    dumps,
    md5,
    ms,
    rsa_encrypt,
    rsa_public_decrypt,
)
from ..base import ProviderError, checked


class ChaoxingAuth:
    async def login(self, username, password, mode="password", **kwargs):
        if mode == "web":
            checked(
                await self.request(
                    "https://passport2.chaoxing.com/fanyalogin",
                    "POST",
                    data={
                        "fid": "-1",
                        "uname": aes(username, WEB_KEY, True),
                        "password": aes(password, WEB_KEY, True),
                        "t": "true",
                        "forbidotherlogin": "0",
                        "validate": "",
                    },
                )
            )
        else:
            result = checked(
                await self.request(
                    "https://passport2-api.chaoxing.com/v11/loginregister?cx_xxt_passport=json",
                    "POST",
                    data={
                        "logininfo": aes(dumps({"uname": username, "code": password}), APP_KEY),
                        "loginType": "2" if mode == "sms" else "1",
                        "roleSelect": "true",
                        "entype": "1",
                        **({"countrycode": "86"} if mode == "sms" else {}),
                    },
                )
            )
            if result.get("status") and not result.get("url") and mode != "sms":
                raise ProviderError("新设备需要安全验证，请改用短信验证码登录")
        return await self.finish_login()

    async def send_code(self, phone, **kwargs):
        timestamp = str(ms())
        return checked(
            await self.request(
                "https://passport2-api.chaoxing.com/api/sendcaptcha",
                "POST",
                data={
                    "to": phone,
                    "countrycode": "86",
                    "time": timestamp,
                    "enc": md5(phone + "jsDyctOCnay7uotq" + timestamp),
                },
            )
        )

    async def profile(self):
        device = uuid.uuid4().hex
        info = dict(
            app_name="com.chaoxing.mobile",
            app_ver="6.7.4",
            board="caiman",
            brand="google",
            cdid=device,
            cdtype="Pixel 9 Pro",
            cpu_ar="arm64-v8a,armeabi-v7a,armeabi",
            device_id=device,
            dpi="440",
            hardware="caiman",
            mediaDrmId="",
            oaid="1004",
            os_lang="",
            os_name="REL",
            os_ver="16",
            platform="android",
            resolution="1080*2243",
            time_stamp=ms(),
        )
        r = checked(
            await self.request(
                "https://sso.chaoxing.com/apis/login/userLogin4Uname.do",
                "POST",
                data={"data": rsa_encrypt(dumps(info))},
            )
        )
        d = r.get("msg")
        if not isinstance(d, dict) or not d.get("puid"):
            raise ProviderError("登录未完成或会话已过期")
        im = d.get("accountInfo", {}).get("imAccount") or {}
        return dict(
            uid=str(d["puid"]),
            name=d.get("name", ""),
            avatar=d.get("pic", ""),
            phone=d.get("phone", ""),
            school=d.get("schoolname", ""),
            deviceInfo=json.loads(rsa_public_decrypt(d["clientId"])) if d.get("clientId") else {},
            imAccount={
                "userName": im.get("username", ""),
                "password": des_decrypt(im["password"]) if im.get("password") else "",
            },
        )

    async def qr_start(self):
        d = checked(await self.request("https://passport2.chaoxing.com/refreshQRCode", "POST"))
        return {
            **d,
            "image": f"https://passport2.chaoxing.com/createqr?{urlencode({'uuid': d['uuid'], 'fid': '-1', 'type': '3'})}",
        }

    async def qr_check(self, qr):
        d = await self.request(
            "https://passport2.chaoxing.com/getauthstatus/v2",
            "POST",
            data={"uuid": qr["uuid"], "enc": qr["enc"], "doubleFactorLogin": "0", "forbidotherlogin": "0"},
        )
        if d.get("status") is True:
            return await self.finish_login()
        return None
