"""Bounded date-template rendering; templates never execute code."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .providers.base import ProviderError

DEFAULT_TEMPLATE = "{YY}{MM}{DD}"
DEFAULT_TIMEZONE = "Asia/Shanghai"
TOKENS = {"YYYY", "YY", "MM", "DD", "HH", "mm"}
PARTS = re.compile(r"\{([^{}]*)\}")


def validate_generator(config):
    if not isinstance(config, dict) or config.get("mode") not in ("disabled", "date"):
        raise ProviderError("请选择有效的签到码生成方式")
    template = config.get("template", DEFAULT_TEMPLATE)
    timezone = config.get("timezone", DEFAULT_TIMEZONE)
    if not isinstance(template, str) or not 1 <= len(template) <= 128:
        raise ProviderError("签到码模板需要 1–128 个字符")
    for token in PARTS.findall(template):
        if token not in TOKENS:
            raise ProviderError(f"不支持的占位符：{{{token}}}")
    literal = PARTS.sub("", template)
    if any(char not in "0123456789" for char in literal):
        raise ProviderError("模板只支持数字和日期时间占位符，请检查花括号及大小写")
    if not isinstance(timezone, str) or not 1 <= len(timezone) <= 64:
        raise ProviderError("请填写有效的 IANA 时区，例如 Asia/Shanghai")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ProviderError("无法识别该时区，请使用 Asia/Shanghai、UTC 等 IANA 时区") from exc
    # Each supported token has the same output width as its name.
    length = len(literal) + sum(len(token) for token in PARTS.findall(template))
    if not 1 <= length <= 32:
        raise ProviderError("生成的签到码需要 1–32 位数字")
    return {"mode": config["mode"], "template": template, "timezone": timezone}


def generate_sign_code(config, *, now=None):
    config = validate_generator(config)
    if config["mode"] != "date":
        raise ProviderError("请先在课程设置中启用日期时间模板")
    zone = ZoneInfo(config["timezone"])
    current = datetime.now(zone) if now is None else now.astimezone(zone)
    values = {
        "YYYY": f"{current.year:04d}",
        "YY": f"{current.year % 100:02d}",
        "MM": f"{current.month:02d}",
        "DD": f"{current.day:02d}",
        "HH": f"{current.hour:02d}",
        "mm": f"{current.minute:02d}",
    }
    return PARTS.sub(lambda match: values[match[1]], config["template"]), current
