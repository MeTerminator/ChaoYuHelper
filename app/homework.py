"""Parse official Chaoxing homework HTML without executing its scripts."""

import json
import re
from html import escape
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .providers.base import ProviderError

HOST = "https://mooc1.chaoxing.com"
LIST_PATHS = {"/mooc2/work/list", "/mooc-ans/mooc2/work/list"}
ANSWER_PATHS = {
    "/mooc-ans/mooc2/work/dowork",
    "/mooc2/work/dowork",
    "/work/dowork",
    "/work/doHomeWorkNew",
    "/mooc-ans/work/doHomeWorkNew",
}


def homework_url(url, base=HOST):
    url = urljoin(base, url)
    uri = urlparse(url)
    if uri.scheme != "https" or uri.hostname != "mooc1.chaoxing.com" or uri.username or uri.password:
        raise ProviderError("作业页面返回了非官方地址")
    if uri.path not in (
        "/mooc2/work/list",
        "/mooc-ans/mooc2/work/list",
        "/mooc-ans/mooc2/work/task",
        "/mooc-ans/mooc2/work/view",
        *ANSWER_PATHS,
    ):
        raise ProviderError("不支持的作业页面，请在官方课程中查看")
    return url


def page_links(html, base, kind="list"):
    """Follow only pagination URLs supplied by the platform, never fabricate tokens."""
    soup = document(html)
    paths = LIST_PATHS if kind == "list" else ANSWER_PATHS
    links = []
    for node in soup.select(
        'a[rel~="next"], a.next, a[title="下一页"], .pageDiv a[href], .pagination a[href], .paging a[href]'
    ):
        if node.get("aria-disabled") == "true" or "disabled" in node.get("class", []):
            continue
        value = node.get("href") or node.get("data-url") or ""
        if not value or value.startswith(("#", "javascript:")):
            continue
        url = homework_url(value, base)
        if urlparse(url).path not in paths:
            raise ProviderError("作业分页地址类型不正确")
        old, new = parse_qs(urlparse(base).query), parse_qs(urlparse(url).query)
        identities = ("courseId", "courseid", "classId", "clazzid", "cpi", "workId", "workid", "answerId")
        if any(key in old and key in new and old[key] != new[key] for key in identities):
            raise ProviderError("作业分页返回了其他课程或作业的地址")
        if url != base and url not in links:
            links.append(url)
    return links


def document(html):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title in ("提示", "温馨提示") or soup.select_one('input[type="password"]'):
        raise ProviderError("作业页面无访问权限或会话已过期，请重新登录并刷新课程")
    return soup


def parse_list(html, base=HOST):
    soup = document(html)
    result = []
    for node in soup.select('[onclick^="goTask"]'):
        url = homework_url(node.get("data", ""), base)
        query = parse_qs(urlparse(url).query)
        title = node.select_one(".right-content p:not(.status)")
        status = node.select_one(".status")
        result.append(
            {
                "id": (query.get("workId") or [""])[0],
                "name": title.get_text(" ", strip=True) if title else node.get("aria-label", "作业"),
                "status": status.get_text(" ", strip=True) if status else "待查看",
                "url": url,
            }
        )
    if not result and not soup.select_one(".task-list, .bottomList, .no-data, .nullMain"):
        raise ProviderError("无法识别作业列表，平台页面结构可能已变化")
    return result


def review(html, base=HOST):
    soup = document(html)
    questions = soup.select(".questionLi") or soup.select(".TiMu")
    for node in questions:
        for dangerous in node.select("script, iframe, form, input, button, textarea, select"):
            dangerous.decompose()
        for media in node.select("img[src], audio[src], video[src], source[src]"):
            media["src"] = urljoin(base, media["src"])
    title = soup.select_one(".mark_name, .detailsHead h2, .detailsHead .tit")
    if not questions:
        raise ProviderError("此作业未开放作答记录或页面结构已变化")
    return {"title": title.get_text(" ", strip=True) if title else "作业详情", "questions": [str(q) for q in questions]}


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


