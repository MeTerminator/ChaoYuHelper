"""Questions, voting, picked students, evaluations and discussions."""

import uuid
from urllib.parse import urlencode

from ...crypto import (
    dumps,
    enc_params,
    ms,
)
from ..base import ProviderError, checked
from .common import MOBILE, extract_json


class ChaoxingActivities:
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
        from ...activity import validate_answers

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
