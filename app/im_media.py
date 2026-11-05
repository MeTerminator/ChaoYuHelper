"""Shared persistent IM image cache and bounded background preloading."""

import asyncio
import time
from contextlib import suppress
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from .homework_media import FORMATS, HomeworkImages
from .im import media_url
from .providers.base import ProviderError


def valid_im_image(url):
    try:
        parsed = urlsplit(url)
        return bool(url and media_url(url) == url and parsed.port in (None, 443) and not parsed.password)
    except (TypeError, ValueError):
        return False


def profile_images(profile):
    return [url for value in [profile.get('avatar'), *(profile.get('avatarPics') or [])]
            if (url := media_url(value))]


def message_images(message):
    if message.get('type') != 'img':
        return []
    body = message.get('body')
    body = body if isinstance(body, dict) else {}
    return [url for value in (message.get('url'), body.get('url'), message.get('thumb'), body.get('thumb'))
            if (url := media_url(value))]


class IMImages(HomeworkImages):
    label = '消息'
    route_path = '/media/im/image'
    max_cache = 256 * 1024 * 1024
    max_entries = 4096
    cache_ttl = 7 * 24 * 3600
    link_ttl = 24 * 3600
    valid_url = staticmethod(valid_im_image)

    def __init__(self, service):
        self.service = service
        self.jobs = {}
        self.clients = {}
        self.prefetch_slots = asyncio.Semaphore(2)
        self.failures = {}
        super().__init__(service.store.path.parent / 'cache' / 'im')

    def link_deadline(self):
        # Stable URLs let all renders and pages reuse the browser's private cache.
        return (int(time.time()) // self.link_ttl + 2) * self.link_ttl

    def ready(self, account, url):
        digest = self.cache_name((account, url))
        return any((entry := self.cache.get(f'{digest}.{extension}')) and entry[0] > time.time()
                   for extension in FORMATS)

    def register_message(self, account, message):
        if not message_images(message):
            return
        body = message.get('body')
        body = body if isinstance(body, dict) else {}
        secret = message.get('secret') or body.get('secret')
        if not isinstance(secret, str) or not secret:
            return
        for url in message_images(message):
            key = self.cache_name((account, url))
            if self.service.store.get('im-image-auth', key) != secret:
                self.service.store.put('im-image-auth', key, secret)

    def create_client(self):
        return httpx.AsyncClient(timeout=35, follow_redirects=False)

    def request_client(self, provider):
        # Platform clients install a request guard which intentionally rejects Easemob.
        # Images have their own per-hop allowlist and an account-scoped HTTP session.
        account = provider.account['id']
        client = self.clients.get(account)
        if client is None or client.is_closed:
            client = self.clients[account] = self.create_client()
        client.cookies = httpx.Cookies(provider.client.cookies)
        return client

    def request_headers(self, provider, url):
        headers = {'Referer': 'https://im.chaoxing.com/'}
        host = urlsplit(url).hostname or ''
        state = self.service.im.connections.get(provider.account['id'])
        # Never send the IM bearer to avatars, arbitrary CDNs or redirect destinations.
        if (host == 'easemob.com' or host.endswith('.easemob.com')) and state and state.access_token:
            headers['Authorization'] = f'Bearer {state.access_token}'
            headers['Accept'] = 'application/octet-stream'
            secret = self.service.store.get('im-image-auth', self.cache_name((provider.account['id'], url)))
            if secret:
                headers['share-secret'] = secret
        return headers

    async def prefetch(self, account, urls):
        urls = list(dict.fromkeys(url for url in urls if self.valid_url(url)))
        for offset in range(0, len(urls), 8):
            await asyncio.gather(*(self._one(account, url) for url in urls[offset:offset + 8]))

    async def _one(self, account, url):
        key = (account, url)
        if self.failures.get(key, 0) > time.monotonic():
            return
        try:
            async with self.prefetch_slots:
                if not self.ready(account, url):
                    await self.get(self.service.provider(account), account, url)
            self.failures.pop(key, None)
        except (httpx.HTTPError, HTTPException, ProviderError):
            if len(self.failures) >= self.max_entries:
                self.failures.clear()
            self.failures[key] = time.monotonic() + 60

    def schedule(self, account, urls):
        for url in dict.fromkeys(urls):
            key = (account, url)
            if not self.valid_url(url) or self.ready(account, url) or key in self.jobs or len(self.jobs) >= 512:
                continue
            task = self.jobs[key] = asyncio.create_task(self._one(account, url))
            def finished(done, key=key):
                self.jobs.pop(key, None)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)

    async def remove(self, account):
        tasks = [task for (owner, _), task in self.jobs.items() if owner == account]
        tasks += [task for (owner, _), task in self.pending.items() if owner == account]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.failures = {key: expiry for key, expiry in self.failures.items() if key[0] != account}
        client = self.clients.pop(account, None)
        if client:
            await client.aclose()

    async def close(self):
        tasks = list(self.jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            await super().close()
        await asyncio.gather(*(client.aclose() for client in self.clients.values()))
        self.clients.clear()
