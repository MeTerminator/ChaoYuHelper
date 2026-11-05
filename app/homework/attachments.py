"""Parse official Chaoxing homework HTML without executing its scripts."""

import re
from html import escape
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup

from ..providers.base import ProviderError
from .constants import HOST


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
