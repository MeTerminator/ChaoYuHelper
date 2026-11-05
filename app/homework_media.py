"""Authenticated same-origin homework images with a bounded, account-scoped cache."""

import asyncio
import hashlib
import hmac
import os
import secrets
import time
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from PIL import Image
from starlette.concurrency import run_in_threadpool

from .providers.base import ProviderError

MAX_IMAGE = 8 * 1024 * 1024
MAX_CACHE = 64 * 1024 * 1024
MAX_ENTRIES = 512
CACHE_TTL = 3600
LINK_TTL = 24 * 3600
FORMATS = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'GIF': 'image/gif', 'WEBP': 'image/webp', 'BMP': 'image/bmp'}


def valid_image_url(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and (host == 'chaoxing.com' or host.endswith('.chaoxing.com') or host == 'p.cldisk.com'))
    except ValueError:
        return False


def image_type(data):
    try:
        with Image.open(BytesIO(data)) as image:
            mime = FORMATS.get(image.format)
            if not mime or image.width * image.height > 40_000_000:
                raise ValueError('unsupported image')
            image.verify()
            return mime
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(502, '平台返回的图片无法读取') from exc


class HomeworkImages:
    label = '作业'
    route_path = '/media/homework/image'
    max_cache = MAX_CACHE
    max_entries = MAX_ENTRIES
    cache_ttl = CACHE_TTL
    link_ttl = LINK_TTL
    valid_url = staticmethod(valid_image_url)

    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else Path(
            os.environ.get("COURSE_HELPER_DATA", Path(__file__).resolve().parents[1] / "data")
        ) / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.secret = secrets.token_bytes(32)
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.downloads = asyncio.Semaphore(4)
        self.pending = {}
        # Only opaque hashes and image extensions are persisted; no cookies or source URLs.
        for path in sorted(self.cache_dir.glob("*.*"), key=lambda item: item.stat().st_mtime):
            if len(path.stem) == 64 and path.suffix[1:] in FORMATS:
                stat = path.stat()
                self.cache[path.name] = (stat.st_mtime + self.cache_ttl, stat.st_size, FORMATS[path.suffix[1:]])
                self.cache_bytes += stat.st_size
        self.prune()

    def signature(self, account, url, expires):
        payload = f'{account}\n{expires}\n{url}'.encode()
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    def url(self, account, url):
        if not self.valid_url(url):
            return ''
        expires = self.link_deadline()
        return self.route_path + '?' + urlencode({
            'account': account, 'url': url, 'expires': expires,
            'signature': self.signature(account, url, expires),
        })

    def link_deadline(self):
        return int(time.time()) + self.link_ttl

    def cache_name(self, key):
        return hashlib.sha256(f"{key[0]}\n{key[1]}".encode()).hexdigest()

    def discard(self, name):
        entry = self.cache.pop(name, None)
        if entry:
            self.cache_bytes -= entry[1]
        (self.cache_dir / name).unlink(missing_ok=True)

    def prune(self, incoming=0):
        now = time.time()
        for name, (deadline, _, _) in list(self.cache.items()):
            if deadline <= now:
                self.discard(name)
        while self.cache and (self.cache_bytes + incoming > self.max_cache
                              or len(self.cache) > self.max_entries - bool(incoming)):
            self.discard(next(iter(self.cache)))

    def cached(self, key):
        self.prune()
        digest = self.cache_name(key)
        for extension, mime in FORMATS.items():
            name = f"{digest}.{extension}"
            if name not in self.cache:
                continue
            try:
                data = (self.cache_dir / name).read_bytes()
                if len(data) > MAX_IMAGE or image_type(data) != mime:
                    self.discard(name)
                    return None
            except (OSError, HTTPException):
                self.discard(name)
                return None
            self.cache.move_to_end(name)
            return data, mime
        return None

    def remember(self, key, data, mime):
        extension = next(fmt for fmt, content_type in FORMATS.items() if content_type == mime)
        name = f"{self.cache_name(key)}.{extension}"
        self.discard(name)
        self.prune(len(data))
        if len(data) > self.max_cache:
            return
        path = self.cache_dir / name
        # Atomic replacement avoids serving partially written images.
        temporary = self.cache_dir / f"{name}.{secrets.token_hex(8)}.tmp"
        try:
            with temporary.open("xb") as stream:
                temporary.chmod(0o600)
                stream.write(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        self.cache[name] = (time.time() + self.cache_ttl, len(data), mime)
        self.cache_bytes += len(data)

    def request_client(self, provider):
        return provider.client

    async def fetch(self, provider, url):
        client = self.request_client(provider)
        # Check every redirect ourselves before sending cookies to its destination.
        for _ in range(6):
            if not self.valid_url(url):
                raise HTTPException(400, f'不支持的{self.label}图片地址')
            async with client.stream('GET', url, follow_redirects=False,
                                              headers=self.request_headers(provider, url)) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get('location', ''))
                    continue
                response.raise_for_status()
                length = response.headers.get('content-length', '')
                if length.isdigit() and int(length) > MAX_IMAGE:
                    raise HTTPException(413, f'{self.label}图片超过 8 MB')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE:
                        raise HTTPException(413, f'{self.label}图片超过 8 MB')
                data = bytes(data)
                return data, await run_in_threadpool(image_type, data)
        raise HTTPException(502, f'{self.label}图片重定向次数过多')

    def request_headers(self, provider, url):
        return {'Referer': 'https://mooc1.chaoxing.com/'}

    async def get(self, provider, account, url):
        if not self.valid_url(url):
            raise HTTPException(400, '不支持的图片地址')
        key = (account, url)
        cached = self.cached(key)
        if cached:
            return cached
        task = self.pending.get(key)
        if task is None:
            async def download():
                async with self.downloads:
                    cached = self.cached(key)
                    if cached:
                        return cached
                    data, mime = await self.fetch(provider, url)
                    self.remember(key, data, mime)
                    return data, mime
            task = self.pending[key] = asyncio.create_task(download())
            def finished(done):
                self.pending.pop(key, None)
                if not done.cancelled():
                    done.exception()  # Retrieve errors even if every caller disconnected.
            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def router(self, service):
        router = APIRouter()

        @router.get(self.route_path)
        async def image(account: str, url: str, expires: int, signature: str):
            if expires < time.time() or not hmac.compare_digest(
                signature.encode(), self.signature(account, url, expires).encode()
            ):
                raise HTTPException(403, f'{self.label}图片链接已失效，请刷新页面')
            if not self.valid_url(url):
                raise HTTPException(400, f'不支持的{self.label}图片地址')
            try:
                provider = service.provider(account)
                if provider.platform != 'chaoxing':
                    raise HTTPException(400, f'不支持的{self.label}账号')
                data, mime = await self.get(provider, account, url)
            except (httpx.HTTPError, ProviderError) as exc:
                raise HTTPException(502, f'{self.label}图片加载失败，请检查账号登录状态后重试') from exc
            return Response(data, media_type=mime, headers={
                'Cache-Control': f'private, max-age={min(self.cache_ttl, self.link_ttl)}', 'X-Content-Type-Options': 'nosniff',
                'Cross-Origin-Resource-Policy': 'same-origin',
            })

        return router


homework_images = HomeworkImages()
