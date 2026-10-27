"""HTTP 接口服务模块 (处理微信回调指令转发与本地事件上报接口)."""

import json
import time
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional
from loguru import logger

from .config import AppConfig
from .service import GuardService


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """支持多线程并发处理请求的 HTTPServer."""
    daemon_threads = True


class GuardHTTPRequestHandler(BaseHTTPRequestHandler):
    """请求处理器."""

    server_service: GuardService
    api_secret: str

    def log_message(self, format, *args):
        # 统一由 loguru 输出日志
        logger.debug(f"[HTTP] {self.address_string()} - {format % args}")

    def _check_auth(self, params: dict) -> bool:
        """检查 API 秘钥."""
        token = self.headers.get("X-API-Secret") or params.get("secret", [""])[0]
        return token == self.api_secret

    def _send_json(self, code: int, data: dict):
        """返回 JSON 格式响应."""
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        """支持 CORS 预检."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Secret")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # 1. 探活接口
        if parsed.path in ("/healthz", "/ping"):
            self._send_json(200, {"status": "ok", "time": time.time()})
            return

        # 2. 状态查询接口
        if parsed.path in ("/api/status", "/status"):
            status_data = {
                "enabled": self.server_service.is_enabled,
                "is_active": self.server_service.is_notifications_active(),
                "session": self.server_service.session_info,
                "has_pending_question": bool(self.server_service.pending_question),
                "has_pending_answer": bool(self.server_service.pending_answer)
            }
            self._send_json(200, {"code": 0, "data": status_data})
            return

        # 3. 本地拉取待消费答复接口 (GET /api/pending)
        if parsed.path in ("/api/pending", "/api/answer", "/pending", "/answer"):
            if not self._check_auth(params):
                self._send_json(401, {"code": 401, "error": "Unauthorized"})
                return

            ans = self.server_service.pop_pending_answer()
            self._send_json(200, {"code": 0, "answer": ans})
            return

        self._send_json(404, {"code": 404, "error": "Not Found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len).decode("utf-8", errors="replace")

        # 1. 来自企业微信网关 (wechat_obsidian) 的指令转发: POST /command
        if parsed.path in ("/command", "/wechat/command", "/api/command", "/api/guard/command"):
            try:
                data = json.loads(post_body) if post_body else {}
                cmd = (data.get("command") or data.get("content") or "").strip()
                from_user = data.get("from_user", "")

                logger.info(f"[Server] 收到微信转发指令: '{cmd}' (来自: {from_user})")
                reply = self.server_service.handle_wechat_command(cmd, from_user)
                self._send_json(200, {"code": 0, "msg": "Command executed", "reply": reply})
            except Exception as e:
                logger.error(f"[Server] 处理指令异常: {e}")
                self._send_json(500, {"code": 500, "error": str(e)})
            return

        # 2. 接收来自 omp 本地扩展或监控器的事件上报: POST /api/event
        if parsed.path in ("/api/event", "/event", "/api/guard/event"):
            if not self._check_auth(params):
                self._send_json(401, {"code": 401, "error": "Unauthorized"})
                return

            try:
                payload = json.loads(post_body)
                event_type = payload.get("type", "")
                event_data = payload.get("data", {})

                logger.info(f"[Server] 收到 omp 事件上报: type='{event_type}'")

                if event_type == "ask":
                    ok = self.server_service.on_ask_event(event_data)
                    self._send_json(200, {"code": 0, "msg": "Ask event processed", "notified": ok})
                elif event_type == "error":
                    ok = self.server_service.on_error_event(event_data)
                    self._send_json(200, {"code": 0, "msg": "Error event processed", "notified": ok})
                elif event_type in ("status", "status_update"):
                    self.server_service.update_session_status(event_data)
                    self._send_json(200, {"code": 0, "msg": "Status updated"})
                else:
                    self._send_json(400, {"code": 400, "error": f"Unknown event type: {event_type}"})
            except Exception as e:
                logger.error(f"[Server] 处理事件上报异常: {e}")
                self._send_json(500, {"code": 500, "error": str(e)})
            return

        # 3. 通用外部通知推送接口: POST /api/notify (支持 GPU 监控、Cron 任务、自动化脚本)
        if parsed.path in ("/api/notify", "/notify", "/api/guard/notify"):
            try:
                data = json.loads(post_body) if post_body else {}
            except Exception:
                data = {}

            # 灵活鉴权: 支持 Header 'X-API-Secret'、query param '?secret=...'、或 JSON '{"secret": "..."}'
            provided_secret = (
                self.headers.get("X-API-Secret")
                or params.get("secret", [""])[0]
                or str(data.get("secret", ""))
            )
            if provided_secret != self.api_secret:
                self._send_json(401, {"code": 401, "error": "Unauthorized: API secret invalid"})
                return

            title = str(data.get("title") or "").strip()
            content = str(data.get("content") or data.get("markdown") or data.get("text") or "").strip()

            if not title and not content:
                self._send_json(400, {"code": 400, "error": "Missing required field: title or content"})
                return

            title = title or "系统通用通知"
            content = content or title

            summary = str(data.get("summary") or "").strip()
            details = str(data.get("details") or "").strip()
            url = str(data.get("url") or "").strip()
            btntxt = str(data.get("btntxt") or "查看详情").strip()
            level = str(data.get("level") or "info").strip()
            host = str(data.get("host") or data.get("tag") or "").strip()
            to_user = str(data.get("to_user") or "").strip()

            logger.info(f"[Server] 收到通用通知推送请求: title='{title}', host='{host}'")
            ok = self.server_service.send_custom_notification(
                title=title,
                content=content,
                summary=summary,
                details=details,
                url=url,
                btntxt=btntxt,
                level=level,
                host=host,
                to_user=to_user
            )
            self._send_json(200, {"code": 0, "msg": "Notification sent", "notified": ok})
            return

        self._send_json(404, {"code": 404, "error": "Not Found"})


def run_server(config: AppConfig, service: Optional[GuardService] = None):
    """启动 HTTP 服务."""
    svc = service or GuardService(config)

    # 注入类属性
    GuardHTTPRequestHandler.server_service = svc
    GuardHTTPRequestHandler.api_secret = config.server.api_secret

    server_address = (config.server.host, config.server.port)
    httpd = ThreadedHTTPServer(server_address, GuardHTTPRequestHandler)

    logger.info(f"[Server] 🚀 omp 远程助手服务启动，监听端口: http://{config.server.host}:{config.server.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("[Server] 收到终止信号，正在关闭服务...")
    finally:
        httpd.server_close()
        logger.info("[Server] 服务已停止。")
