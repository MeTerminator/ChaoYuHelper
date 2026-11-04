"""Small presentation helpers for IM conversations."""

import json
from datetime import datetime


def attachment_info(message):
    ext = message.get("ext") or message.get("attributes") or {}
    attachment = ext.get("attachment") or {}
    if isinstance(attachment, str):
        try:
            attachment = json.loads(attachment)
        except (ValueError, TypeError):
            attachment = {}
    if not isinstance(attachment, dict):
        return {}
    info = attachment.get("att_chat_course") or attachment.get("att_sign") or {}
    return info if isinstance(info, dict) else {}


def message_preview(message):
    if message.get("_recalled"):
        return "[已撤回] " + message_preview({**message, "_recalled": False})
    if message.get("type") == "cmd":
        return system_notice(message)
    info = attachment_info(message)
    return str(
        info.get("title")
        or message.get("msg")
        or {
            "img": "[图片]",
            "file": "[文件]",
            "audio": "[语音]",
            "video": "[视频]",
            "cmd": "[系统通知]",
            "custom": "[活动消息]",
        }.get(message.get("type"), "[消息]")
    )


def message_time(message):
    try:
        value = float(message.get("time") or message.get("timestamp") or 0)
        return value / 1000 if value > 100_000_000_000 else value
    except (ValueError, TypeError):
        return 0


def time_label(message, full=False):
    value = message_time(message)
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(value).strftime("%m-%d %H:%M" if full else "%H:%M")
    except (ValueError, OSError, OverflowError):
        return ""


def conversation_id(message):
    if message.get("chatType") not in (None, "groupChat"):
        return ""
    return str(message.get("to") or "").split("/")[0]


def merge_messages(*batches):
    messages = {}
    for batch in batches:
        for message in batch:
            key = str(
                message.get("serverMsgId")
                or message.get("id")
                or message.get("mid")
                or json.dumps(message, sort_keys=True, default=str)
            )
            messages[key] = message
    return sorted(messages.values(), key=message_time)


def session_key(chat_type, target_id):
    if chat_type not in ("groupChat", "singleChat") or not target_id:
        return ""
    return f"{chat_type}:{target_id}"


def message_session(message, my_user_id):
    chat_type = message.get("chatType") or "groupChat"
    sender = str(message.get("from") or "").split("/")[0]
    target = str(message.get("to") or "").split("/")[0]
    if chat_type == "singleChat" and sender and sender != str(my_user_id):
        target = sender
    return session_key(chat_type, target)


def media_url(value):
    from urllib.parse import urlsplit

    if not isinstance(value, str):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("http://"):
        value = "https://" + value[7:]
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        domains = ("chaoxing.com", "chaoxing.com.cn", "easemob.com", "easecdn.com", "cldisk.com", "xuexi365.com")
        if url.scheme == "https" and not url.username and any(host == d or host.endswith("." + d) for d in domains):
            return value
    except ValueError:
        pass
    return ""


def message_ext(message):
    value = message.get("ext") or message.get("attributes") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def system_notice(message):
    """Only command actions rendered by the official Web IM are human notices."""
    if message.get("type") != "cmd":
        return ""
    action = message.get("action")
    data = message_ext(message).get("data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return ""
    if not isinstance(data, dict):
        return ""
    if action == "CMD_MESSAGE_TIP":
        return str(data.get("tip") or "")
    if action == "CMD_GROUP_CHAT_INVITATION":
        users = data.get("listUser") or []
        names = [str(u.get("name") or u.get("uname") or "") for u in users if isinstance(u, dict)]
        names = [n for n in names if n]
        return "、".join(names) + " 加入了群聊" if names else ""
    return ""


def visible_message(message):
    if message.get("type") in ("read", "channel", "delivery"):
        return False
    return message.get("type") != "cmd" or bool(system_notice(message))


def receipt_label(message, receipt=None):
    receipt = receipt or {}
    if message.get("chatType") == "groupChat":
        count = receipt.get("count")
        return f"{count} 人已读" if isinstance(count, int) and count > 0 else "已发送 · 群已读未知"
    if receipt.get("read") or message.get("isRead") is True:
        return "已读"
    if message.get("isRead") is False:
        return "未读"
    return "待确认已读"


def control_update(message, my_user_id):
    """Decode received synchronization events without sending acknowledgements."""
    ext = message_ext(message)
    sender = str(message.get("from") or "").split("/")[0]
    action, kind = message.get("action"), message.get("type")
    if action == "CMD_READ_ACK" and sender == str(my_user_id):
        chat_type = "groupChat" if ext.get("conversationType") in (1, "1") else "singleChat"
        key = session_key(chat_type, ext.get("conversationId"))
        try:
            stamp = float(ext.get("timestamp") or 0)
            stamp = stamp / 1000 if stamp > 100_000_000_000 else stamp
        except (ValueError, TypeError):
            stamp = 0
        if key and stamp > 0:
            return {"kind": "local-read", "key": key, "time": stamp}
    if kind == "read":
        mid = str(message.get("mid") or message.get("ackId") or "")
        if message.get("chatType") == "groupChat" or message.get("groupReadCount"):
            counts = message.get("groupReadCount") or {}
            if isinstance(counts, dict):
                return {"kind": "group-read", "key": session_key("groupChat", message.get("to")), "counts": counts}
        if sender and sender != str(my_user_id) and mid:
            return {"kind": "peer-read", "key": session_key("singleChat", sender), "mid": mid}
    if kind == "channel" and sender and sender != str(my_user_id):
        return {"kind": "peer-channel", "key": session_key("singleChat", sender)}
    if action == "REVOKE_FLAG" or kind == "recall":
        mid = str(ext.get("msgId") or message.get("mid") or message.get("ackId") or "")
        key = message_session(message, my_user_id)
        if mid and key:
            return {"kind": "recall", "key": key, "mid": mid, "sender": sender}
    return {}
