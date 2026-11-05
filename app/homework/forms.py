"""Parse official Chaoxing homework HTML without executing its scripts."""

import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from ..providers.base import ProviderError
from .controls import dropdown_controls, initial_answer
from .pages import document


def parse_form(html, base, partial=False):
    """Keep the server's signed action and hidden fields; reject unknown layouts."""
    soup = document(html)
    form = soup.select_one('form[action*="addStudentWorkNewWeb"]')
    if not form:
        raise ProviderError("平台未提供可识别的作业提交表单，可能尚未开放或已截止")
    action = urljoin(base, form["action"])
    uri = urlparse(action)
    if (
        uri.scheme != "https"
        or uri.hostname != "mooc1.chaoxing.com"
        or uri.username
        or uri.password
        or uri.path
        not in (
            "/mooc-ans/work/addStudentWorkNewWeb",
            "/work/addStudentWorkNewWeb",
        )
    ):
        raise ProviderError("无法识别作业提交地址")
    fields = {}
    for node in soup.select('input[type="hidden"]'):
        name = node.get("name") or node.get("id")
        if name:
            fields[name] = node.get("value", "")
    questions = []
    for type_input in soup.select('input[name^="answertype"], input[id^="answertype"]'):
        key = type_input.get("name") or type_input.get("id")
        qid = key.removeprefix("answertype")
        node = type_input.find_parent(
            class_=lambda classes: classes and any(c in classes.split() for c in ("questionLi", "singleQuesId", "TiMu"))
        )
        if not qid or not node or any(q["id"] == str(qid) for q in questions):
            continue
        try:
            typ = int(type_input.get("value", "-1"))
        except ValueError as exc:
            raise ProviderError("无法识别作业题型") from exc
        title = node.select_one("h3, .mark_name, .Zy_TItle")
        if not title:
            raise ProviderError("无法识别作业题干，已停止构造提交表单")
        options = {}
        for choice in node.select('input[type="radio"], input[type="checkbox"]'):
            key = choice.get("value", "")
            label = choice.find_parent("label") or choice.parent
            if key:
                options[key] = label.get_text(" ", strip=True)
        if not options:
            for i, choice in enumerate(node.select(".stem_answer .answerBg")):
                key = choice.get("name") or choice.get("data") or chr(65 + i)
                options[str(key)] = choice.get_text(" ", strip=True)
        if typ == 3 and not options:
            options = {"true": "正确", "false": "错误"}
        editors = [
            n.get("name") or n.get("id")
            for n in node.select('textarea, input[type="text"]')
            if (n.get("name") or n.get("id") or "").startswith("answerEditor")
        ]
        if typ == 2 and not editors:
            editors = [key for key in fields if key.startswith("answerEditor" + str(qid))]
        dropdowns = dropdown_controls(node, typ)
        slots = bool(editors) and (typ in (2, 9, 10) or len(editors) > 1)
        supported = (
            (typ in (0, 1, 3) and bool(options))
            or (typ == 2 and bool(editors))
            or typ in (4, 5, 6, 7, 8)
            or (typ in (11, 13) and bool(dropdowns))
            or slots
        )
        content = str(title)
        # Text-only control labels cannot represent an image-based choice.
        if typ in (0, 1, 3):
            choices = node.select(".stem_answer, .mark_letter")
            if choices and any(choice.select_one("img") for choice in choices):
                content += "".join(str(choice) for choice in choices)
        if typ in (11, 13):
            stem = node.select_one(".stem_answer, .matching")
            if stem:
                clean = BeautifulSoup(str(stem), "html.parser")
                for control in clean.select(".selectBox, .thirdUlList, script, input, select, textarea"):
                    control.decompose()
                content += str(clean)
        questions.append(
            {
                "id": str(qid),
                "type": typ,
                "content": content,
                "base_url": base,
                "options": options,
                "editors": editors,
                "supported": supported,
                "dropdowns": dropdowns,
                "slots": slots,
                **initial_answer(node, qid, typ, options, editors, dropdowns, fields),
            }
        )
    if not questions:
        raise ProviderError("平台未返回作业题目")
    ids = set(re.findall(r"[^,\s]+", fields.get("answerwqbid", "")))
    if not partial and ids and ids != {q["id"] for q in questions}:
        raise ProviderError("作业题目没有完整加载，已停止构造提交表单")
    expected = fields.get("totalQuestionNum") or (parse_qs(uri.query).get("totalQuestionNum") or [""])[0]
    if not partial and expected.isdigit() and int(expected) != len(questions):
        raise ProviderError("作业题目数量不完整，请在官方平台确认分页方式")
    return {
        "action": action,
        "fields": fields,
        "questions": questions,
        "referer": base,
        "expected_ids": sorted(ids),
        "expected_count": int(expected) if expected.isdigit() else None,
    }


def merge_forms(pages):
    first = pages[0]
    result = {**first, "fields": dict(first["fields"]), "questions": []}
    ids, seen = set(), set()
    expected_counts = {p["expected_count"] for p in pages if p.get("expected_count") is not None}
    for page in pages:
        ids.update(page.get("expected_ids", []))
        for q in page["questions"]:
            if q["id"] in seen:
                continue
            seen.add(q["id"])
            result["questions"].append(q)
            prefixes = ("answertype" + q["id"], "answer" + q["id"], "answerEditor" + q["id"])
            result["fields"].update({k: v for k, v in page["fields"].items() if k.startswith(prefixes)})
    if (ids and ids != seen) or (expected_counts and max(expected_counts) != len(seen)):
        raise ProviderError("作业分页未返回完整题目，不能提交不完整作业")
    result["fields"]["totalQuestionNum"] = str(len(seen))
    return result
