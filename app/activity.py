"""Normalize platform activity data at the UI/service boundary."""

import time
from urllib.parse import parse_qs, urlparse

from .providers.base import ProviderError


def update_problem_timing(unlocked, event, now=None):
    """Keep each problem's original dt when the teacher extends its deadline."""
    now = time.time() if now is None else now
    if event.get("type") == "problem" and event.get("prob") is not None:
        state = unlocked.setdefault(str(event["prob"]), {})
        state.update({key: event[key] for key in ("dt", "limit", "prob") if key in event})
        if event.get("dt") is not None:
            state["deadline"] = float(event["dt"]) / 1000 + max(0, float(event.get("limit") or 0))
        return
    problem = event.get("problem") or {}
    pid = str(problem.get("prob") or problem.get("sid") or "")
    if not pid or event.get("op") not in ("unlockproblem", "extendtime"):
        return
    state = unlocked.setdefault(pid, {})
    if event["op"] == "unlockproblem":
        state.update(problem)
        if problem.get("limit") is not None:
            state["deadline"] = now + max(0, float(problem["limit"]))
    elif problem.get("extend") is not None and "deadline" in state:
        state["deadline"] += max(0, float(problem["extend"]))


def rain_answer_timing(unlocked, problem, force_retry=False, now=None):
    now = time.time() if now is None else now
    state = unlocked.get(str(problem["problemId"]), {})
    retry = force_retry or ("deadline" in state and state["deadline"] <= now)
    dt = state.get("dt") or problem.get("dt")
    if retry and dt is None:
        raise ProviderError("缺少原题目时间，无法构造补交请求，请从课堂时间线打开题目")
    return retry, dt if retry else None


class PickUpdates:
    """Notify once per newly picked account, without replaying the initial list."""

    def __init__(self, account_uids):
        self.accounts = {str(uid) for uid in account_uids}
        self.seen = None

    def update(self, users):
        current = {str(u.get("userId", "")) for u in users}
        new = [] if self.seen is None else [u for u in users if str(u.get("userId", "")) in self.accounts - self.seen]
        self.seen = current if self.seen is None else self.seen | current
        return new


def reference_answers(questions):
    """Use only references actually supplied by the platform; never guess."""
    from .providers.chaoxing import text

    result = {}
    for q in questions:
        typ, opts = int(q.get("type", 0)), q.get("answer") or []
        correct = [str(o["name"]) for o in opts if o.get("isanswer") in (True, 1, "1") and "name" in o]
        value = None
        if typ == 1 and correct:
            value = correct
        elif typ in (0, 3, 16) and correct:
            value = correct[0]
        elif typ in (2, 9, 10) and opts:
            value = [text(o.get("content", "")).split(";")[0].strip() for o in opts]
        elif typ not in (0, 1, 2, 3, 9, 10, 16) and opts:
            value = text(opts[0].get("answer", "")).split(";")[0].strip()
        if value and (not isinstance(value, list) or all(value)):
            result[str(q["id"])] = value
    return result


def topic_id(active):
    extras = active.get("extraInfo") or active.get("extras") or {}
    query = parse_qs(urlparse(active.get("url", "")).query)
    value = extras.get("topicId") or (query.get("topicId") or query.get("topicid") or [None])[0]
    if value is None or str(value).strip() == "":
        raise ProviderError("活动未提供讨论 topicId，请刷新活动列表")
    return str(value)


def slide_index(event):
    value = (
        (event.get("slide") or {}).get("si")
        if event.get("op") == "slidenav"
        else event.get("slideindex", event.get("si"))
    )
    return max(0, int(value) - 1) if value is not None else None


def validate_answers(active, questions, answers):
    missing = []
    for i, q in enumerate(questions):
        value = answers.get(str(q["id"]))
        required = int(active["type"]) != 14 or q.get("ismust") in (1, "1", True)
        empty = (
            value is None
            or value == ""
            or value == []
            or (isinstance(value, list) and any(v in ("", None) for v in value))
        )
        if required and empty and not q.get("_images"):
            missing.append(str(i + 1))
    if missing:
        raise ProviderError("请完成题目：" + "、".join(missing))
