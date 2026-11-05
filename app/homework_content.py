"""Normalize platform HTML into a small, locally styled document fragment."""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment

from .homework import HOST

TAGS = set(
    "div span p br hr h1 h2 h3 h4 h5 h6 b strong i em u s del sub sup blockquote pre code "
    "ul ol li dl dt dd table caption thead tbody tfoot tr th td img a ruby rt rp".split()
)
CLASSES = {
    "mark_name": "hw-title",
    "mark_letter": "hw-options",
    "mark_answer": "hw-answer",
    "analysisDiv": "hw-analysis",
    "colorGreen": "hw-correct",
    "colorRed": "hw-incorrect",
    "colorShallow": "hw-muted",
    "totalScore": "hw-score",
    "reserve-newline": "hw-newlines",
    "mark_key": "hw-answer-key",
    "mark_fill": "hw-answer-block",
}


def clean_homework_html(content, image_url, base=HOST):
    """Only keep content markup; never keep upstream CSS, IDs, scripts or handlers."""
    soup = BeautifulSoup(str(content or ""), "html.parser")
    for node in soup.select("[style]"):
        if node.name and re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", node.get("style", ""), re.I):
            node.decompose()
    for node in soup.select(
        "script, style, link, meta, base, iframe, object, embed, form, input, "
        "button, textarea, select, svg, audio, video, source, .aiAssistant, "
        ".element-invisible-hidden, [hidden]"
    ):
        node.decompose()
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for node in soup.select(".marking_dui, .marking_cuo, .marking_bandui"):
        node.string = (
            "✓" if "marking_dui" in node.get("class", []) else "✗" if "marking_cuo" in node.get("class", []) else "△"
        )
    for node in list(soup.find_all(True)):
        if node.name not in TAGS:
            node.unwrap()
            continue
        attrs = {}
        classes = [CLASSES[c] for c in node.get("class", []) if c in CLASSES]
        if classes:
            attrs["class"] = classes
        if node.name == "img":
            # Lazy-loading markup occurs in both the mobile and desktop homework pages.
            source = node.get("data-src") or node.get("data-original") or node.get("src", "")
            source = image_url(urljoin(base, source)) if source else ""
            if not source:
                node.replace_with(soup.new_string(node.get("alt") or "[图片地址不受支持]"))
                continue
            attrs.update(src=source, alt=node.get("alt", ""), loading="lazy", decoding="async")
            width = str(node.get("width", ""))
            if width.isdigit() and 0 < int(width) <= 4096:
                attrs["width"] = width
        elif node.name == "a":
            target = urljoin(base, node.get("href", ""))
            if target.startswith("https://"):
                attrs.update(href=target, target="_blank", rel="noopener noreferrer")
        elif node.name in ("td", "th"):
            for key in ("colspan", "rowspan"):
                value = str(node.get(key, ""))
                if value.isdigit() and 0 < int(value) <= 100:
                    attrs[key] = value
        node.attrs = attrs
    return str(soup)
