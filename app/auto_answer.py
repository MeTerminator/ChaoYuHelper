"""Server-owned, account-scoped AI auto-answer jobs for classroom interaction activities."""

import asyncio
import hashlib
import json
import math
import random
import time
from typing import Any, Dict, List

from .activity import validate_answers
from .ai_client import get_ai_config, solve_questions
from .auto_sign import validate_delay
from .im import attachment_info, message_time
from .providers.base import ProviderError
from .providers.chaoxing.common import MOBILE


class AutoAnswer:
    def __init__(self, service):
        self.service = service
        self.tasks = {}
        self.limit = asyncio.Semaphore(2)
        self.closing = False

    @staticmethod
    def key(account_id: str, course: Dict[str, Any], active_id: str) -> str:
        identity = ["auto-answer", account_id, str(course["courseId"]), str(course.get("classId", "")), str(active_id)]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    @staticmethod
    def is_quick_answer(active_type: Any, title: str) -> bool:
        """STRICT check to reject '抢答' (Quick Answer). NEVER submit 抢答."""
        typ_str = str(active_type).strip()
        if typ_str == "4":
            return True
        if "抢答" in str(title or ""):
            return True
        return False

    @staticmethod
    def enabled(settings: Dict[str, Any]) -> bool:
        return settings.get("aiTakeover") is True

    def receive(self, account_id: str, message: Dict[str, Any], user_id: str):
        if self.closing or message.get("type") in ("cmd", "read", "channel", "delivery"):
            return
        if str(message.get("from") or "").split("/")[0] == user_id:
            return
        # Reconnect deliveries can include old messages; history reads never call this method.
        age = time.time() - message_time(message)
        if not math.isfinite(age) or not -60 <= age <= 300:
            return

        info = attachment_info(message)
        atype = str(info.get("atype", ""))
        aid = str(info.get("aid", ""))
        title = str(info.get("title") or "课堂互动答题")

        # RULE 1: STRICTLY NEVER SUBMIT 抢答
        if self.is_quick_answer(atype, title):
            return

        # Supported interactive activity types (42: 随堂练习, 43: 投票, 14: 问卷)
        if atype not in ("42", "43", "14") or not aid:
            return

        ci = info.get("courseInfo")
        if not isinstance(ci, dict) or not ci.get("courseid") or not ci.get("classid"):
            return
        course = {"courseId": str(ci["courseid"]), "classId": str(ci["classid"])}

        account = self.service.store.get("accounts", account_id, {})
        if account.get("platform") != "chaoxing":
            return

        settings = self.service.settings(account_id, course)
        if not self.enabled(settings):
            return

        ai_config = get_ai_config(self.service.store)
        if not ai_config.get("api_key"):
            return

        key = self.key(account_id, course, aid)
        if key in self.tasks or self.service.store.get("auto-answer", key):
            return
        if len(self.tasks) >= 256:
            return

        delay = validate_delay(settings.get("aiAnswerDelay") or {"min": 3, "max": 10})
        seconds = random.uniform(delay["min"], delay["max"])
        recv_time = time.time()

        entry = {
            "account_id": account_id,
            "account": account.get("name", account_id),
            "course": course,
            "activeId": aid,
            "activeType": atype,
            "title": title,
            "delay": seconds,
            "status": "solving",
            "time": recv_time,
        }
        self.service.store.put("auto-answer", key, entry)
        task = asyncio.create_task(self._run(key, entry, info, recv_time))
        self.tasks[key] = (account_id, task)

        def completed(done):
            self.tasks.pop(key, None)
            if done.cancelled():
                if self.service.store.get("auto-answer", key, {}).get("status") == "solving":
                    self._finish(key, entry, "cancelled", error="AI 答题任务已取消")
            else:
                done.exception()

        task.add_done_callback(completed)

    def _finish(self, key: str, entry: Dict[str, Any], status: str, *, result: Any = None, error: str = ""):
        value = {**entry, "status": status, "finish_time": time.time()}
        self.service.store.put("auto-answer", key, value)
        self.service.store.put(
            "history",
            f"auto-answer:{key}",
            {
                **value,
                "source": "auto-answer",
                "action": "AI 答题 · " + entry["title"],
                "ok": status == "success",
                "result": result,
                "error": error,
            },
        )

    async def _run(self, key: str, entry: Dict[str, Any], info: Dict[str, Any], recv_time: float):
        try:
            account_id = entry["account_id"]
            course = entry["course"]
            aid = entry["activeId"]
            atype = int(entry["activeType"])
            title = entry["title"]

            # Double check 抢答
            if self.is_quick_answer(atype, title):
                self._finish(key, entry, "skipped", error="按规则跳过抢答活动")
                return

            provider = self.service.provider(account_id)
            url = info.get("url") or ""
            if not url:
                if atype == 42:
                    url = f"{MOBILE}/pptTestPaper/preAddQuestion2quiz?classId={course['classId']}&activePrimaryId={aid}&appType=15&uid={provider.uid}&isTeacherViewOpen=0"
                elif atype == 43:
                    url = f"{MOBILE}/widget/quickvote/goSubmitVote?courseId={course['courseId']}&classId={course['classId']}&activeId={aid}&isTeacherViewOpen=0"

            active = {"id": aid, "type": atype, "name": title, "url": url}

            # 1. 第一时间交给 AI 答题 (immediately request AI solving)
            ai_config = get_ai_config(self.service.store)
            if not ai_config.get("api_key"):
                raise ProviderError("未配置 AI API KEY，请在系统设置中配置")

            data = await provider.questions(active)
            questions = data.get("questionlist") or []
            if not questions and atype == 43 and data.get("pptActive"):
                questions = [data["pptActive"]]

            if not questions:
                raise ProviderError("平台未返回题目内容")

            # Check if already answered
            already = bool(data.get("pptUserAttend")) or any(q.get("studentAnswer") is not None for q in questions)
            if already:
                self._finish(key, entry, "success", result={"message": "该题目当前账号已作答过"})
                return

            course_name = ""
            courses = self.service.store.get("courses", account_id, [])
            for c in courses:
                if str(c.get("courseId")) == str(course["courseId"]):
                    course_name = c.get("name", "")
                    break

            answers = await solve_questions(ai_config, questions, course_name=course_name, active_name=title)
            validate_answers(active, questions, answers)

            # 2. 随机提交延迟 (wait for remaining delay)
            target_delay = float(entry.get("delay", 0))
            elapsed = time.time() - recv_time
            remaining = max(0.0, target_delay - elapsed)
            if remaining > 0:
                await asyncio.sleep(remaining)

            # 3. 提交作答
            async with self.limit, self.service.locks.setdefault(account_id, asyncio.Lock()):
                result = await provider.submit_questions(course, active, questions, answers)
                self._finish(key, entry, "success", result=result)

        except asyncio.CancelledError:
            self._finish(key, entry, "cancelled", error="AI 答题任务已取消")
            raise
        except Exception as exc:
            self._finish(key, entry, "failed", error=str(exc))

    async def answer_now(self, account_id: str, course: Dict[str, Any], active: Dict[str, Any]) -> Dict[str, Any]:
        """Manually trigger AI to answer a specific activity immediately with random delay."""
        aid = str(active["id"])
        atype = int(active.get("type") or active.get("activeType") or 0)
        title = active.get("name") or "课堂答题"

        # STRICT check: NEVER SUBMIT 抢答
        if self.is_quick_answer(atype, title):
            raise ProviderError("系统已设定严格跳过抢答活动，绝不提交抢答")

        if atype not in (42, 43, 14):
            raise ProviderError(f"当前活动类型 ({atype}) 不支持 AI 答题")

        settings = self.service.settings(account_id, course)
        delay = validate_delay(settings.get("aiAnswerDelay") or {"min": 3, "max": 10})
        seconds = random.uniform(delay["min"], delay["max"])
        recv_time = time.time()

        key = self.key(account_id, course, aid)
        account = self.service.store.get("accounts", account_id, {})
        entry = {
            "account_id": account_id,
            "account": account.get("name", account_id),
            "course": course,
            "activeId": aid,
            "activeType": str(atype),
            "title": title,
            "delay": seconds,
            "status": "solving",
            "time": recv_time,
        }
        self.service.store.put("auto-answer", key, entry)

        await self._run(key, entry, active, recv_time)
        res = self.service.store.get("auto-answer", key, {})
        if res.get("status") == "failed":
            raise ProviderError(res.get("error", "AI 答题提交失败"))
        return res

    async def check_course(self, account_id: str, course: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check all ongoing activities in the course and solve with AI."""
        provider = self.service.provider(account_id)
        activities = await provider.activities(course)
        ongoing = [a for a in activities if a.get("ongoing")]

        results = []
        for a in ongoing:
            atype = a.get("type")
            title = a.get("name", "")
            # Skip 抢答
            if self.is_quick_answer(atype, title):
                continue
            if atype in (42, 43, 14):
                try:
                    res = await self.answer_now(account_id, course, a)
                    results.append({"id": a["id"], "name": title, "ok": True, "result": res})
                except Exception as exc:
                    results.append({"id": a["id"], "name": title, "ok": False, "error": str(exc)})
        return results

    async def remove(self, account_id: str):
        tasks = [task for owner, task in list(self.tasks.values()) if owner == account_id]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        self.closing = True
        tasks = [task for _, task in list(self.tasks.values())]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
