"""配置加载与验证模块."""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import yaml
from loguru import logger


@dataclass
class WeChatConfig:
    corp_id: str
    agent_id: int
    secret: str
    token: str
    encoding_aes_key: str
    to_user: str = "@all"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8089
    public_url: str = "http://your-server-domain.com"
    api_secret: str = "YOUR_CUSTOM_API_SECRET"


@dataclass
class GuardConfig:
    include_collab_link: bool = True
    ask_timeout_seconds: int = 300
    alert_cooldown_seconds: int = 60
    default_enabled: bool = True


@dataclass
class AppConfig:
    wechat: WeChatConfig
    server: ServerConfig = field(default_factory=ServerConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """加载应用配置，优先从指定路径加载，其次查找同级目录下的 config.yaml."""
    paths_to_try = []
    if config_path:
        paths_to_try.append(Path(config_path))
    
    current_dir = Path(__file__).resolve().parent.parent
    paths_to_try.extend([
        current_dir / "config.yaml",
        current_dir / "config.example.yaml"
    ])

    loaded_data = None
    used_path = None
    for p in paths_to_try:
        if p.exists() and p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    loaded_data = yaml.safe_load(f)
                    used_path = p
                    break
            except Exception as e:
                logger.warning(f"[Config] 读取配置文件 {p} 失败: {e}")

    if not loaded_data:
        raise FileNotFoundError(f"未找到可用的配置文件，尝试路径: {[str(p) for p in paths_to_try]}")

    logger.info(f"[Config] 成功加载配置文件: {used_path}")

    # 读取并构造 WeChatConfig
    wc_data = loaded_data.get("wechat", {})
    wechat_cfg = WeChatConfig(
        corp_id=str(wc_data.get("corp_id", "")).strip(),
        agent_id=int(wc_data.get("agent_id", 1000006)),
        secret=str(wc_data.get("secret", "")).strip(),
        token=str(wc_data.get("token", "")).strip(),
        encoding_aes_key=str(wc_data.get("encoding_aes_key", "")).strip(),
        to_user=str(wc_data.get("to_user", "@all")).strip() or "@all"
    )

    # 读取 ServerConfig
    sc_data = loaded_data.get("server", {})
    server_cfg = ServerConfig(
        host=str(sc_data.get("host", "0.0.0.0")),
        port=int(sc_data.get("port", 8089)),
        public_url=str(sc_data.get("public_url", "http://your-server-domain.com")),
        api_secret=str(sc_data.get("api_secret", "YOUR_CUSTOM_API_SECRET"))
    )

    # 读取 GuardConfig
    gc_data = loaded_data.get("guard", {})
    guard_cfg = GuardConfig(
        include_collab_link=bool(gc_data.get("include_collab_link", True)),
        ask_timeout_seconds=int(gc_data.get("ask_timeout_seconds", 300)),
        alert_cooldown_seconds=int(gc_data.get("alert_cooldown_seconds", 60)),
        default_enabled=bool(gc_data.get("default_enabled", True))
    )

    return AppConfig(wechat=wechat_cfg, server=server_cfg, guard=guard_cfg)
