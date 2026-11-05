"""Cloud file upload and deduplication."""

from ...crypto import (
    file_crc,
)
from ..base import checked


class ChaoxingFiles:
    async def upload(self, content, filename):
        token = (await self.request("https://pan-yz.chaoxing.com/api/token/uservalid"))["_token"]
        exists = await self.request(
            "https://pan-yz.chaoxing.com/api/crcStorageStatus",
            params={"puid": self.uid, "crc": file_crc(content), "_token": token},
        )
        if exists.get("result") and exists.get("exist"):
            return exists["data"]["objectid"]
        d = checked(
            await self.request(
                "https://pan-yz.chaoxing.com/upload",
                "POST",
                params={"_from": "mobilelearn", "_token": token},
                data={"puid": self.uid},
                files={"file": (filename, content)},
            )
        )
        return d["data"]["objectId"]
