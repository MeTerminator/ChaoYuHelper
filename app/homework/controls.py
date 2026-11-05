"""Parse official Chaoxing homework HTML without executing its scripts."""

import json

from bs4 import BeautifulSoup

from .attachments import saved_media


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
