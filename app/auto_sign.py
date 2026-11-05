"""Server-owned, account-scoped check-in jobs triggered only by live IM events."""

import asyncio
import hashlib
import json
import math
import random
import time

from .im import attachment_info, message_time
from .providers.base import ProviderError
from .sign_code import generate_sign_code


def validate_delay(value):
    if not isinstance(value, dict):
        raise ProviderError("请填写签到随机提交延迟")
    try:
        minimum, maximum = value.get("min", 1), value.get("max", 3)
        if isinstance(minimum, bool) or isinstance(maximum, bool):
            raise ValueError
        minimum, maximum = float(minimum), float(maximum)
    except (TypeError, ValueError) as exc:
        raise ProviderError("签到延迟需要填写有效秒数") from exc
    if not math.isfinite(minimum) or not math.isfinite(maximum) or not 0 <= minimum <= maximum <= 3600:
        raise ProviderError("签到延迟需满足 0 ≤ 最小秒数 ≤ 最大秒数 ≤ 3600")
    return {"min": minimum, "max": maximum}


class AutoSign:
    def __init__(self, service):
        self.service = service
        self.tasks = {}
        self.limit = asyncio.Semaphore(4)
        self.closing = False

    @staticmethod
    def key(account_id, course, active_id):
        identity = [account_id, str(course["courseId"]), str(course.get("classId", "")), str(active_id)]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    @staticmethod
    def enabled(settings):
        return (
            settings.get("autoLocationSign") is True or (settings.get("signCodeGenerator") or {}).get("mode") == "date"
        )

    def receive(self, account_id, message, user_id):
        if self.closing or message.get("type") in ("cmd", "read", "channel", "delivery"):
            return
        if str(message.get("from") or "").split("/")[0] == user_id:
            return
        # Reconnect deliveries can include old messages; history reads never call this method.
        age = time.time() - message_time(message)
        if not math.isfinite(age) or not -60 <= age <= 300:
            return
        info = attachment_info(message)
        if str(info.get("atype", "")) not in ("2", "54", "74") or not info.get("aid"):
            return
        ci = info.get("courseInfo")
        if not isinstance(ci, dict) or not ci.get("courseid") or not ci.get("classid"):
            return  # Group check-ins have no course-scoped authorization.
        course = {"courseId": str(ci["courseid"]), "classId": str(ci["classid"])}
        account = self.service.store.get("accounts", account_id, {})
        if account.get("platform") != "chaoxing":
            return
        settings = self.service.settings(account_id, course)
        if not self.enabled(settings):
            return
        active_id = str(info["aid"])
        key = self.key(account_id, course, active_id)
        if key in self.tasks or self.service.store.get("auto-sign", key):
            return
        if len(self.tasks) >= 256:
            return
        delay = validate_delay(settings.get("signDelay", {}))
        seconds = random.uniform(delay["min"], delay["max"])
        entry = {
            "account_id": account_id,
            "account": account.get("name", account_id),
            "course": course,
            "activeId": active_id,
            "title": str(info.get("title") or "签到"),
            "delay": seconds,
            "status": "scheduled",
            "time": time.time(),
        }
        self.service.store.put("auto-sign", key, entry)
        task = asyncio.create_task(self._run(key, entry))
        self.tasks[key] = (account_id, task)

        def completed(done):
            self.tasks.pop(key, None)
            if done.cancelled():
                # A task cancelled before its first turn never enters _run's exception handler.
                if self.service.store.get("auto-sign", key, {}).get("status") == "scheduled":
                    self._finish(key, entry, "cancelled", error="自动签到任务已取消")
            else:
                done.exception()

        task.add_done_callback(completed)

    def _finish(self, key, entry, status, *, result=None, error=""):
        value = {**entry, "status": status, "time": time.time()}
        self.service.store.put("auto-sign", key, value)
        self.service.store.put(
            "history",
            f"auto-sign:{key}",
            {
                **value,
                "source": "auto-sign",
                "action": "自动签到 · " + entry["title"],
                "ok": status == "success",
                "result": result,
                "error": error,
            },
        )

    async def _run(self, key, entry):
        try:
            await asyncio.sleep(entry["delay"])
            account_id = entry["account_id"]
            async with self.limit, self.service.locks.setdefault(account_id, asyncio.Lock()):
                # A successful manual submission while waiting wins over this job.
                if self.service.store.get("auto-sign", key, {}).get("status") != "scheduled":
                    return
                settings = self.service.settings(account_id, entry["course"])
                if not self.enabled(settings):
                    self._finish(key, entry, "cancelled", error="自动签到已关闭，取消待提交任务")
                    return
                result = await asyncio.wait_for(self._submit(entry), timeout=90)
                if result is None or result is False:
                    raise ProviderError("平台未返回有效的签到结果")
                self._finish(key, entry, "success", result=result)
        except asyncio.CancelledError:
            if self.service.store.get("auto-sign", key, {}).get("status") not in ("manual", "success"):
                self._finish(key, entry, "cancelled", error="自动签到任务已取消；若已发出请求，请在平台核对结果")
            raise
        except Exception as exc:
            error = str(exc) if isinstance(exc, ProviderError) else "自动签到运行失败或超时，请手动检查平台结果"
            self._finish(key, entry, "failed", error=error)

    async def _submit(self, entry):
        provider = self.service.provider(entry["account_id"])
        active_id, course = entry["activeId"], entry["course"]
        detail = await provider.sign_detail(active_id)
        info = detail.get("data", detail)
        if not isinstance(info, dict):
            raise ProviderError("无法识别签到详情，请手动处理")
        info = {**info, **await provider.active_info(active_id)}
        kind = str(info.get("otherId", ""))
        if kind not in ("4", "5"):
            raise ProviderError("本次不是位置或签到码签到，未自动提交，请手动处理")
        for field, expected in (("courseId", course["courseId"]), ("classId", course["classId"])):
            if info.get(field) is not None and str(info[field]) != expected:
                raise ProviderError("签到详情与消息中的课程不一致，未自动提交")
        # Fetches may take time: honor settings disabled while they were in flight.
        settings = self.service.settings(entry["account_id"], course)
        params = {}
        if kind == "4":
            if settings.get("autoLocationSign") is not True:
                raise ProviderError("位置签到自动提交未启用，未自动提交")
            loc = settings.get("location") or {}
            if not loc.get("address") or loc.get("latitude") is None or loc.get("longitude") is None:
                raise ProviderError("缺少完整的课程位置，请手动处理")
            params.update(address=loc["address"], latitude=loc["latitude"], longitude=loc["longitude"])
        else:
            params["code"], _ = generate_sign_code(settings.get("signCodeGenerator") or {})
        if info.get("showVCode") in (1, "1", True) or info.get("openCheckFaceFlag") in (1, "1", True):
            raise ProviderError("本次签到需要验证码或人脸校验，请手动处理")
        if info.get("ifPhoto", info.get("ifphoto")) in (1, "1", True):
            images = settings.get("imageObjectIds") or []
            if not images:
                raise ProviderError("本次签到需要照片，请先在课程设置中上传或手动处理")
            params["object_id"] = images[0]
        self.service.store.put(
            "auto-sign",
            self.key(entry["account_id"], course, active_id),
            {
                **entry,
                "status": "submitting",
                "time": time.time(),
            },
        )
        return await provider.sign(course, active_id, kind, **params)

    def manual_success(self, account_id, course, active_id):
        key = self.key(account_id, course, active_id)
        self.service.store.put("auto-sign", key, {"status": "manual", "time": time.time()})

    async def remove(self, account_id):
        tasks = [task for owner, task in list(self.tasks.values()) if owner == account_id]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        self.closing = True
        for account_id in {owner for owner, _ in self.tasks.values()}:
            await self.remove(account_id)
