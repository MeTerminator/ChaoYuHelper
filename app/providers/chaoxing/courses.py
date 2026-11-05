"""Course and classroom activity discovery."""

import asyncio

from ...crypto import (
    enc_params,
    ms,
)
from ..base import checked
from .common import MOBILE


class ChaoxingCourses:
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