def dropdown_controls(node, typ):
    if typ not in (11, 13):
        return []
    controls = []
    for i, box in enumerate(
        node.select(".selectBox.order_box" if typ == 13 else ".line_answer_ct .selectBox, .thirdUlList .lineOption")
    ):
        span = box.select_one("p > span")
        native = box.select_one("select")
        values = {}
        if native:
            values = {
                o.get("value"): o.get_text(" ", strip=True) for o in native.select("option[value]") if o.get("value")
            }
        else:
            for choice in box.select("ul.options > li[data]"):
                label = choice.select_one("a") or choice
                values[choice["data"]] = label.get_text(" ", strip=True)
        name = box.get("index") if native else span.get("data") if span else None
        label = box.find_parent(class_="line_answer_ct")
        left = label.select_one(".line_option") if label else box.select_one(".matchNum")
        if not values or (typ == 11 and (not name or not name.isdigit())):
            return []
        controls.append(
            {
                "name": name or str(i),
                "label": left.get_text(" ", strip=True) if left else f"第 {i + 1} 位",
                "options": values,
            }
        )
    if typ == 11 and len({c["name"] for c in controls}) != len(controls):
        return []
    return controls


def initial_answer(node, qid, typ, options, editors, dropdowns, fields):
    raw = fields.get("answer" + qid, "")
    textarea = node.find("textarea", attrs={"name": "answer" + qid})
    if textarea and not raw:
        raw = textarea.get_text()
    initial = BeautifulSoup(str(raw), "html.parser").get_text(" ", strip=True)
    if typ in (0, 1, 3):
        checked = [n.get("value") for n in node.select("input[checked][value]") if n.get("value") in options]
        if typ == 1:
            initial = checked or [key for key in options if len(key) == 1 and key in str(raw)]
        else:
            initial = checked[0] if checked else str(raw) if str(raw) in options else None
    elif typ == 2 or (editors and (typ in (9, 10) or len(editors) > 1)):
        initial = []
        for editor in editors:
            element = node.find(attrs={"name": editor}) or node.find(id=editor)
            value = element.get_text() if element and element.name == "textarea" else fields.get(editor, "")
            initial.append(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))
    elif typ in (11, 13):
        initial = [None] * len(dropdowns)
        if raw:
            if typ == 13 and len(str(raw)) == len(dropdowns):
                initial = list(str(raw))
            elif typ == 11:
                try:
                    previous = {str(pair["name"]): pair["content"] for pair in json.loads(raw)}
                    initial = [previous.get(control["name"]) for control in dropdowns]
                except (ValueError, TypeError, KeyError):
                    pass
        initial = [v if v in c["options"] else None for v, c in zip(initial, dropdowns)]
    return {"initial": initial, "existing_media": saved_media(raw)}


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


def attachment_html(file):
    oid = str(file.get("object_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", oid):
        raise ProviderError("附件 Object ID 不正确")
    name = escape(str(file.get("name") or "附件"))
    if file.get("image"):
        return (
            f'<p><img class="ans-ued-img" src="https://p.ananas.chaoxing.com/star4/{oid}/origin.jpg" alt="{name}"></p>'
        )
    url = HOST + "/mooc-ans/ueditorupload/read?" + urlencode({"objectId": oid})
    return (
        f'<div class="attach"><a data="{oid}" href="{url}" target="_blank" rel="noopener noreferrer">{name}</a></div>'
    )


def saved_media(html):
    """Keep already saved answer files when resuming or editing its text."""
    soup = BeautifulSoup(str(html or ""), "html.parser")
    media = []
    for image in soup.select("img[src]"):
        uri = urlparse(image["src"])
        if uri.scheme == "https" and ((uri.hostname or "").endswith(".chaoxing.com") or uri.hostname == "p.cldisk.com"):
            media.append('<p><img class="ans-ued-img" src="' + escape(image["src"], quote=True) + '"></p>')
    for node in soup.select(".attach a, .attachNew span[data]"):
        oid = node.get("data") or (parse_qs(urlparse(node.get("href", "")).query).get("objectId") or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{8,128}", oid):
            media.append(attachment_html({"object_id": oid, "name": node.get_text(" ", strip=True), "image": False}))
    return list(dict.fromkeys(media))
