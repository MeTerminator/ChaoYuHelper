"""学习通协议迁移；所有提交均由用户操作触发。"""

import json
import re

from bs4 import BeautifulSoup

from ..base import ProviderError

MOBILE = "https://mobilelearn.chaoxing.com"
IM_WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://fe.chaoxing.com/",
}


def text(html):
    return BeautifulSoup(str(html or ""), "html.parser").get_text(" ", strip=True)


def extract_json(html, names):
    for name in names:
        match = re.search(r"\b" + name + r"\s*=\s*", html)
        if match:
            try:
                return json.JSONDecoder().raw_decode(html[match.end() :].lstrip())[0]
            except ValueError:
                continue
    raise ProviderError("未能解析题目数据，请检查登录状态或平台页面变化")
