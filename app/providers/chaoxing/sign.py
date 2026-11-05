"""Check-in details, location payloads, face verification and manual submission."""

import uuid
from io import BytesIO

from PIL import Image

from ...crypto import (
    device_code,
    dumps,
    md5,
    ms,
    signed,
)
from ..base import ProviderError, checked
from .common import MOBILE


class ChaoxingSign:
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
