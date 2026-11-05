"""IM credentials and user/group profiles."""

import asyncio

from ..base import ProviderError, checked
from .common import IM_WEB_HEADERS


class ChaoxingMessaging:
    async def im_session(self):
        # Same cookie-authenticated token endpoint used by the official Web IM.
        result = checked(
            await self.request("https://learn.chaoxing.com/apis/user/getUserImToken", params={"crossOrigin": "true"})
        )
        session = result.get("msg")
        if result.get("result") != 1 or not isinstance(session, dict):
            raise ProviderError("获取消息服务登录凭证失败，请重新登录学习通")
        uid, token = session.get("uid"), session.get("hxToken")
        if not uid or not isinstance(token, str) or not token:
            raise ProviderError("消息服务登录凭证不完整，请重新登录学习通")
        return {"userName": str(uid), "accessToken": token}

    async def im_user(self, user_id):
        data = await self.request(
            "https://im.chaoxing.com/webim/user/getUserInfoByTuid",
            params={"tuid": str(user_id), "crossOrigin": "true"},
            headers=IM_WEB_HEADERS,
        )
        if not isinstance(data, dict):
            raise ProviderError("无法读取用户资料")
        return {"id": str(user_id), "name": data.get("name") or str(user_id), "avatar": data.get("pic") or ""}

    async def im_group(self, group_id):
        data = await self.request(
            "https://im.chaoxing.com/webim/huanxin/getGroupInfo",
            params={"roomId": str(group_id), "crossOrigin": "true"},
            headers=IM_WEB_HEADERS,
        )
        item = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        pics = item.get("pics") if isinstance(item.get("pics"), list) else []
        return {
            "id": str(group_id),
            "name": item.get("name") or str(group_id),
            "avatar": pics[0] if pics else "",
            "avatarPics": pics[:4],
        }

    async def im_profiles(self, users=(), groups=()):
        # Cache within this account's provider; do not let one unavailable profile break the conversation.
        if not hasattr(self, "im_profile_cache"):
            self.im_profile_cache = {}
        limit = asyncio.Semaphore(6)

        async def fetch(kind, identifier):
            key = f"{kind}:{identifier}"
            if key in self.im_profile_cache:
                return key, self.im_profile_cache[key]
            async with limit:
                try:
                    profile = await (self.im_group(identifier) if kind == "groupChat" else self.im_user(identifier))
                    self.im_profile_cache[key] = profile
                    return key, profile
                except ProviderError:
                    return key, {"id": identifier, "name": identifier, "avatar": ""}

        jobs = [("singleChat", str(i)) for i in set(users) if i]
        jobs += [("groupChat", str(i)) for i in set(groups) if i]
        return dict(await asyncio.gather(*(fetch(kind, identifier) for kind, identifier in jobs)))
