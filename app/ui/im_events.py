"""IM polling, history pagination and local event application."""

import json
import time

from nicegui import ui

from ..im import (
    attachment_info,
    control_update,
    merge_messages,
    message_session,
    visible_message,
)
from ..im_media import profile_images
from ..service import service
from .components import guarded


class MessageEventsView:
    def handle_im_status(self, event):
        self.im_status = str(event.get("status", "未连接"))
        if self.section == "groups" and hasattr(self, "im_status_label"):
            self.im_status_label.set_text(self.im_status)
        if "错误" in self.im_status:
            ui.notify(self.im_status, type="negative")

    async def poll_im(self):
        if self.im_polling or not self.account_id or self.platform != "chaoxing":
            return
        self.im_polling = True
        try:
            account_id = self.account_id
            if self.im_event_account != account_id:
                self.im_event_account = account_id
                self.im_event_sequence = service.im.snapshot(account_id)["sequence"]
                self.im_was_connected = False
            snapshot = service.im.snapshot(account_id, self.im_event_sequence)
            self.im_event_sequence = snapshot["sequence"]
            self.im_user_id = snapshot["user_id"]
            if snapshot["status"] != self.im_status:
                self.handle_im_status({"status": snapshot["status"]})
            reconnected = snapshot["connected"] and not self.im_was_connected
            self.im_was_connected = snapshot["connected"]
            for event in snapshot["events"]:
                if account_id != self.account_id:
                    return
                if event["name"] == "course-im-control":
                    self.apply_im_control(event["message"])
                else:
                    await self.handle_im(event["message"])
            if reconnected and self.section == "groups" and self.im_connecting != account_id:
                await guarded(self.connect_im)
        finally:
            self.im_polling = False

    async def load_older_im(self):
        if self.im_selected:
            await self.group_history(self.im_selected, older=True)

    async def group_history(self, group_id, older=False):
        if not group_id or group_id not in self.im_groups:
            return
        page = self.im_pages.get(group_id, {})
        if older and (page.get("isLast") or page.get("loading")):
            return
        changed = self.im_selected != group_id
        self.im_selected = group_id
        self.im_unread[group_id] = 0
        self.im_request += 1
        request, account_id = self.im_request, self.account_id
        self.render_im_groups()
        self.render_im_chat(loading=True, scroll_bottom=not older)
        if changed or not hasattr(self, "im_input"):
            self.render_im_composer()
        conversation = self.im_groups[group_id]
        args = [conversation["id"], conversation["chatType"], page.get("cursor", -1) if older else -1]
        self.im_pages.setdefault(group_id, {})["loading"] = True
        try:
            result = await service.im.call(account_id, "historyPage", *args)
        finally:
            if account_id == self.account_id:
                self.im_pages.setdefault(group_id, {})["loading"] = False
        if request != self.im_request or account_id != self.account_id or self.section != "groups":
            return
        if result.get("error"):
            ui.notify("云端漫游读取失败，保留本地消息：" + result["error"], type="warning")
        else:
            self.im_pages[group_id] = {"cursor": result.get("cursor"), "isLast": result.get("isLast", False)}
            for message in result.get("data", []):
                message = {**message, "chatType": conversation["chatType"], "_account_id": account_id}
                # History can omit 'to'; use the requested conversation in that case.
                message.setdefault("to", conversation["id"])
                self.cache_im_message(message, group_id)
        users = {str(m.get("from") or "").split("/")[0] for m in self.im_history.get(group_id, [])}
        profiles = await self.provider().im_profiles(users=users)
        if request != self.im_request or account_id != self.account_id or self.section != "groups":
            return
        self.im_profiles.update(profiles)
        service.im_images.schedule(
            self.account_id, [url for profile in profiles.values() for url in profile_images(profile)]
        )
        self.im_unread[group_id] = 0
        service.store.put("im-read", f"{account_id}:{group_id}", time.time())
        self.render_im_groups()
        self.render_im_chat(scroll_bottom=not older)

    def apply_im_control(self, message, render=True):
        if message.get("_account_id") != self.account_id:
            return
        event = control_update(message, self.im_self_id())
        if not event:
            return
        key = event.get("key")
        if not key:
            return
        account_id = self.account_id
        kind = event["kind"]
        if kind == "local-read":
            store_key = f"{account_id}:{key}"
            previous = service.store.get("im-read", store_key, 0)
            service.store.put("im-read", store_key, max(previous, event["time"]))
            if self.im_latest_time(key) <= max(previous, event["time"]):
                self.im_unread[key] = 0
        elif kind in ("peer-read", "peer-channel", "group-read"):
            if kind == "peer-read":
                receipts = {event["mid"]: {"read": True}}
            elif kind == "group-read":
                receipts = {
                    str(mid): {"count": count}
                    for mid, count in event["counts"].items()
                    if isinstance(count, int) and count >= 0
                }
            else:
                # A channel receipt covers messages known at receipt time, not future messages.
                receipts = {
                    str(m.get("serverMsgId") or m.get("id") or ""): {"read": True}
                    for m in self.im_history.get(key, [])
                    if str(m.get("from") or "").split("/")[0] == self.im_self_id()
                }
            for mid, receipt in receipts.items():
                if not mid:
                    continue
                store_key = f"{account_id}:{key}:{mid}"
                previous = service.store.get("im-receipts", store_key, {})
                if "count" in receipt:
                    receipt["count"] = max(previous.get("count", 0), receipt["count"])
                service.store.put("im-receipts", store_key, {**previous, **receipt})
        elif kind == "recall":
            service.store.put(
                "im-recalls",
                f"{account_id}:{key}:{event['mid']}",
                {"sender": event["sender"], "confirmed": message.get("type") == "recall"},
            )
        if render and self.section == "groups" and hasattr(self, "group_region"):
            self.render_im_groups()
            if key == self.im_selected:
                self.render_im_chat()

    def im_message_recalled(self, message, key):
        mid = str(message.get("serverMsgId") or message.get("id") or "")
        recall = service.store.get("im-recalls", f"{self.account_id}:{key}:{mid}", {})
        sender = str(message.get("from") or "").split("/")[0]
        return bool(recall and (recall.get("confirmed") or recall.get("sender") == sender))

    def cache_im_message(self, message, key=None):
        if not visible_message(message):
            self.apply_im_control(message, render=False)
            return
        key = key or message_session(message, self.im_self_id())
        if not key:
            return
        record_id = str(
            message.get("serverMsgId") or message.get("id") or message.get("mid") or __import__("uuid").uuid4().hex
        )
        service.store.put(
            "im-messages",
            f"{self.account_id}:{key}:{record_id}",
            {"account_id": self.account_id, "message": message, "session": key},
        )
        self.im_history[key] = merge_messages(self.im_history.get(key, []), [message])
        if key not in self.im_groups:
            kind, target = key.split(":", 1)
            self.im_groups[key] = {"id": target, "chatType": kind}

    async def handle_im(self, message):
        if message.get("_account_id") != self.account_id:
            return
        if not visible_message(message):
            self.apply_im_control(message)
            return
        account_id = self.account_id
        key = message_session(message, self.im_self_id())
        if not key:
            return
        self.cache_im_message(message, key)
        sender = str(message.get("from") or "").split("/")[0]
        if sender != self.im_self_id() and (key != self.im_selected or self.section != "groups"):
            self.im_unread[key] = self.im_unread.get(key, 0) + 1
        elif self.section == "groups":
            service.store.put("im-read", f"{account_id}:{key}", time.time())
        if self.section == "groups":
            self.render_im_groups()
            if key == self.im_selected:
                self.render_im_chat()
        conversation = self.im_groups[key]
        sender = str(message.get("from") or "").split("/")[0]
        users = [sender] + ([conversation["id"]] if conversation["chatType"] == "singleChat" else [])
        profiles = await self.provider().im_profiles(
            users=users, groups=[conversation["id"]] if conversation["chatType"] == "groupChat" else []
        )
        if account_id != self.account_id:
            return
        self.im_profiles.update(profiles)
        service.im_images.schedule(
            self.account_id, [url for profile in profiles.values() for url in profile_images(profile)]
        )
        profile = profiles.get(key, {})
        if profile.get("name") != conversation["id"]:
            conversation.update(profile)
        self.save_im_conversations()
        if self.section == "groups":
            self.render_im_groups()
            if key == self.im_selected:
                self.render_im_chat()
        if attachment_info(message):
            ui.notify("收到新的课堂活动", timeout=8000)
            body = json.dumps(str(attachment_info(message).get("title") or "收到新的课堂活动"))
            await ui.run_javascript(
                "if ('Notification' in window && Notification.permission === 'granted') "
                f"new Notification('课堂活动', {{body: {body}}})"
            )
