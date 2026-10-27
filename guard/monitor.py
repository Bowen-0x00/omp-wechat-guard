"""本地 omp 状态监控模块 (通过 Collab Named Pipe / 会话日志实时发现状态与异常)."""

import os
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests
from loguru import logger
from .config import AppConfig


class LocalOmpMonitor:
    """本地 omp 守护监控器."""

    def __init__(self, config: AppConfig, report_url: Optional[str] = None):
        self.config = config
        self.report_url = report_url or f"http://127.0.0.1:{config.server.port}"
        self.api_secret = config.server.api_secret

        self.last_state_hash = ""
        self.last_error_time = 0.0

    @staticmethod
    def get_active_collab_hosts() -> List[Dict[str, Any]]:
        """从 ~/.omp/run/collab-hosts/ 读取并查询所有本地活跃的 Collab Host."""
        home = Path.home()
        hosts_dir = home / ".omp" / "run" / "collab-hosts"
        if not hosts_dir.exists():
            return []

        active_hosts = []
        for file in hosts_dir.glob("*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    meta = json.load(f)

                pipe_path = meta.get("endpoint")
                token = meta.get("token")
                if not pipe_path or not token:
                    continue

                # 查询 snapshot
                with open(pipe_path, "r+b", buffering=0) as pipe:
                    req = json.dumps({"v": 1, "op": "snapshot", "token": token}).encode("utf-8") + b"\n"
                    pipe.write(req)
                    res_raw = pipe.readline()
                    res = json.loads(res_raw.decode("utf-8"))
                    if res.get("ok"):
                        snapshot = res.get("snapshot", {})

                        # 查询 link
                        link_req = json.dumps({"v": 1, "op": "link", "token": token, "access": "control", "generation": snapshot.get("generation", 1)}).encode("utf-8") + b"\n"
                        pipe.write(link_req)
                        link_res = json.loads(pipe.readline().decode("utf-8"))
                        if link_res.get("ok"):
                            snapshot["url"] = link_res.get("url")

                        active_hosts.append(snapshot)
            except Exception as e:
                logger.debug(f"[Monitor] 读取 Collab Named Pipe 异常 (可能已退出): {e}")

        return active_hosts

    def check_and_report_once(self) -> None:
        """执行单次状态巡检并上报."""
        hosts = self.get_active_collab_hosts()
        if not hosts:
            return

        for host in hosts:
            session_name = host.get("sessionName", "未命名会话")
            is_busy = host.get("busy", False)
            input_required = host.get("inputRequired", False)
            collab_url = host.get("url", "")
            model_info = host.get("model", {})
            model_name = f"{model_info.get('provider', '')}/{model_info.get('id', '')}" if isinstance(model_info, dict) else str(model_info)

            # 上报会话状态同步
            status_payload = {
                "session_name": session_name,
                "cwd": host.get("cwd", ""),
                "model": model_name,
                "busy": is_busy,
                "input_required": input_required,
                "collab_url": collab_url
            }

            try:
                requests.post(
                    f"{self.report_url}/api/event",
                    headers={"X-API-Secret": self.api_secret},
                    json={"type": "status", "data": status_payload},
                    timeout=5
                )
            except Exception as e:
                logger.debug(f"[Monitor] 同步状态至守护服务异常: {e}")

    def poll_loop(self, interval_seconds: float = 3.0):
        """持续轮询监控主循环."""
        logger.info(f"[Monitor] 本地 omp 监控器已启动，巡检间隔: {interval_seconds}s")
        while True:
            try:
                self.check_and_report_once()
            except Exception as e:
                logger.error(f"[Monitor] 轮询异常: {e}")
            time.sleep(interval_seconds)
