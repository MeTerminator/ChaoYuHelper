"""Local manual-operation history."""

from datetime import datetime

from nicegui import ui

from ..service import service


class HistoryView:
    def render_history(self):
        self.heading("ACTIVITY LOG", "操作记录", "查看手动提交的逐账号结果。记录保存在后端服务中。")
        rows = list(service.store.all("history").values())[-200:][::-1]
        if not rows:
            self.empty("history", "还没有操作记录", "完成签到、答题或其他提交后，结果会显示在这里。")
        for row in rows:
            with ui.row().classes("activity-row"):
                ui.icon("check_circle" if row["ok"] else "error_outline").classes(
                    "text-teal-700" if row["ok"] else "text-red-600"
                )
                ui.label(row["account"]).classes("font-semibold")
                ui.label("操作成功" if row["ok"] else row.get("error", "")).classes("grow text-sm")
                ui.label(datetime.fromtimestamp(row["time"]).strftime("%m-%d %H:%M:%S")).classes("muted text-xs")
