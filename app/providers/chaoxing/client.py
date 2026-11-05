"""Chaoxing composition and lifecycle."""

import uuid

from ...crypto import (
    md5,
)
from ..base import Provider
from .access_verification import ChaoxingAccessVerification
from .activities import ChaoxingActivities
from .auth import ChaoxingAuth
from .captcha import ChaoxingCaptcha
from .courses import ChaoxingCourses
from .files import ChaoxingFiles
from .homework import ChaoxingHomework
from .messaging import ChaoxingMessaging
from .sign import ChaoxingSign


class Chaoxing(
    ChaoxingAuth,
    ChaoxingAccessVerification,
    ChaoxingMessaging,
    ChaoxingCourses,
    ChaoxingHomework,
    ChaoxingSign,
    ChaoxingActivities,
    ChaoxingCaptcha,
    ChaoxingFiles,
    Provider,
):
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
