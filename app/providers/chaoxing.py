"""学习通协议迁移；所有提交均由用户操作触发。"""

import json
import re
import uuid
from io import BytesIO
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from PIL import Image

from ..crypto import (
    APP_KEY,
    CAPTCHA_ID,
    WEB_KEY,
    aes,
    des_decrypt,
    device_code,
    dumps,
    enc_params,
    file_crc,
    md5,
    ms,
    rsa_encrypt,
    rsa_public_decrypt,
    signed,
)
from .base import Provider, ProviderError, checked

MOBILE = "https://mobilelearn.chaoxing.com"


def text(html):
    return BeautifulSoup(str(html or ""), "html.parser").get_text(" ", strip=True)


def extract_json(html, names):
    for name in names:
        match = re.search(r"\b" + name + r"\s*=\s*", html)
        if match:
            try:
                return json.JSONDecoder().raw_decode(html[match.end() :].lstrip())[0]
            except ValueError:
                continue
    raise ProviderError("未能解析题目数据，请检查登录状态或平台页面变化")


class Chaoxing(Provider):
    platform = "chaoxing"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.homework_forms = {}
        device = self.store.get("config", "device") or uuid.uuid4().hex
        self.store.put("config", "device", device)
        agent = f"(device:Pixel 9 Pro) Language/zh_CN com.chaoxing.mobile/ChaoXingStudy_3_6.7.4_android_phone_10940_314 (@Kalimdor)_{device}"
        schild = md5(r"(schild:ipL$TkeiEmfy1gTXb2XHrdLN0a@7c^vu) " + agent)
        self.client.headers.update(
            {
                "User-Agent": f"Dalvik/2.1.0 (Linux; U; Android 16; Pixel 9 Pro Build/BP4A.260205.002) (schild:{schild}) {agent}",
                "Accept-Language": "zh_CN",
            }
        )

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

    async def courses(self):
        d = checked(
            await self.request(
                "https://mooc1-api.chaoxing.com/mycourse/backclazzdata",
                params={"view": "json", "getTchClazzType": "1", "mcode": ""},
            )
        )
        courses = []
        for item in d.get("channelList", []):
            c = item.get("content", {})
            if not c.get("course") or c.get("state") != 0:
                continue
            course = c["course"]["data"][0]
            courses.append(
                dict(
                    courseId=str(course["id"]),
                    classId=str(c["id"]),
                    cpi=str(c.get("cpi", "")),
                    image=course.get("imageurl", ""),
                    name=course.get("name", "未知课程"),
                    teacher=course.get("teacherfactor", ""),
                    note=c.get("name", ""),
                    schools=course.get("schools", ""),
                    beginDate=c.get("beginDate"),
                    endDate=c.get("endDate"),
                )
            )
        return courses

    async def activities(self, course):
        import asyncio

        cid, clid = course["courseId"], course["classId"]
        joined = await self.request(
            "https://mooc1-api.chaoxing.com/gas/clazzperson",
            params={
                "courseid": cid,
                "clazzid": clid,
                "userid": self.uid,
                "personid": course.get("cpi", ""),
                "view": "json",
                "fields": "clazzid,popupagreement,personid,clazzname,createtime",
            },
        )
        join_time = (joined.get("data") or [{}])[0].get("createtime", "")
        app, web = await asyncio.gather(
            self.request(
                MOBILE + "/ppt/activeAPI/taskactivelist",
                params=enc_params(
                    {
                        "courseId": cid,
                        "classId": clid,
                        "uid": self.uid,
                        "cpi": course.get("cpi", ""),
                        "joinclasstime": join_time,
                    }
                ),
            ),
            self.request(
                MOBILE + "/v2/apis/active/student/activelist",
                params={"fid": "0", "courseId": cid, "classId": clid, "showNotStartedActive": "0", "_": ms()},
            ),
        )
        checked(app)
        checked(web)
        web_map = {str(a["id"]): a for a in web.get("data", {}).get("activeList", [])}
        result = []
        app_ids = {str(a["id"]) for a in app.get("activeList", [])}
        all_activities = [*app.get("activeList", []), *[a for aid, a in web_map.items() if aid not in app_ids]]
        for a in all_activities:
            w = web_map.get(str(a["id"]), {})
            result.append(
                {
                    **a,
                    "id": str(a["id"]),
                    "type": int(a.get("activeType") or a.get("type") or w.get("activeType") or w.get("type") or 0),
                    "name": a.get("nameOne") or a.get("name", ""),
                    "description": a.get("nameTwo") or w.get("nameFour", ""),
                    "signType": str(w.get("otherId", "0")),
                    "ongoing": a.get("status", w.get("status")) in (1, "1", True),
                    "url": a.get("url") or w.get("url", ""),
                    "extraInfo": a.get("extraInfo") or w.get("extraInfo") or {},
                }
            )
        return result

    async def homework_list(self, course):
        from ..homework import HOST, document, homework_url, page_links, parse_list

        page = await self.request(
            HOST + "/visit/stucoursemiddle",
            params={
                "courseid": course["courseId"],
                "clazzid": course["classId"],
                "cpi": course.get("cpi", ""),
                "ismooc2": "1",
            },
            raw=True,
        )
        soup = document(page.text)
        fields = {n.get("id"): n.get("value", "") for n in soup.select("input[id]")}
        link = soup.select_one('a[data-url*="/work/list"]')
        if not link or not fields.get("workEnc"):
            raise ProviderError("该课程未提供作业入口，请确认已登录且具有课程访问权限")
        url = homework_url(link["data-url"])
        page = await self.request(
            url,
            params={
                "courseId": course["courseId"],
                "classId": course["classId"],
                "cpi": course.get("cpi", ""),
                "ut": "s",
                "t": fields.get("t", ""),
                "stuenc": fields.get("enc", ""),
                "enc": fields["workEnc"],
            },
            raw=True,
        )
        pending, visited, result = [page], set(), {}
        while pending:
            current = pending.pop(0)
            url = str(current.url)
            if url in visited:
                continue
            visited.add(url)
            if len(visited) > 100:
                raise ProviderError("作业列表分页超过 100 页，请缩小课程范围")
            for work in parse_list(current.text, url):
                result.setdefault(work["id"], work)
            for next_url in page_links(current.text, url):
                if next_url not in visited:
                    pending.append(await self.request(next_url, raw=True))
        return list(result.values())

    async def homework_review(self, work):
        from urllib.parse import parse_qs, urlparse

        from ..homework import HOST, homework_url, review

        if work.get("status") not in ("已完成", "待批阅", "已提交"):
            raise ProviderError("此作业尚未提交，请使用作答入口")
        url = homework_url(work["url"])
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        params["selectTimes"] = "0"
        # Explicit review endpoint avoids task -> dowork redirects that may begin an attempt.
        page = await self.request(HOST + "/mooc-ans/mooc2/work/view", params=params, raw=True)
        return review(page.text, str(page.url))

    async def homework_open(self, work):
        from ..homework import homework_url, merge_forms, page_links, parse_form

        pending = [await self.request(homework_url(work["url"]), raw=True)]
        pages, visited = [], set()
        while pending:
            page = pending.pop(0)
            url = str(page.url)
            if url in visited:
                continue
            visited.add(url)
            if len(visited) > 100:
                raise ProviderError("作业题目分页超过 100 页，请检查平台页面")
            pages.append(parse_form(page.text, url, partial=True))
            for next_url in page_links(page.text, url, "questions"):
                if next_url not in visited:
                    pending.append(await self.request(next_url, raw=True))
        form = merge_forms(pages)
        self.homework_forms[str(work["id"])] = form
        return form["questions"]

    def homework_attachment_key(self, work_id):
        return f"{self.account.get('id', self.uid)}:{work_id}"

    def homework_files(self, work_id):
        return self.store.get("homework-attachments", self.homework_attachment_key(work_id), {})

    async def homework_upload(self, work_id, question_id, content, filename):
        from pathlib import Path

        form = self.homework_forms.get(str(work_id))
        question = next((q for q in (form or {}).get("questions", []) if q["id"] == str(question_id)), None)
        if not question or question["type"] not in (4, 5, 6, 7, 8) or question.get("slots"):
            raise ProviderError("请先加载当前账号的主观题，附件只能用于主观题")
        if not content or len(content) > 50 * 1024 * 1024:
            raise ProviderError("附件不能为空且不能超过 50 MB")
        name = Path(filename).name
        is_image = Path(name).suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
        if is_image:
            try:
                with Image.open(BytesIO(content)) as image:
                    image.verify()
            except Exception as exc:
                raise ProviderError("图片附件内容损坏或与扩展名不符") from exc
        oid = await self.upload(content, name)
        file = {
            "object_id": str(oid),
            "name": name,
            "image": is_image,
        }
        files = self.homework_files(work_id)
        entries = files.setdefault(str(question_id), [])
        if not any(f["object_id"] == file["object_id"] for f in entries):
            entries.append(file)
        self.store.put("homework-attachments", self.homework_attachment_key(work_id), files)
        return file

    async def homework_submit(self, work_id, answers):
        from ..homework import submission

        form = self.homework_forms.get(str(work_id))
        if not form:
            raise ProviderError("请先加载此账号的作业题目")
        if not isinstance(answers, dict) or not isinstance(answers.get("_keep_existing", {}), dict):
            raise ProviderError("作业答案或保留附件选项的格式不正确")
        selected, available = answers.get("_attachments") or {}, self.homework_files(work_id)
        if not isinstance(selected, dict):
            raise ProviderError("附件选择格式不正确")
        attachments = {}
        for qid, object_ids in selected.items():
            candidates = {f["object_id"]: f for f in available.get(qid, [])}
            if not isinstance(object_ids, list) or any(oid not in candidates for oid in object_ids):
                raise ProviderError("附件不属于当前账号和题目，请重新上传")
            attachments[qid] = [candidates[oid] for oid in dict.fromkeys(object_ids)]
        url, data = submission(form, answers, attachments)
        result = checked(await self.request(url, "POST", data=data, headers={"Referer": form["referer"]}))
        if not any(result.get(key) in (True, 1, "1", "success") for key in ("status", "result", "success")):
            raise ProviderError("无法确认作业是否提交成功，请先查看作答记录，不要重复提交")
        self.homework_forms.pop(str(work_id), None)
        return result

    async def active_info(self, active_id):
        d = checked(await self.request(MOBILE + "/v2/apis/active/getPPTActiveInfo", params={"activeId": active_id}))
        return d.get("data") or {}

    async def sign_detail(self, active_id, group=False):
        return checked(
            await self.request(
                MOBILE + ("/sign/getSignDetail" if group else "/newsign/signDetail"),
                params={"id": active_id} if group else {"activePrimaryId": active_id, "type": "1"},
            )
        )

    def location_payload(self, address, latitude, longitude):
        basic = dict(result=1, address=address, longitude=float(longitude), latitude=float(latitude))
        info = self.account.get("deviceInfo") or {}
        sig = {
            "data": dumps({"latitude": float(latitude), "longitude": float(longitude), "address": address}),
            "cxtime": str(ms()),
            "cxcid": info.get("cid", ""),
        }
        result = {
            **basic,
            "mockData": {"strategy": 0, "probability": -1},
            "locType": 161,
            "signConfig": {
                "signToken": signed(sig, info.get("sc", "")),
                "cxcid": sig["cxcid"],
                "cxtime": sig["cxtime"],
            },
        }
        return dumps(basic), dumps(result)

    async def sign(
        self,
        course,
        active_id,
        kind="0",
        *,
        code="",
        enc="",
        object_id="",
        address="",
        latitude=None,
        longitude=None,
        validate="",
        enc2="",
        face_id="",
        face_enc="",
        group=False,
    ):
        kind = str(kind)
        if kind not in ("0", "2", "3", "4", "5"):
            raise ProviderError("不支持的签到方式")
        if kind in ("3", "5") and not group and not code:
            raise ProviderError("请填写签到码或手势序列")
        base = {
            "activeId": active_id,
            "courseId": course.get("courseId", ""),
            "uid": self.uid,
            "clientip": "",
            "latitude": "-1",
            "longitude": "-1",
            "appType": "15",
            "fid": "0",
            "name": self.account.get("name", ""),
            "deviceCode": device_code(),
        }
        if kind in ("3", "5") and not group:
            checked(
                await self.request(
                    MOBILE + "/widget/sign/pcStuSignController/checkSignCode",
                    params={"activeId": active_id, "signCode": code},
                )
            )
            base["signCode"] = code
        if kind == "2":
            if not enc:
                raise ProviderError("请扫描或填写二维码内容")
            base["enc"] = enc
            if enc2:
                base["enc2"] = enc2
        if object_id:
            base["objectId"] = object_id
        if latitude is not None and longitude is not None and address:
            if not (-90 <= float(latitude) <= 90 and -180 <= float(longitude) <= 180):
                raise ProviderError("经纬度范围不正确")
            base.update(
                latitude=f"{float(latitude):.6f}", longitude=f"{float(longitude):.6f}", address=address, ifTiJiao="1"
            )
            base["location"], base["locationResult"] = self.location_payload(address, latitude, longitude)
        if kind == "4" and (latitude is None or longitude is None or not address):
            raise ProviderError("位置签到需要地址和经纬度")
        if validate:
            base["validate"] = validate
        if face_id:
            base.update(currentFaceId=face_id, ifCFP="1", faceEnc=face_enc)
        use_get = group or (kind == "0" and not object_id)
        r = await self.request(
            MOBILE + ("/sign/stuSignajax" if group else "/pptSign/stuSignajax"),
            "GET" if use_get else "POST",
            params=base if use_get else None,
            data=None if use_get else base,
            raw=True,
        )
        value = r.text.strip()
        if value != "success":
            raise ProviderError(value or "平台未返回签到结果")
        return {"success": True, "message": "签到成功"}

    async def attendees(self, active_id, group=False):
        url = "/widget/sign/group/pcTeaSignGroupController/getAttendList" if group else "/v2/apis/sign/getAttendInfo"
        return checked(await self.request(MOBILE + url, params={"activeId": active_id, "moreClassAttendEnc": ""}))

    async def receipt(self, active_id):
        return await self.request(MOBILE + "/sign/signReceipt2", params={"activeId": active_id})

    async def face(self, active_id):
        d = checked(
            await self.request(
                "https://passport2-api.chaoxing.com/api/getUserFaceid", params={"enc": md5(self.uid + "uWwjeEKsri")}
            )
        )
        face = d["data"]
        source = await self.request(face["http"].replace("http:", "https:"), raw=True)
        image = Image.open(BytesIO(source.content)).convert("RGB")
        exif = image.getexif()
        exif[37510] = b"ASCII\x00\x00\x00CourseHelper_" + uuid.uuid4().hex[:10].encode()
        output = BytesIO()
        image.save(output, format="JPEG", exif=exif)
        object_id = await self.upload(output.getvalue(), "face.jpg")
        info = self.account.get("deviceInfo") or {}
        result = dict(
            currentFaceId=object_id,
            LiveDetectionStatus="1",
            collectStatus="1",
            cxcid=info.get("cid", ""),
            cxtime=str(ms()),
        )
        result["signToken"] = signed(result, info.get("sc", ""))
        d = await self.request(
            MOBILE + "/pptSign/check-face-result",
            params={
                "DB_STRATEGY": "PRIMARY_KEY",
                "STRATEGY_PARA": "activeId",
                "activeId": active_id,
                "faceResult": dumps(result),
            },
        )
        if d.get("status") != 1:
            raise ProviderError("人脸校验未通过")
        return {"face_id": object_id, "face_enc": d["enc"]}

    async def upload(self, content, filename):
        token = (await self.request("https://pan-yz.chaoxing.com/api/token/uservalid"))["_token"]
        exists = await self.request(
            "https://pan-yz.chaoxing.com/api/crcStorageStatus",
            params={"puid": self.uid, "crc": file_crc(content), "_token": token},
        )
        if exists.get("result") and exists.get("exist"):
            return exists["data"]["objectid"]
        d = checked(
            await self.request(
                "https://pan-yz.chaoxing.com/upload",
                "POST",
                params={"_from": "mobilelearn", "_token": token},
                data={"puid": self.uid},
                files={"file": (filename, content)},
            )
        )
        return d["data"]["objectId"]

    async def questions(self, active):
        if int(active["type"]) == 42:
            html = (await self.request(active["url"], raw=True)).text
            return {
                "questionlist": extract_json(html, ["questionList", "quizList"]),
                "pptActive": extract_json(html, ["active"]),
            }
        d = checked(await self.request(MOBILE + "/v2/apis/quiz/quizDetail", params={"activeId": active["id"]}))
        return d.get("data") or {}

    async def submit_questions(self, course, active, questions, answers):
        from ..activity import validate_answers

        validate_answers(active, questions, answers)
        cid, clid, aid = course["courseId"], course["classId"], active["id"]
        kind = int(active["type"])
        if kind == 43:
            q = questions[0]
            value = answers[str(q["id"])]
            return checked(
                await self.request(
                    MOBILE + "/widget/quickvote/doQuestion",
                    "POST",
                    data={
                        "courseId": cid,
                        "classId": clid,
                        "activeId": aid,
                        "questionId": str(q["id"]),
                        "option": "".join(value) if isinstance(value, list) else value,
                    },
                )
            )
        if kind == 14:
            data = [("preventsubmit", "1"), ("courseId", cid), ("classId", clid), ("activeId", aid)]
            for q in questions:
                qid = str(q["id"])
                value = answers.get(qid)
                if value is None or value == "" or value == []:
                    continue
                data.append(("questionId", qid))
                data.extend((f"answer{qid}", str(v)) for v in (value if isinstance(value, list) else [value]))
            return checked(
                await self.request(
                    MOBILE + "/v2/apis/studentQuestion/doQuestion",
                    "POST",
                    content=urlencode(data).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            )
        status = await self.request(
            MOBILE + "/pptTestPaperStu/checkActiveStatus",
            "POST",
            data={"classId": clid, "activePrimaryId": aid, "appType": "15"},
        )
        if status.get("status") != 1:
            raise ProviderError("活动已结束或无法确认状态")
        payload = []
        for q in questions:
            value = answers.get(str(q["id"]), "")
            typ = int(q["type"])
            person = dict(q.get("personAnswer") or {})
            if typ in (0, 1, 3, 16):
                person["myoption"] = "".join(value) if isinstance(value, list) else value
            elif typ in (2, 9, 10):
                person["blankAnswer"] = [{"content": v} for v in value]
            else:
                person.update(content=value, imgs=person.get("imgs", []), recs=q.get("_images", person.get("recs", [])))
            payload.append({**{k: v for k, v in q.items() if not k.startswith("_")}, "personAnswer": person})
        return checked(
            await self.request(
                MOBILE + "/v2/apis/studentQuestion/doQuestionAnswering",
                "POST",
                params={
                    "activeId": aid,
                    "courseId": cid,
                    "classId": clid,
                    "DB_STRATEGY": "PRIMARY_KEY",
                    "STRATEGY_PARA": "activeId",
                },
                json=payload,
            )
        )

    async def pick(self, aid):
        data = await self.request(
            MOBILE + "/widget/webcache/pick/stu/pickAttend",
            params={"activeId": aid, "qasort": "0", "screenMode": "0", "webCacheId": aid, "_": ms()},
        )
        # The original endpoint uses result=0 before the teacher has picked anyone.
        if isinstance(data, dict) and data.get("result") == 0 and not data.get("errorMsg"):
            return {"userListChoose": []}
        return checked(data).get("data") or {}

    async def evaluate(self, course, aid, score, content="", scores=None):
        return checked(
            await self.request(
                MOBILE + "/v2/apis/score/stuSubmitAnswer",
                "POST",
                data={
                    "activeId": aid,
                    "courseId": course["courseId"],
                    "classId": course["classId"],
                    "score": str(score),
                    "content": content,
                    "scoreList": dumps(scores or []),
                },
            )
        )

    async def topic(self, topic_id):
        d = checked(
            await self.request(
                "https://groupyd.chaoxing.com/apis/topic/getTopic",
                "POST",
                params=enc_params({}),
                data={"puid": self.uid, "maxW": "1080", "topicId": topic_id},
            )
        )["data"]
        replies = checked(
            await self.request(
                "https://groupyd.chaoxing.com/apis/invitation/getReplys2",
                "POST",
                params=enc_params({}),
                data={
                    "puid": self.uid,
                    "uuid": d["uuid"],
                    "maxW": "1080",
                    "lastValue": "",
                    "lastAuxValue": "",
                    "order": "0",
                },
            )
        )
        return {**d, "replies": replies.get("data", {}).get("list", [])}

    async def reply(self, topic_uuid, content, anonymous=False):
        return checked(
            await self.request(
                "https://groupyd.chaoxing.com/apis/invitation/addReply",
                "POST",
                params=enc_params(
                    {
                        "puid": self.uid,
                        "uuid": str(uuid.uuid4()),
                        "maxW": "1080",
                        "topicUUID": topic_uuid,
                        "anonymous": "1" if anonymous else "0",
                    }
                ),
                data={"content": content},
            )
        )

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
