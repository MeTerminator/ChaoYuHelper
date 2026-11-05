"""Account-bound homework loading, attachments and submission."""

from io import BytesIO

from PIL import Image

from ..base import ProviderError, checked


class ChaoxingHomework:
    async def homework_list(self, course):
        from ...homework import HOST, document, homework_url, page_links, parse_list

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

        from ...homework import HOST, homework_url, review

        if work.get("status") not in ("已完成", "待批阅", "已提交"):
            raise ProviderError("此作业尚未提交，请使用作答入口")
        url = homework_url(work["url"])
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        params["selectTimes"] = "0"
        # Explicit review endpoint avoids task -> dowork redirects that may begin an attempt.
        page = await self.request(HOST + "/mooc-ans/mooc2/work/view", params=params, raw=True)
        return review(page.text, str(page.url))

    async def homework_open(self, work):
        from ...homework import homework_url, merge_forms, page_links, parse_form

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
        from ...homework import submission

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
