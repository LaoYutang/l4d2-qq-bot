"""Same-origin configuration UI, using the environment password directly in a header."""

import hmac
import json
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError

from .config import Settings
from .config_store import ConfigConflictError, ConfigStore

STATIC_PATH = Path(__file__).with_name("static")
PASSWORD_HEADER = "X-Config-Password"


def create_web_app(settings: Settings, store: ConfigStore) -> web.Application:
    password = settings.web_password.get_secret_value()
    if not password:
        raise ValueError("请设置 L4D2_WEB_PASSWORD 后启用配置界面")

    @web.middleware
    async def protect(request: web.Request, handler):
        if request.path.startswith("/api/") and not hmac.compare_digest(
            request.headers.get(PASSWORD_HEADER, "").encode("utf-8"), password.encode("utf-8")
        ):
            response = web.json_response({"error": "密码错误"}, status=401)
        else:
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                response = exc
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def result(config, revision, *, saved=False):
        return web.json_response(
            {"config": config.model_dump(), "revision": revision, "saved": saved}
        )

    async def get_config(request):
        try:
            return result(*(await store.read()))
        except (OSError, ValueError):
            return web.json_response({"error": "读取 config.json 失败，请检查配置文件"}, status=500)

    async def save_config(request):
        if request.content_type != "application/json":
            return web.json_response({"error": "请提交 JSON 配置"}, status=415)
        try:
            payload = await request.json()
            if (
                not isinstance(payload, dict)
                or "config" not in payload
                or not isinstance(payload.get("revision"), str)
            ):
                raise ValueError("请求中缺少 config 对象或 revision")
            return result(*(await store.save(payload["config"], payload["revision"])), saved=True)
        except ConfigConflictError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except ValidationError as exc:
            errors = [
                {"field": ".".join(map(str, error["loc"])), "message": error["msg"]}
                for error in exc.errors(include_input=False, include_url=False)
            ]
            return web.json_response({"error": "配置校验失败", "errors": errors}, status=400)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return web.json_response({"error": "配置或请求格式错误"}, status=400)
        except OSError:
            return web.json_response(
                {"error": "保存 config.json 失败，请检查数据目录写入权限"}, status=500
            )

    async def index(request):
        return web.FileResponse(STATIC_PATH / "index.html")

    app = web.Application(middlewares=[protect], client_max_size=1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/api/config", get_config)
    app.router.add_post("/api/config", save_config)
    for name in ("app.js", "style.css"):

        async def asset(request, filename=name):
            return web.FileResponse(STATIC_PATH / filename)

        app.router.add_get("/" + name, asset)
    return app
