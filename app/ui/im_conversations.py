"""IM connection, conversation loading, drafts and manual sending."""

import asyncio

from nicegui import ui

from ..im import (
    merge_messages,
    message_session,
    message_time,
    session_key,
    visible_message,
)
from ..im_media import profile_images
from ..providers.base import ProviderError
from ..service import service
from .components import field, guarded, primary


class ConversationsView:
    def render_groups(self):
        if self.platform != "chaoxing" or not self.account_id:
            self.empty("forum", "请选择学习通账号", "群聊功能使用学习通的环信即时通讯服务。")
            return
        self.im_request += 1
        for row in service.store.all("im-messages").values():
            if row["account_id"] == self.account_id:
                message = row["message"]
                if not visible_message(message):
                    self.apply_im_control(message, render=False)
                    continue
                gid = row.get("session") or message_session(message, self.im_self_id())
                if gid:
                    self.im_history[gid] = merge_messages(self.im_history.get(gid, []), [message])
        for row in service.store.all("im-conversations").values():
            if row.get("account_id") == self.account_id:
                conversation = dict(row["conversation"])
                if not visible_message(conversation.get("lastMessage") or {}):
                    conversation.pop("lastMessage", None)
                if (
                    conversation.get("chatType") == "singleChat"
                    and conversation.get("id") == self.im_self_id()
                    and not self.im_history.get(row["key"])
                    and not conversation.get("lastMessage")
                ):
                    continue
                self.im_groups.setdefault(row["key"], conversation)
        with ui.row().classes("w-full items-center justify-between gap-3"):
            with ui.row().classes("items-center gap-3"):
                ui.label("消息").classes("page-title")
                self.im_status_label = ui.label(self.im_status).classes("im-status")
            with ui.row().classes("items-center gap-2"):
                ui.button("刷新会话", icon="refresh", on_click=lambda: guarded(self.load_groups)).props("flat")
                ui.button("发起私聊", icon="person_add_alt", on_click=self.im_private_dialog).props("flat")
                with ui.button(icon="more_horiz").props('flat round aria-label="消息选项"'):
                    with ui.menu():
                        ui.menu_item(
                            "启用桌面通知", on_click=lambda: ui.run_javascript("Notification.requestPermission()")
                        )
                        ui.menu_item("通过活动 ID 打开签到", on_click=self.im_activity_dialog)
        with ui.element("div").classes("im-workspace"):
            with ui.column().classes("im-sidebar"):
                with ui.column().classes("im-list-heading"):
                    ui.label("会话").classes("text-base font-semibold")
                    ui.input(
                        placeholder="搜索会话", value=self.im_query, on_change=lambda e: self.filter_im_groups(e.value)
                    ).props('outlined dense clearable aria-label="搜索会话"').classes("w-full im-search").add_slot(
                        "prepend", '<q-icon name="search" />'
                    )
                    ui.toggle(
                        {"all": "全部", "groupChat": "群聊", "singleChat": "私聊"},
                        value=self.im_filter,
                        on_change=lambda e: self.filter_im_type(e.value),
                    ).props("flat dense no-caps").classes("w-full")
                self.group_region = ui.column().classes("im-conversations")
            with ui.column().classes("im-chat"):
                self.im_chat_header = ui.row().classes("im-chat-header")
                self.im_scroll = ui.scroll_area().classes("im-chat-scroll")
                with self.im_scroll:
                    self.message_region = ui.column().classes("im-messages")
                self.im_composer = ui.column().classes("im-composer")
        self.render_im_groups()
        self.render_im_chat()
        self.render_im_composer()
        ui.timer(0.1, lambda: guarded(self.connect_im), once=True)

    def im_self_id(self):
        return self.im_user_id or str(self.provider().account.get("imAccount", {}).get("userName") or "")

    def im_private_dialog(self):
        with ui.dialog() as dialog, ui.card().classes("w-96 p-6 gap-4"):
            ui.label("发起私聊").classes("section-title")
            user_id = field("对方 IM 用户 ID")
            ui.label("也可以点击消息中的用户昵称发起私聊。打开会话不会发送消息。").classes("muted text-xs")

            async def open_private():
                uid = (user_id.value or "").strip()
                if not uid or len(uid) > 128 or not all(c.isalnum() or c in "_.@-" for c in uid):
                    raise ProviderError("请填写有效的 IM 用户 ID")
                await self.start_im_private(uid)
                dialog.close()

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat")
                primary("打开会话", lambda: guarded(open_private), "chat")
        dialog.open()

    async def start_im_private(self, user_id):
        account_id = self.account_id
        profiles = await self.provider().im_profiles(users=[user_id])
        if account_id != self.account_id or self.section != "groups":
            return
        key = session_key("singleChat", user_id)
        self.im_profiles.update(profiles)
        service.im_images.schedule(
            self.account_id, [url for profile in profiles.values() for url in profile_images(profile)]
        )
        self.im_groups.setdefault(key, {"id": user_id, "chatType": "singleChat"})
        self.im_groups[key].update(profiles.get(key, {}))
        self.save_im_conversations()
        self.im_filter = "all"
        # Rebuild filter controls as well as the list.
        self.render_groups()
        await self.group_history(key)

    def render_im_composer(self):
        self.im_composer.clear()
        with self.im_composer:
            if not self.im_selected:
                ui.label("选择会话后可发送消息").classes("muted text-sm")
                return
            key, account_id = self.im_selected, self.account_id
            draft_key = f"{account_id}:{key}"
            value = self.im_drafts.get(key, service.store.get("im-drafts", draft_key, ""))

            def draft_changed(event):
                if account_id == self.account_id:
                    self.im_drafts[key] = event.value or ""
                    service.store.put("im-drafts", draft_key, event.value or "")

            self.im_input = (
                ui.textarea(placeholder="输入消息…", value=value, on_change=draft_changed)
                .props('outlined autogrow rows=2 maxlength=5000 aria-label="消息输入框"')
                .classes("w-full im-textarea")
            )
            self.im_input.on("keydown.ctrl.enter.prevent", lambda: guarded(self.send_im_text))
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Ctrl + Enter 发送 · 最多 5000 字节").classes("muted text-xs")
                ui.space()
                if self.im_groups.get(key, {}).get("chatType") == "groupChat":
                    ui.button("活动 ID", on_click=self.im_activity_dialog).props("flat dense")
                self.im_send_button = primary("发送", lambda: guarded(self.send_im_text), "send")
                if self.im_sending:
                    self.im_send_button.disable()

    async def send_im_text(self):
        if self.im_sending or not self.im_selected:
            return
        key, account_id = self.im_selected, self.account_id
        content = self.im_input.value or ""
        if not content.strip():
            raise ProviderError("消息不能为空")
        if len(content.encode("utf-8")) > 5000:
            raise ProviderError("消息不能超过 5000 字节")
        conversation = self.im_groups[key]
        args = {
            "accountId": account_id,
            "targetId": conversation["id"],
            "chatType": conversation["chatType"],
            "text": content,
        }
        self.im_sending = True
        self.im_send_button.disable()
        try:
            try:
                result = await service.im.call(account_id, "sendText", args)
            except Exception as exc:
                raise ProviderError("发送结果未确认，请查看会话后再决定是否重发；草稿已保留。") from exc
            if account_id != self.account_id:
                return
            if result.get("error"):
                raise ProviderError(result["error"] + "；草稿已保留，请确认会话后再决定是否重发。")
            if self.im_drafts.get(key, content) == content:
                self.im_drafts[key] = ""
                service.store.put("im-drafts", f"{account_id}:{key}", "")
            self.cache_im_message(result["data"])
            if self.section == "groups":
                self.render_im_groups()
                if self.im_selected == key:
                    if self.im_input.value == content:
                        self.im_input.set_value("")
                    self.render_im_chat()
        finally:
            self.im_sending = False
            if self.section == "groups" and hasattr(self, "im_send_button"):
                self.im_send_button.enable()

    def save_im_conversations(self):
        for key, conversation in self.im_groups.items():
            service.store.put(
                "im-conversations",
                f"{self.account_id}:{key}",
                {"account_id": self.account_id, "key": key, "conversation": conversation},
            )

    def im_activity_dialog(self):
        with ui.dialog() as dialog, ui.card().classes("w-96 p-6 gap-4"):
            ui.label("打开群聊签到").classes("section-title")
            active_id = field("群聊签到活动 ID")

            async def direct():
                if not (active_id.value or "").isdigit():
                    raise ProviderError("请填写有效活动 ID")
                await self.sign_dialog(
                    {"courseId": "", "classId": ""},
                    {"id": active_id.value, "type": 2, "name": "群聊签到", "signType": "0"},
                    group=True,
                )
                dialog.close()

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat")
                primary("打开签到", lambda: guarded(direct), "open_in_new")
        dialog.open()

    async def connect_im(self):
        account_id = self.account_id
        if self.section != "groups" or self.platform != "chaoxing" or not account_id:
            return
        if self.im_connecting == account_id:
            return
        self.im_connecting = account_id
        try:
            state = await service.im.ensure(account_id)
            if account_id != self.account_id or self.section != "groups":
                return
            self.im_user_id = state.user_id
            self.handle_im_status({"status": state.status})
            await self.load_groups()
        finally:
            if self.im_connecting == account_id:
                self.im_connecting = None

    async def load_groups(self):
        account_id = self.account_id
        result, joined = await asyncio.gather(
            service.im.call(account_id, "conversations"), service.im.call(account_id, "groups")
        )
        if account_id != self.account_id or self.section != "groups":
            return
        warnings = [r["error"] for r in (result, joined) if r.get("error")]
        if warnings:
            ui.notify("部分会话未能同步，保留可用及本地会话：" + "；".join(warnings), type="warning")
        for group in joined.get("data", []):
            gid = str(group.get("groupId") or group.get("groupid") or group.get("id") or "")
            if gid:
                key = session_key("groupChat", gid)
                self.im_groups.setdefault(key, {"id": gid, "chatType": "groupChat"})
                self.im_groups[key]["name"] = group.get("groupName") or group.get("groupname") or gid
        for item in result.get("data", []):
            target, kind = str(item.get("conversationId") or ""), item.get("conversationType")
            key = session_key(kind, target)
            if not key:
                continue
            latest = item.get("lastMessage")
            if (
                kind == "singleChat"
                and target == self.im_self_id()
                and latest
                and not visible_message(latest)
                and not self.im_history.get(key)
            ):
                continue
            self.im_groups.setdefault(key, {"id": target, "chatType": kind})
            if latest and visible_message(latest):
                # Summary messages are not inserted into history: retain them for previews only.
                self.im_groups[key]["lastMessage"] = latest
            read_at = service.store.get("im-read", f"{account_id}:{key}", 0)
            self.im_unread[key] = int(item.get("unReadCount") or 0) if message_time(latest or {}) > read_at else 0
        self.render_im_groups()
        profiles = await self.provider().im_profiles(
            users=[g["id"] for g in self.im_groups.values() if g["chatType"] == "singleChat"],
            groups=[g["id"] for g in self.im_groups.values() if g["chatType"] == "groupChat"],
        )
        if account_id != self.account_id or self.section != "groups":
            return
        self.im_profiles.update(profiles)
        service.im_images.schedule(
            self.account_id, [url for profile in profiles.values() for url in profile_images(profile)]
        )
        for key, profile in profiles.items():
            if key in self.im_groups:
                conversation = self.im_groups[key]
                if profile.get("name") != conversation["id"]:
                    conversation["name"] = profile["name"]
                conversation.update({k: v for k, v in profile.items() if k in ("avatar", "avatarPics")})
        self.save_im_conversations()
        self.render_im_groups()
        if self.im_groups:
            first = next(iter(sorted(self.im_groups, key=self.im_latest_time, reverse=True)))
            await self.group_history(self.im_selected if self.im_selected in self.im_groups else first)
        else:
            self.im_selected = None
            self.render_im_chat()
            self.render_im_composer()
