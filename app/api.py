"""Local API. Bearer token lives in data/api-token (mode 0600)."""

import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .providers.base import ProviderError
from .service import DATA_DIR, service

path = DATA_DIR / "api-token"
if not path.exists():
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32))
TOKEN = path.read_text().strip()
security = HTTPBearer()


def authorize(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not secrets.compare_digest(credentials.credentials, TOKEN):
        raise HTTPException(401, "Invalid API token")


router = APIRouter(prefix="/api", dependencies=[Depends(authorize)], tags=["课程助手"])


class CourseRequest(BaseModel):
    account_id: str
    course: dict[str, Any]


class SettingsRequest(CourseRequest):
    settings: dict[str, Any]


class HomeworkRequest(CourseRequest):
    work: dict[str, Any]


class ActionRequest(CourseRequest):
    accounts: list[str] = Field(min_length=1)
    active: dict[str, Any]
    values: dict[str, Any] = Field(default_factory=dict)


def require_fields(data, fields, label):
    missing = [key for key in fields if key not in data or data[key] is None or data[key] == ""]
    if missing:
        raise HTTPException(422, f"{label}缺少字段：" + "、".join(missing))


@router.get("/accounts")
async def accounts():
    return [service.public(a) for a in service.accounts()]


@router.get("/accounts/{account_id}/courses")
async def courses(account_id: str, refresh: bool = False):
    return await service.courses(account_id, refresh)


@router.post("/activities")
async def activities(body: CourseRequest):
    require_fields(body.course, ("courseId", "classId"), "课程")
    provider = service.provider(body.account_id)
    if provider.platform != "chaoxing":
        raise HTTPException(422, "雨课堂请通过课堂 WebSocket 查看活动")
    return await provider.activities(body.course)


@router.post("/settings/read")
async def settings(body: CourseRequest):
    require_fields(body.course, ("courseId", "classId"), "课程")
    return service.settings(body.account_id, body.course)


@router.post("/homework")
async def homework(body: CourseRequest):
    require_fields(body.course, ("courseId", "classId"), "课程")
    provider = service.provider(body.account_id)
    if provider.platform != "chaoxing":
        raise HTTPException(422, "作业接口仅支持学习通")
    return await provider.homework_list(body.course)


@router.post("/homework/review")
async def homework_review(body: HomeworkRequest):
    require_fields(body.work, ("url", "status"), "作业")
    provider = service.provider(body.account_id)
    if provider.platform != "chaoxing":
        raise HTTPException(422, "作业接口仅支持学习通")
    return await provider.homework_review(body.work)


@router.post("/homework/open")
async def homework_open(body: HomeworkRequest):
    require_fields(body.work, ("id", "url"), "作业")
    provider = service.provider(body.account_id)
    if provider.platform != "chaoxing":
        raise HTTPException(422, "作业接口仅支持学习通")
    return await provider.homework_open(body.work)


@router.post("/homework/attachments")
async def homework_upload(
    account_id: str = Form(...), work_id: str = Form(...), question_id: str = Form(...), file: UploadFile = File(...)
):
    provider = service.provider(account_id)
    if provider.platform != "chaoxing":
        raise HTTPException(422, "作业接口仅支持学习通")
    content = await file.read(50 * 1024 * 1024 + 1)
    return await provider.homework_upload(work_id, question_id, content, file.filename or "附件")


@router.put("/settings")
async def save_settings(body: SettingsRequest):
    require_fields(body.course, ("courseId", "classId"), "课程")
    location = body.settings.get("location")
    if location:
        if not isinstance(location, dict):
            raise HTTPException(422, "位置应为对象")
        require_fields(location, ("address", "latitude", "longitude"), "位置")
    service.provider(body.account_id)
    service.save_settings(body.account_id, body.course, body.settings)
    return {"ok": True}


@router.post("/actions/{action}")
async def perform(action: str, body: ActionRequest):
    action_fields = {
        "sign": (),
        "questions": ("questions", "answers"),
        "evaluate": ("score",),
        "reply": ("topic_uuid", "content"),
        "homework": ("work_id", "answers"),
        "rain-checkin": (),
        "rain-answer": ("problem_id", "problem_type"),
    }
    if action not in action_fields:
        raise HTTPException(404, "Unknown action")
    require_fields(body.values, action_fields[action], "操作参数")
    if action in ("sign", "questions", "evaluate"):
        require_fields(body.active, ("id",), "活动")
        require_fields(body.course, ("courseId", "classId"), "课程")
    if action == "questions":
        require_fields(body.active, ("type",), "活动")
        if not isinstance(body.values["questions"], list) or not isinstance(body.values["answers"], dict):
            raise HTTPException(422, "题目应为数组、答案应为对象")
        for question in body.values["questions"]:
            if not isinstance(question, dict):
                raise HTTPException(422, "题目应为对象")
            require_fields(question, ("id", "type"), "题目")
    if action == "homework":
        if body.accounts != [body.account_id] or not isinstance(body.values["answers"], dict):
            raise HTTPException(422, "作业必须使用加载题目的当前账号，答案应为对象")
    if action in ("rain-checkin", "rain-answer"):
        require_fields(body.course, ("lessonId",), "课程")
    source = service.provider(body.account_id)
    for account_id in body.accounts:
        p = service.provider(account_id)
        if (p.platform, p.server) != (source.platform, source.server):
            raise HTTPException(422, "不能混用不同平台或服务器的账号")

    async def operation(provider):
        if action == "sign" and provider.platform == "chaoxing":
            return await provider.sign(body.course, body.active["id"], **body.values)
        if action == "questions" and provider.platform == "chaoxing":
            return await provider.submit_questions(
                body.course, body.active, body.values["questions"], body.values["answers"]
            )
        if action == "evaluate" and provider.platform == "chaoxing":
            return await provider.evaluate(body.course, body.active["id"], **body.values)
        if action == "reply" and provider.platform == "chaoxing":
            return await provider.reply(**body.values)
        if action == "homework" and provider.platform == "chaoxing":
            return await provider.homework_submit(body.values["work_id"], body.values["answers"])
        if action == "rain-checkin" and provider.platform != "chaoxing":
            return await provider.checkin(body.course["lessonId"])
        if action == "rain-answer" and provider.platform != "chaoxing":
            await provider.ensure_lesson(body.course["lessonId"])
            return await provider.answer(**{**body.values, "lesson_id": body.course["lessonId"]})
        raise ProviderError("不支持的操作或平台")

    if action not in ("sign", "questions", "evaluate", "reply", "homework", "rain-checkin", "rain-answer"):
        raise HTTPException(404, "Unknown action")
    return await service.batch(body.accounts, operation)
