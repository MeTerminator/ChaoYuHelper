import http.cookiejar
import uuid
from http.cookies import SimpleCookie
from urllib.parse import urlparse

import httpx

SERVERS = {
    "yuketang": "https://www.yuketang.cn",
    "pro": "https://pro.yuketang.cn",
    "changjiang": "https://changjiang.yuketang.cn",
    "huanghe": "https://huanghe.yuketang.cn",
}


class ProviderError(Exception):
    pass


def checked(data):
    if not isinstance(data, dict):
        raise ProviderError("平台返回的数据格式不正确，请刷新或重新登录")
    if isinstance(data, dict):
        if data.get("result") in (0, -1, False) or data.get("success") is False or data.get("status") is False:
            raise ProviderError(str(data.get("errorMsg") or data.get("msg") or data.get("mes") or "平台拒绝了请求"))
        if "code" in data and data["code"] not in (0, "0", 200):
            raise ProviderError(str(data.get("message") or data.get("msg") or f"平台错误 {data['code']}"))
    return data


class Provider:
    platform = ""

    def __init__(self, store, account=None, server="yuketang"):
        self.store, self.account = store, account or {}
        self.server = "yuketang" if self.platform == "chaoxing" else server
        self.device_id = self.store.get("config", "device") or uuid.uuid4().hex
        self.store.put("config", "device", self.device_id)
        self.client = httpx.AsyncClient(timeout=35, follow_redirects=True, max_redirects=5)
        for cookie in self.account.get("cookies", []):
            self.client.cookies.jar.set_cookie(http.cookiejar.Cookie(**cookie))
        self.bearer = ""
        self.lesson_token = ""

    @property
    def uid(self):
        return str(self.account.get("uid", ""))

    def cookies(self):
        fields = (
            "version",
            "name",
            "value",
            "port",
            "port_specified",
            "domain",
            "domain_specified",
            "domain_initial_dot",
            "path",
            "path_specified",
            "secure",
            "expires",
            "discard",
            "comment",
            "comment_url",
        )
        return [
            {**{f: getattr(c, f) for f in fields}, "rest": c._rest, "rfc2109": c.rfc2109}
            for c in self.client.cookies.jar
        ]

    def save(self):
        if self.account.get("id"):
            self.account["cookies"] = self.cookies()
            self.store.put("accounts", self.account["id"], self.account)

    async def close(self):
        await self.client.aclose()

    async def guard_request(self, request):
        # Validate the platform origin for every redirect hop.
        host = request.url.host or ""
        if request.url.scheme != "https" or not (
            host.endswith(".chaoxing.com")
            or host.endswith(".yuketang.cn")
            or host in ("open.weixin.qq.com", "lp.open.weixin.qq.com", "upload.qiniup.com", "p.cldisk.com")
        ):
            raise ProviderError("不支持的平台地址或重定向")

    async def request(
        self, url, method="GET", *, params=None, data=None, json=None, files=None, headers=None, raw=False, content=None
    ):
        if url.startswith("/"):
            url = SERVERS[self.server] + url
        host = urlparse(url).hostname or ""
        if not (
            host.endswith(".chaoxing.com")
            or host.endswith(".yuketang.cn")
            or host in ("open.weixin.qq.com", "lp.open.weixin.qq.com", "upload.qiniup.com", "p.cldisk.com")
        ):
            raise ProviderError("不支持的平台地址")
        if self.platform == "rainClassroom":
            # Rain's app API requires both ordinary cookies and mirrored session headers.
            # Build a request without sending it to honor cookie domain/path restrictions.
            cookie_header = self.client.build_request("GET", url).headers.get("cookie", "")
            cookies = SimpleCookie()
            cookies.load(cookie_header)
            session_headers = {}
            if "csrftoken" in cookies:
                session_headers["x-csrftoken"] = cookies["csrftoken"].value
            if "sessionid" in cookies:
                session_headers["sessionid"] = cookies["sessionid"].value
            if cookies:
                session_headers["x-uid"] = self.uid
            headers = {**session_headers, **(headers or {})}
        try:
            hooks = self.client.event_hooks.setdefault("request", [])
            if self.guard_request not in hooks:
                hooks.append(self.guard_request)
            r = await self.client.request(
                method, url, params=params, data=data, json=json, files=files, headers=headers, content=content
            )
            r.raise_for_status()
            self.save()
            if raw:
                return r
            try:
                return r.json()
            except ValueError as exc:
                raise ProviderError("平台返回非 JSON 数据，可能需要重新登录") from exc
        except httpx.HTTPStatusError as exc:
            endpoint = urlparse(str(exc.request.url))
            raise ProviderError(
                f"平台接口返回 HTTP {exc.response.status_code}（{endpoint.hostname}{endpoint.path}）"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"平台网络请求失败（{type(exc).__name__}），请检查网络后重试") from exc

    def account_metadata(self):
        return {}

    async def finish_login(self):
        profile = await self.profile()
        self.account = {
            **profile,
            **self.account_metadata(),
            "platform": self.platform,
            "server": self.server,
            "id": f"{self.platform}:{self.server}:{profile['uid']}",
        }
        self.save()
        return self.account

    def import_cookies(self, cookie_string):
        domain = ".chaoxing.com" if self.platform == "chaoxing" else urlparse(SERVERS[self.server]).hostname
        for item in cookie_string.split(";"):
            if "=" in item:
                name, value = item.strip().split("=", 1)
                self.client.cookies.set(name, value, domain=domain, path="/")
