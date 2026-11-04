import argparse
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from nicegui import app, ui

from app.api import router
from app.auth import AdminAuth, AdminMiddleware, create_auth_router
from app.homework_media import homework_images
from app.providers.base import ProviderError
from app.service import service
from app.ui import index  # noqa: F401

app.title = "课程助手"
app.include_router(router)
app.include_router(homework_images.router(service))
admin_auth = AdminAuth(service.store)
app.include_router(create_auth_router(admin_auth))
app.add_middleware(AdminMiddleware, auth=admin_auth)
app.add_static_files("/assets", str(Path(__file__).parent / "app/static"))
app.on_shutdown(service.close)


@app.exception_handler(ProviderError)
async def provider_error(request: Request, exc: ProviderError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "application": "course-helper"}


def main():
    parser = argparse.ArgumentParser(description="课程助手")
    parser.add_argument("--host", default="127.0.0.1", help="后端监听地址")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-open", action="store_true", help="兼容旧命令；服务始终不自动打开浏览器")
    args = parser.parse_args()
    secret = service.store.get("config", "ui-secret")
    if not secret:
        import secrets

        secret = secrets.token_urlsafe(32)
        service.store.put("config", "ui-secret", secret)
    app.on_startup(lambda: print(f"课程助手已启动：http://{args.host}:{args.port}", flush=True))
    ui.run(
        title="课程助手",
        favicon="🎓",
        host=args.host,
        port=args.port,
        show=False,
        reload=False,
        storage_secret=secret,
        fastapi_docs=True,
        language="zh-CN",
        show_welcome_message=False,
    )


if __name__ == "__main__":
    main()
