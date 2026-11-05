"""Parse official Chaoxing homework HTML without executing its scripts."""

import json
from html import escape
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from ..providers.base import ProviderError
from .attachments import attachment_html


def submission(form, answers, attachments=None):
    """Build a manual submission using the loaded account's signed form only."""
    fields = dict(form["fields"])
    missing = []
    for i, q in enumerate(form["questions"]):
        if not q["supported"]:
            raise ProviderError(f"第 {i + 1} 题类型暂不支持，不能提交不完整作业")
        value = answers.get(q["id"])
        files = (attachments or {}).get(q["id"], [])
        existing = q.get("existing_media", []) if (answers.get("_keep_existing") or {}).get(q["id"], True) else []
        if (
            (value is None or value == "" or value == [] or (isinstance(value, list) and any(v == "" for v in value)))
            and not files
            and not existing
        ):
            missing.append(str(i + 1))
            continue
        typ, qid = q["type"], q["id"]
        fields["answertype" + qid] = str(typ)
        if typ in (0, 1, 3):
            selected = value if isinstance(value, list) else [value]
            if any(str(v) not in q["options"] for v in selected):
                raise ProviderError(f"第 {i + 1} 题包含无效选项")
            fields["answer" + qid] = "".join(str(v) for v in selected)
        elif typ == 2 or q.get("slots"):
            if not isinstance(value, list) or len(value) != len(q["editors"]):
                raise ProviderError(f"第 {i + 1} 题填空数量不正确")
            for key, content in zip(q["editors"], value):
                fields[key] = str(content)
        elif typ in (11, 13):
            controls = q["dropdowns"]
            if (
                not isinstance(value, list)
                or len(value) != len(controls)
                or any(str(v) not in c["options"] for v, c in zip(value, controls))
            ):
                raise ProviderError(f"第 {i + 1} 题连线 / 排序选项不完整或无效")
            if typ == 13:
                if len(set(value)) != len(value):
                    raise ProviderError(f"第 {i + 1} 题排序不能重复")
                fields["answer" + qid] = "".join(value)
            else:
                fields["answer" + qid] = json.dumps(
                    sorted(
                        [{"name": int(c["name"]), "content": str(v)} for c, v in zip(controls, value)],
                        key=lambda item: item["name"],
                    ),
                    separators=(",", ":"),
                )
        else:
            fields["answer" + qid] = (
                "<p>"
                + escape(str(value or "")).replace("\n", "</p><p>")
                + "</p>"
                + "".join(existing)
                + "".join(attachment_html(f) for f in files)
            )
    if missing:
        raise ProviderError("请完成题目：" + "、".join(missing))
    fields["answerwqbid"] = ",".join(q["id"] for q in form["questions"]) + ","
    uri = urlparse(form["action"])
    params = parse_qs(uri.query, keep_blank_values=True)
    params["saveStatus"] = ["1"]
    params.setdefault("totalQuestionNum", [str(len(form["questions"]))])
    url = urlunparse(uri._replace(query=urlencode(params, doseq=True)))
    return url, fields
