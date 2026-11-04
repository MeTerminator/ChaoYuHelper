import asyncio
import base64
import json
import re

import websockets
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from ..crypto import ms
from .base import SERVERS, Provider, ProviderError, checked


class Rain(Provider):
    platform = "rainClassroom"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.login_mode = self.account.get("loginMode", "app")
        self.lesson_tokens = {}
        self.lesson_locks = {}
        self.client.headers.update(
            {
                "user-agent": "Android",
                "brand": "google Pixel 9 Pro",
                "uuid": "",
                "buildnumber": "1610",
                "xtua": "client=app&tag=1.3.3&platform=Android",
                "systemversion": "16",
                "incremental": "14624737",
                "accept": "application/json",
                "isphysicaldevice": "true",
                "xtbz": "ykt",
                "x-client": "web" if self.login_mode == "web" else "app",
            }
        )

    def account_metadata(self):
        return {"loginMode": self.login_mode}

    def set_login_mode(self, mode):
        self.login_mode = mode
        self.client.headers["x-client"] = "web" if mode == "web" else "app"

    def import_cookies(self, cookie_string):
        super().import_cookies(cookie_string)
        self.set_login_mode("web")

    @property
    def auth(self):
        if not self.bearer:
            raise ProviderError("请先进入课堂")
        return {"authorization": "Bearer " + self.bearer}

    def auth_for(self, lesson_id=None):
        if lesson_id is None:
            return self.auth
        context = self.lesson_tokens.get(str(lesson_id))
        if not context:
            raise ProviderError("请先进入对应课堂")
        return {"authorization": "Bearer " + context[0]}

    async def ensure_lesson(self, lesson_id):
        lesson_id = str(lesson_id)
        async with self.lesson_locks.setdefault(lesson_id, asyncio.Lock()):
            if lesson_id not in self.lesson_tokens:
                await self.checkin(lesson_id)

    async def login(self, username, password, mode="password", ticket="", rand="", **kwargs):
        self.set_login_mode("web" if mode == "web" else "app")
        # A read-only bootstrap establishes csrftoken before authenticated POSTs.
        if "csrftoken" not in self.client.build_request("GET", SERVERS[self.server] + "/").headers.get("cookie", ""):
            await self.request("/v/course_meta/user_info")
        if mode == "web":
            response = checked(await self.request("/pc/register/get_pws_public_key/"))
            public_key = response["data"]["public_key"]
            cipher = PKCS1_v1_5.new(RSA.import_key(public_key))
            encrypted = base64.b64encode(cipher.encrypt(password.encode())).decode()
            checked(
                await self.request(
                    "/pc/login/verify_pwd_login/",
                    "POST",
                    json={
                        "type": "E" if "@" in username else "PP",
                        "name": username,
                        "pwd": encrypted,
                        "ticket": ticket,
                        "randstr": rand,
                        "hcaptcha_token": "",
                    },
                    headers={"Referer": SERVERS[self.server] + "/web", "Origin": SERVERS[self.server]},
                )
            )
            return await self.finish_login()
        data = dict(
            type=3 if mode == "sms" else (2 if "@" in username else 1),
            phoneNumber="",
            password="",
            email="",
            code="",
            pushDeviceId=self.device_id,
            ticket=ticket,
            rand=rand,
        )
        data["email" if "@" in username and mode != "sms" else "phoneNumber"] = username
        data["code" if mode == "sms" else "password"] = password
        if mode == "sms":
            checked(
                await self.request(
                    "/api/v3/user/code/verify", "POST", json={"phoneNumber": username, "email": "", "code": password}
                )
            )
        checked(await self.request("/api/v3/user/login/app", "POST", json=data))
        return await self.finish_login()

    async def send_code(self, phone, ticket="", rand=""):
        return checked(
            await self.request(
                "/api/v3/user/code/send",
                "POST",
                json={"phoneNumber": phone, "email": "", "ticket": ticket, "rand": rand},
            )
        )

    async def profile(self):
        d = checked(await self.request("/v/course_meta/user_info"))
        p = (d.get("data") or {}).get("user_profile")
        if not p or not p.get("user_id"):
            raise ProviderError("会话无效，请重新登录")
        return dict(
            uid=str(p["user_id"]),
            name=p.get("name", ""),
            avatar=p.get("avatar") or p.get("avatar_96", ""),
            phone=p.get("phone_number", ""),
            school=p.get("school", ""),
        )

    async def qr_start(self):
        d = checked(await self.request("/api/v3/user/login/pre-info"))["data"]
        return {**d, "image": d["qrImage"]}

    async def qr_check(self, qr):
        d = await self.request("/api/v3/user/login", "POST", json={"token": qr["token"]})
        if d.get("code") == 0:
            return await self.finish_login()
        if d.get("code") == 50001:
            return None
        checked(d)

    async def wechat_start(self):
        d = checked(await self.request("/api/v3/user/login/wechat-auth-param", "POST", json={}))["data"]
        r = await self.request(
            "https://open.weixin.qq.com/connect/qrconnect",
            params={
                "appid": d["appId"],
                "scope": "snsapi_login",
                "redirect_uri": d["redirectUri"] + "?path=%2Fauthorize%2Fwx-qrlogin%3Fsuccess%3D1",
                "state": d["state"],
                "login_type": "jssdk",
                "self_redirect": "true",
                "f": "xml",
            },
            raw=True,
        )
        match = re.search(r"<uuid><!\[CDATA\[(.+?)\]\]></uuid>", r.text, re.DOTALL)
        if not match:
            raise ProviderError("微信二维码生成失败")
        return {"uuid": match[1], "state": d["state"], "image": "https://open.weixin.qq.com/connect/qrcode/" + match[1]}

    async def wechat_check(self, qr):
        r = await self.request(
            "https://lp.open.weixin.qq.com/connect/l/qrconnect", params={"uuid": qr["uuid"]}, raw=True
        )
        code = re.search(r"window\.wx_errcode=(\d+)", r.text)
        auth = re.search(r"window\.wx_code='(.+?)'", r.text)
        if code and code[1] == "405" and auth:
            await self.request(
                "/api/v3/user/login/wechat-web-callback",
                params={"path": "/authorize/wx-qrlogin?success=1", "code": auth[1], "state": qr["state"]},
                raw=True,
            )
            self.set_login_mode("web")
            return await self.finish_login()
        if code and code[1] == "402":
            raise ProviderError("微信二维码已过期，请刷新")
        return None

    async def courses(self):
        all_courses, on_lesson = await asyncio.gather(
            self.request("/v/course_meta/learning_list/"), self.request("/api/v3/classroom/on-lesson")
        )
        checked(all_courses)
        checked(on_lesson)
        lessons = {
            str(i["courseId"]): str(i["lessonId"]) for i in on_lesson.get("data", {}).get("onLessonClassrooms", [])
        }
        return [
            dict(
                courseId=str(c["course_id"]),
                classId=str(c["classroom_id"]),
                name=c.get("course_name", ""),
                image=(c.get("teacher") or {}).get("avatar", ""),
                teacher=(c.get("teacher") or {}).get("name", ""),
                note=c.get("classroom_name", ""),
                lessonId=lessons.get(str(c["course_id"]), ""),
                ongoing=str(c["course_id"]) in lessons,
            )
            for c in all_courses.get("data", [])
        ]

    async def checkin(self, lesson_id):
        r = await self.request(
            "/api/v3/lesson/checkin", "POST", json={"source": 21, "lessonId": lesson_id, "joinIfNotIn": True}, raw=True
        )
        d = checked(r.json())
        self.bearer = r.headers.get("set-auth", "").removeprefix("Bearer ")
        self.lesson_token = d["data"]["lessonToken"]
        if not self.bearer:
            raise ProviderError("平台未返回课堂授权令牌")
        self.lesson_tokens[str(lesson_id)] = (self.bearer, self.lesson_token)
        return d

    async def scan(self, url):
        d = checked(await self.request("/api/v3/app/scan", "POST", json={"url": url}))
        lesson_id = str(d["data"]["value"])
        await self.checkin(lesson_id)
        return lesson_id

    async def eligibility(self, lesson_id=None):
        return checked(
            await self.request("/api/v3/lesson/location-checkin/eligibility", headers=self.auth_for(lesson_id))
        )

    async def location_checkin(self, lesson_id, latitude, longitude, address):
        if not address or not -90 <= float(latitude) <= 90 or not -180 <= float(longitude) <= 180:
            raise ProviderError("请填写有效地址与经纬度")
        return checked(
            await self.request(
                "/api/v3/lesson/location-checkin/submit",
                "POST",
                headers=self.auth_for(lesson_id),
                json={"lat": latitude, "lng": longitude, "lessonId": lesson_id, "source": 54, "address": address},
            )
        )

    async def presentation(self, presentation_id, lesson_id=None):
        d = checked(
            await self.request(
                "/api/v3/lesson/presentation/fetch",
                params={"presentation_id": presentation_id},
                headers=self.auth_for(lesson_id),
            )
        )
        return d.get("data") or {}

    async def answer(
        self,
        problem_id,
        problem_type,
        *,
        options=None,
        content="",
        image_urls=None,
        retry=False,
        time=None,
        lesson_id=None,
    ):
        result = options
        if problem_type == 5:
            result = {
                "content": content,
                "pics": [{"pic": u, "thumb": u + "?imageView2/2/w/568"} for u in image_urls or []],
                "videos": [],
            }
        data = {
            "problemId": problem_id,
            "dt": time if time is not None else ms(),
            "problemType": problem_type,
            "result": result,
        }
        if retry:
            data = {"problems": [{**data, "retry_times": None}]}
        return checked(
            await self.request(
                "/api/v3/lesson/problem/" + ("retry" if retry else "answer"),
                "POST",
                headers=self.auth_for(lesson_id),
                json=data,
            )
        )

    async def upload(self, content, filename):
        d = checked(
            await self.request(
                "/pc/generate_qiniu_token", "POST", json={"bucket_name": "cms-attachment", "expired_time": 3600}
            )
        )
        key = f"{ms()}{filename}"
        d = checked(
            await self.request(
                "https://upload.qiniup.com/",
                "POST",
                data={"token": d["data"]["token"], "key": key, "fname": filename},
                files={"file": (key, content)},
            )
        )
        return "https://qn-scd1.yuketang.cn/" + d["key"]

    async def listen(self, lesson_id, callback):
        url = SERVERS[self.server].replace("https:", "wss:") + "/wsapp/"
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(
                json.dumps(
                    {
                        "op": "hello",
                        "userid": self.uid,
                        "role": "student",
                        "auth": self.lesson_tokens[str(lesson_id)][1],
                        "lessonid": lesson_id,
                    }
                )
            )
            async for message in ws:
                data = json.loads(message)
                await callback(data)
                if data.get("op") == "lessonfinished":
                    break
