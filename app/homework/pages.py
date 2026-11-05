"""Parse official Chaoxing homework HTML without executing its scripts."""

from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from ..providers.base import ProviderError
from .constants import ANSWER_PATHS, HOST, LIST_PATHS


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
                "details": list(
                    dict.fromkeys(
                        line
                        for line in node.stripped_strings
                        if line
                        not in {
                            title.get_text(" ", strip=True) if title else "",
                            status.get_text(" ", strip=True) if status else "",
                        }
                    )
                ),
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
    title_text = title.get_text(" ", strip=True) if title else "作业详情"
    heading = soup.select_one(".detailsHead")
    details = []
    if heading:
        # Limit metadata to the platform's visible heading, excluding question content and hidden controls.
        for node in heading.select("script, style, input, .mark_title, h2, .tit, [hidden]"):
            node.decompose()
        summary = " ".join(heading.stripped_strings)
        details = [summary] if summary else []
    return {"title": title_text, "questions": [str(q) for q in questions], "details": details}
