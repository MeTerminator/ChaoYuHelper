"""课程助手浏览器界面。"""

from nicegui import ui

from ..homework_content import clean_homework_html
from ..homework_media import homework_images
from ..providers.chaoxing import text


def rich(content):
    return ui.html(str(content or ""), sanitize=True).classes("question-content w-full")


def homework_rich(content, account_id, **kwargs):
    cleaned = clean_homework_html(content, lambda url: homework_images.url(account_id, url), **kwargs)
    return ui.html(cleaned, sanitize=True).classes("homework-content w-full")


def field(label, value="", password=False):
    return (
        ui.input(label, value=value, password=password, password_toggle_button=password)
        .props("outlined dense")
        .classes("w-full")
    )


def primary(label, callback, icon=None):
    return ui.button(label, on_click=callback, icon=icon).classes("primary-btn")


async def guarded(operation):
    # Event callbacks can delete the button/slot which invoked them during a render.
    # Keep subsequent notifications and dialogs bound to the stable client root.
    client = ui.context.client
    with client:
        try:
            return await operation()
        except Exception as exc:
            ui.notify(str(exc), type="negative", timeout=9000, close_button=True)
            return None


def options_for(q, rain=False):
    options = q.get("options" if rain else "answer") or []
    result = {}
    for index, option in enumerate(options):
        key = str(option.get("key" if rain else "name", chr(65 + index)))
        result[key] = f"{key} · {text(option.get('value' if rain else 'content', ''))}"
    return result
