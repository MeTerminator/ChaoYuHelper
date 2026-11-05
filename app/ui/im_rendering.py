"""Conversation list, avatars and message presentation."""

import json

from nicegui import ui

from ..im import (
    attachment_info,
    media_url,
    message_preview,
    message_session,
    message_time,
    receipt_label,
    session_key,
    system_notice,
    time_label,
    visible_message,
)
from ..im_media import message_images
from ..service import service
from .components import guarded


class MessagesView:
    def filter_im_groups(self, value):
        self.im_query = value or ""
        self.render_im_groups()

    def filter_im_type(self, value):
        self.im_filter = value or "all"
        self.render_im_groups()

    def im_group_name(self, key):
        conversation = self.im_groups.get(key, {})
        return str(conversation.get("name") or conversation.get("id") or "会话")

    def im_latest(self, key):
        visible = [m for m in self.im_history.get(key, []) if visible_message(m)]
        cached = (visible or [{}])[-1]
        summary = self.im_groups.get(key, {}).get("lastMessage") or {}
        if not visible_message(summary):
            summary = {}
        latest = max([cached, summary], key=message_time)
        return {**latest, "_recalled": True} if self.im_message_recalled(latest, key) else latest

    def im_latest_time(self, key):
        return message_time(self.im_latest(key))

    def draw_im_avatar(self, profile, name, css="im-avatar", on_click=None):
        pictures = [
            url for value in (profile.get("avatarPics") or [profile.get("avatar")]) if (url := media_url(value))
        ][:4]
        avatar = ui.element("div").classes(css + (" im-avatar-grid" if len(pictures) > 1 else ""))
        if on_click:
            avatar.on("click", on_click).classes("cursor-pointer").props('role="button" tabindex=0')
            avatar.on("keydown.enter", on_click)
        with avatar:
            if pictures:
                for url in pictures:
                    source = service.im_images.url(self.account_id, url)
                    service.im_images.schedule(self.account_id, [url])
                    image = (
                        ui.image(source)
                        .props("no-spinner loading=eager decoding=async no-transition")
                        .classes("im-avatar-image")
                    )
                    with image.add_slot("error"):
                        ui.label(name[:1] if name and not name.isdigit() else "人").classes("absolute-center")
            else:
                ui.label(
                    name[:1]
                    if name and not name.isdigit()
                    else "群"
                    if profile.get("chatType") == "groupChat"
                    else "人"
                )

    def im_local_read_label(self, key):
        if self.im_unread.get(key):
            return f"{self.im_unread[key]} 条未读"
        if service.store.get("im-read", f"{self.account_id}:{key}", 0):
            return "本地已读"
        return "无未读" if key in self.im_unread else "未同步"

    def render_im_groups(self):
        self.group_region.clear()
        groups = sorted(self.im_groups, key=self.im_latest_time, reverse=True)
        visible = [
            key
            for key in groups
            if self.im_query.casefold() in self.im_group_name(key).casefold()
            and (self.im_filter == "all" or self.im_groups[key]["chatType"] == self.im_filter)
        ]
        with self.group_region:
            for key in visible:
                name, conversation = self.im_group_name(key), self.im_groups[key]
                latest = self.im_latest(key)
                label = ("群聊 " if conversation["chatType"] == "groupChat" else "私聊 ") + name
                with (
                    ui.button(on_click=lambda k=key: guarded(lambda: self.group_history(k)))
                    .props(f"flat no-caps aria-label={json.dumps('打开' + label, ensure_ascii=False)}")
                    .classes("im-conversation" + (" selected" if key == self.im_selected else ""))
                ):
                    with ui.row().classes("w-full items-center no-wrap gap-3"):
                        self.draw_im_avatar(conversation, name)
                        with ui.column().classes("im-conversation-copy"):
                            with ui.row().classes("w-full items-center no-wrap gap-2"):
                                ui.label(name).classes("im-conversation-name")
                                ui.label(time_label(latest)).classes("im-conversation-time")
                            ui.label(message_preview(latest) if latest else "点击查看聊天记录").classes("im-preview")
                            label = self.im_local_read_label(key)
                            ui.label(label).classes(
                                "im-conversation-read unread" if self.im_unread.get(key) else "im-conversation-read"
                            )
                        if self.im_unread.get(key):
                            ui.badge(str(self.im_unread[key])).classes("im-unread")
            if not visible:
                with ui.column().classes("im-list-empty"):
                    ui.icon("forum", size="32px")
                    ui.label("没有找到相关会话" if groups else "连接消息服务后，会话会显示在这里")

    def render_im_chat(self, loading=False, scroll_bottom=True):
        conversation = self.im_groups.get(self.im_selected, {})
        self.im_chat_header.clear()
        with self.im_chat_header:
            self.draw_im_avatar(conversation, self.im_group_name(self.im_selected))
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(self.im_group_name(self.im_selected) if self.im_selected else "消息").classes("im-chat-title")
                subtitle = "群聊" if conversation.get("chatType") == "groupChat" else "私聊"
                state = self.im_local_read_label(self.im_selected)
                ui.label(
                    "正在同步云端消息…"
                    if loading
                    else f"{subtitle} · {state}"
                    if self.im_selected
                    else "选择会话开始聊天"
                ).classes("muted text-xs")
            ui.space()
            if self.im_selected:
                ui.button(icon="refresh", on_click=lambda: guarded(lambda: self.group_history(self.im_selected))).props(
                    'flat round aria-label="刷新聊天记录"'
                )
        self.message_region.clear()
        with self.message_region:
            messages = [m for m in self.im_history.get(self.im_selected, []) if visible_message(m)]
            if self.im_selected and not self.im_pages.get(self.im_selected, {}).get("isLast", False):
                with ui.row().classes("w-full justify-center"):
                    ui.button("加载更早消息", icon="history", on_click=lambda: guarded(self.load_older_im)).props(
                        "flat dense"
                    )
            if not self.im_selected or not messages:
                with ui.column().classes("im-chat-empty"):
                    ui.icon("question_answer", size="54px")
                    ui.label(
                        "选择一个会话" if not self.im_selected else "正在加载消息" if loading else "暂无聊天记录"
                    ).classes("text-lg font-semibold")
                    ui.label("支持群聊、私聊和云端消息漫游").classes("muted text-sm")
            previous_day = ""
            for message in messages:
                day = time_label(message, full=True).split(" ")[0]
                if day and day != previous_day:
                    ui.label(day).classes("im-date-divider")
                    previous_day = day
                self.draw_im_message(message)
        self.im_scroll.scroll_to(percent=1 if scroll_bottom else 0)

    def draw_im_message(self, message):
        if not visible_message(message):
            return
        if message.get("type") == "cmd":
            ui.label(system_notice(message)).classes("im-system-notice")
            return
        key = message_session(message, self.im_self_id())
        mid = str(message.get("serverMsgId") or message.get("id") or "")
        recalled = self.im_message_recalled(message, key)
        info = attachment_info(message)
        sender = str(message.get("from") or "课堂消息").split("/")[0]
        mine = sender == self.im_self_id()
        profile = self.im_profiles.get(session_key("singleChat", sender), {})
        nickname = "我" if mine else profile.get("name") or sender
        with ui.row().classes("im-message" + (" mine" if mine else "")):
            self.draw_im_avatar(
                profile,
                nickname,
                "im-message-avatar",
                on_click=lambda uid=sender: guarded(lambda: self.start_im_private(uid)),
            )
            with ui.column().classes("im-message-content"):
                with ui.row().classes("im-message-meta items-center gap-2"):
                    ui.button(nickname, on_click=lambda uid=sender: guarded(lambda: self.start_im_private(uid))).props(
                        "flat dense"
                    ).classes("im-nickname")
                    ui.label(time_label(message, full=True))
                    if mine:
                        receipt = service.store.get("im-receipts", f"{self.account_id}:{key}:{mid}", {})
                        label = receipt_label(message, receipt)
                        ui.label(label).classes(
                            "im-message-read confirmed" if label == "已读" else "im-message-read"
                        ).tooltip("对方已读状态以收到的回执为准；待确认不代表对方一定未读")
                    else:
                        ui.label("本地已读").classes("im-message-read")
                with ui.column().classes(
                    "im-bubble" + (" im-activity" if info else "") + (" im-recalled" if recalled else "")
                ):
                    if recalled:
                        ui.label("已撤回").classes("im-recalled-label")
                    if info:
                        with ui.row().classes("items-center gap-2 text-teal-700"):
                            ui.icon("campaign", size="18px")
                            ui.label("课堂活动").classes("text-xs font-semibold")
                    url = media_url(message.get("url") or (message.get("body") or {}).get("url"))
                    if message.get("type") == "img" and url:
                        source = service.im_images.url(self.account_id, url)
                        service.im_images.register_message(self.account_id, message)
                        service.im_images.schedule(self.account_id, message_images(message))
                        size = message.get("size") or (message.get("body") or {}).get("size") or {}
                        try:
                            ratio = max(0.25, min(4, float(size.get("width", 4)) / float(size.get("height", 3))))
                        except (AttributeError, TypeError, ValueError, ZeroDivisionError):
                            ratio = 4 / 3
                        image = (
                            ui.image(source)
                            .props(f"no-spinner loading=eager decoding=async no-transition fit=contain ratio={ratio}")
                            .classes("im-inline-image")
                        )
                        with image.add_slot("error"):
                            with ui.column().classes("absolute-full flex-center gap-1"):
                                ui.label("图片暂时无法加载").classes("text-xs")
                                ui.button("重新加载", on_click=image.force_reload).props("flat dense size=sm")
                    elif message.get("type") in ("file", "audio", "video") and url:
                        ui.link(message.get("filename") or message_preview(message), url, new_tab=True).classes(
                            "im-file-link"
                        )
                    else:
                        ui.label(message_preview(message)).classes("im-message-text")
                    if info.get("aid"):

                        async def open_message():
                            ci = info.get("courseInfo") or {}
                            course = {"courseId": str(ci.get("courseid", "")), "classId": str(ci.get("classid", ""))}
                            active = {
                                "id": str(info["aid"]),
                                "type": int(info.get("atype", 2)),
                                "name": info.get("title", ""),
                                "url": info.get("url", ""),
                                "signType": "0",
                            }
                            await self.open_activity(course, active, group=not bool(course["courseId"]))

                        ui.button("打开活动", icon="arrow_forward", on_click=lambda: guarded(open_message)).props(
                            "flat dense"
                        )
