import asyncio
import os
from pathlib import Path

from .providers.base import ProviderError
from .providers.chaoxing import Chaoxing
from .providers.rain import Rain
from .store import Store

DATA_DIR = Path(os.environ.get("COURSE_HELPER_DATA", Path(__file__).resolve().parents[1] / "data"))


class Service:
    def __init__(self, store=None):
        self.store = store or Store(DATA_DIR)
        self.providers = {}
        self.locks = {}

    def new_login(self, platform, server="yuketang"):
        return (Chaoxing if platform == "chaoxing" else Rain)(self.store, server=server)

    def provider(self, account_id):
        account = self.store.get("accounts", account_id)
        if not account:
            raise ProviderError("请先添加并选择账号")
        if account_id not in self.providers:
            self.providers[account_id] = (Chaoxing if account["platform"] == "chaoxing" else Rain)(
                self.store, account, server=account["server"]
            )
        return self.providers[account_id]

    def accounts(self, platform=None, server=None):
        return [
            a
            for a in self.store.all("accounts").values()
            if (not platform or a["platform"] == platform)
            and (not server or a["platform"] == "chaoxing" or a["server"] == server)
        ]

    @staticmethod
    def public(account):
        return {
            k: v
            for k, v in account.items()
            if k in ("id", "uid", "platform", "server", "name", "avatar", "phone", "school")
        }

    async def delete_account(self, account_id):
        provider = self.providers.pop(account_id, None)
        if provider:
            await provider.close()
        self.store.delete("accounts", account_id)
        self.store.delete("courses", account_id)

    def setting_key(self, account_id, course):
        a = self.store.get("accounts", account_id)
        if not a:
            raise ProviderError("请先添加并选择账号")
        return f"{a.get('platform')}:{a.get('server')}:{course['courseId']}:{course.get('classId', '')}"

    def settings(self, account_id, course):
        return self.store.get("settings", self.setting_key(account_id, course), {})

    def save_settings(self, account_id, course, value):
        location = value.get("location")
        if location:
            try:
                lat, lng = float(location["latitude"]), float(location["longitude"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError("请填写有效地址与经纬度") from exc
            if not location.get("address") or not -90 <= lat <= 90 or not -180 <= lng <= 180:
                raise ProviderError("请填写有效地址与经纬度")
        self.store.put("settings", self.setting_key(account_id, course), value)

    async def courses(self, account_id, refresh=True):
        self.provider(account_id)
        if not refresh:
            return self.store.get("courses", account_id, [])
        result = await self.provider(account_id).courses()
        self.store.put("courses", account_id, result)
        return result

    async def batch(self, account_ids, operation):
        if not account_ids:
            raise ProviderError("请选择至少一个账号")

        async def one(account_id):
            lock = self.locks.setdefault(account_id, asyncio.Lock())
            async with lock:
                account = self.store.get("accounts", account_id, {})
                try:
                    result = await operation(self.provider(account_id))
                    if result is None or result is False:
                        raise ProviderError("平台未返回有效的操作结果")
                    entry = {"account": account.get("name", account_id), "ok": True, "result": result}
                except Exception as exc:
                    entry = {"account": account.get("name", account_id), "ok": False, "error": str(exc)}
                self.store.put("history", __import__("uuid").uuid4().hex, {**entry, "time": __import__("time").time()})
                return entry

        return await asyncio.gather(*(one(i) for i in dict.fromkeys(account_ids)))

    async def close(self):
        await asyncio.gather(*(p.close() for p in self.providers.values()))


service = Service()
