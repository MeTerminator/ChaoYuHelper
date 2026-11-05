"""Read-only homework aggregation with account-wide throttling and encrypted snapshots."""

import asyncio
import math
import time

from .providers.base import PlatformVerificationRequired, ProviderError

SUBMITTED = ("已完成", "待批阅", "已提交")
REFRESH_SECONDS = 120
VERIFICATION_SECONDS = 300
COURSE_INTERVAL = 1.0


def course_key(course):
    return str(course["courseId"]), str(course["classId"])


def cached_response(record):
    return {
        **record["summary"],
        "from_cache": True,
        "retry_after": max(0, math.ceil(record["next_refresh"] - time.time())),
    }


async def load_summary(service, account_id, *, refresh=False):
    provider = service.provider(account_id)
    if provider.platform != "chaoxing":
        raise ProviderError("我的作业目前支持学习通，请在顶部切换平台")
    # Shared by every page and REST caller, not just one refresh button.
    lock = service.homework_locks.setdefault(account_id, asyncio.Lock())
    async with lock:
        record = service.store.get("homework-summary", account_id, {})
        if not record and not refresh:
            return {
                "works": [],
                "courses": [],
                "errors": [],
                "from_cache": True,
                "cache_missing": True,
                "retry_after": 0,
                "checked_at": None,
                "notice": "暂无作业缓存，请点击“刷新全部作业”加载。",
            }
        # Persisted snapshots remain valid for page entry regardless of age.
        # Only an explicit refresh may fetch again, subject to platform cooldown.
        if record and (not refresh or time.time() < record.get("next_refresh", 0)):
            return cached_response(record)
        previous = (record.get("summary") or {}).get("works", [])
        errors, works, seen = [], [], set()
        stale, verification = False, False
        try:
            courses = await service.courses(account_id)
        except Exception as exc:
            # Never erase a useful snapshot just because course discovery failed.
            if not record:
                raise
            summary = {**record["summary"], "stale": True, "notice": f"课程刷新失败，保留上次数据：{exc}"}
            record = {"summary": summary, "next_refresh": time.time() + REFRESH_SECONDS}
            service.store.put("homework-summary", account_id, record)
            return cached_response(record)
        for index, course in enumerate(courses):
            try:
                if verification:
                    raise PlatformVerificationRequired("因学习通要求验证码，本课程暂未读取；请先输入图片验证码完成验证")
                if index:
                    await asyncio.sleep(COURSE_INTERVAL)
                entries = [{**work, "course": course} for work in await provider.homework_list(course)]
            except Exception as exc:
                if isinstance(exc, PlatformVerificationRequired):
                    verification = True
                errors.append({"course": course, "error": str(exc)})
                entries = [work for work in previous if course_key(work["course"]) == course_key(course)]
                stale = stale or bool(entries)
            for work in entries:
                key = (*course_key(course), str(work["id"]))
                if key not in seen:
                    seen.add(key)
                    works.append(work)
        works.sort(key=lambda work: work["status"] in SUBMITTED)
        delay = VERIFICATION_SECONDS if verification else REFRESH_SECONDS
        summary = {
            "works": works,
            "courses": courses,
            "errors": errors,
            "stale": stale,
            "verification_required": verification,
            "checked_at": time.time(),
            "notice": "部分课程显示上次成功读取的数据，状态可能已变化" if stale else "",
        }
        record = {"summary": summary, "next_refresh": time.time() + delay}
        service.store.put("homework-summary", account_id, record)
        return {**summary, "from_cache": False, "retry_after": delay}


async def start_verification(service, account_id, refresh=False):
    provider = service.provider(account_id)
    if provider.platform != "chaoxing":
        raise ProviderError("访问验证码仅支持学习通")
    async with service.homework_locks.setdefault(account_id, asyncio.Lock()):
        return await provider.access_verification_start(refresh)


async def submit_verification(service, account_id, token, code):
    provider = service.provider(account_id)
    if provider.platform != "chaoxing":
        raise ProviderError("访问验证码仅支持学习通")
    async with service.homework_locks.setdefault(account_id, asyncio.Lock()):
        await provider.access_verification_submit(token, code)
        record = service.store.get("homework-summary", account_id)
        if record:
            record["next_refresh"] = 0
            service.store.put("homework-summary", account_id, record)
