"""企业微信 API 客户端模块 (Token 管理与多模态消息推送)."""

import time
from typing import Optional, Dict, Any
import requests
from loguru import logger
from .config import WeChatConfig


class WeComClient:
    """企业微信官方 API 客户端."""

    def __init__(self, cfg: WeChatConfig):
        self.corp_id = cfg.corp_id.strip()
        self.agent_id = int(cfg.agent_id)
        self.secret = cfg.secret.strip()
        self.default_to_user = cfg.to_user.strip() or "@all"

        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def get_access_token(self, force_refresh: bool = False) -> str:
        """获取并缓存企业微信 access_token (提前 300 秒续期)."""
        now = time.time()
        if not force_refresh and self._token and now < self._token_expires_at:
            return self._token

        url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        params = {"corpid": self.corp_id, "corpsecret": self.secret}

        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data.get("errcode") != 0:
                errcode = data.get("errcode")
                errmsg = data.get("errmsg")
                if errcode == 60020:
                    logger.error(
                        f"[WeComClient] 鉴权失败 (60020): 当前 IP 未在应用 {self.agent_id} 的『企业可信IP』白名单中！"
                        f"\n👉 请登录企业微信后台 -> 应用管理 -> 选择应用 -> 在『企业可信IP』中填入当前服务器/出口IP。"
                    )
                raise RuntimeError(f"获取 Access Token 失败 [{errcode}]: {errmsg}")

            self._token = data["access_token"]
            self._token_expires_at = now + data.get("expires_in", 7200) - 300
            logger.debug("[WeComClient] 成功获取/刷新 access_token")
            return self._token
        except Exception as e:
            logger.error(f"[WeComClient] 获取 access_token 异常: {e}")
            raise

    def send_message(self, payload: Dict[str, Any]) -> bool:
        """通用发送企业微信应用消息接口."""
        try:
            token = self.get_access_token()
            url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"

            if "touser" not in payload or not payload["touser"]:
                payload["touser"] = self.default_to_user
            if "agentid" not in payload:
                payload["agentid"] = self.agent_id

            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            errcode = data.get("errcode", -1)

            if errcode == 0:
                logger.info(f"[WeComClient] 消息推送成功 -> {payload['touser']} ({payload.get('msgtype')})")
                return True
            elif errcode == 42001 or errcode == 40014:  # Token 过期或不合法，重试一次
                logger.warning(f"[WeComClient] Token 失效 ({errcode})，尝试强制刷新后重发...")
                token = self.get_access_token(force_refresh=True)
                url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
                resp2 = requests.post(url, json=payload, timeout=10)
                data2 = resp2.json()
                if data2.get("errcode") == 0:
                    logger.info(f"[WeComClient] 重发消息成功 -> {payload['touser']}")
                    return True
                else:
                    logger.error(f"[WeComClient] 重发失败: {data2}")
                    return False
            elif errcode == 60020:
                logger.error(
                    f"[WeComClient] 消息推送被拦截 (60020): IP 白名单未放行当前机器 IP。"
                    f"请确保应用 {self.agent_id} 的企业可信IP已配置当前 IP！"
                )
                return False
            else:
                logger.warning(f"[WeComClient] 消息推送未成功: {data}")
                return False
        except Exception as e:
            logger.error(f"[WeComClient] 发送消息网络异常: {e}")
            return False

    def send_text(self, content: str, to_user: str = "") -> bool:
        """发送纯文本消息."""
        payload = {
            "touser": to_user or self.default_to_user,
            "msgtype": "text",
            "agentid": self.agent_id,
            "text": {"content": content.strip()},
            "safe": 0
        }
        return self.send_message(payload)

    def send_markdown(self, markdown_content: str, to_user: str = "") -> bool:
        """发送 Markdown 消息."""
        payload = {
            "touser": to_user or self.default_to_user,
            "msgtype": "markdown",
            "agentid": self.agent_id,
            "markdown": {"content": markdown_content.strip()}
        }
        return self.send_message(payload)

    def send_textcard(
        self,
        title: str,
        description: str,
        url: str,
        btntxt: str = "立即前往操作",
        to_user: str = ""
    ) -> bool:
        """发送图文文本卡片 (TextCard)，可直接点击链接."""
        payload = {
            "touser": to_user or self.default_to_user,
            "msgtype": "textcard",
            "agentid": self.agent_id,
            "textcard": {
                "title": title.strip(),
                "description": description.strip(),
                "url": url.strip(),
                "btntxt": btntxt.strip()
            }
        }
        return self.send_message(payload)

    def _split_markdown_chunks(self, content: str, max_bytes: int = 1900) -> list:
        """将长 Markdown 在段落边界智能切片，避免企业微信 2048 字节截断."""
        if len(content.encode("utf-8")) <= max_bytes:
            return [content]

        paragraphs = content.split("\n\n")
        chunks = []
        current_chunk = ""

        for p in paragraphs:
            candidate = f"{current_chunk}\n\n{p}" if current_chunk else p
            if len(candidate.encode("utf-8")) <= max_bytes:
                current_chunk = candidate
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                if len(p.encode("utf-8")) > max_bytes:
                    lines = p.split("\n")
                    sub_chunk = ""
                    for line in lines:
                        sub_cand = f"{sub_chunk}\n{line}" if sub_chunk else line
                        if len(sub_cand.encode("utf-8")) <= max_bytes:
                            sub_chunk = sub_cand
                        else:
                            if sub_chunk:
                                chunks.append(sub_chunk)
                            sub_chunk = line
                    current_chunk = sub_chunk
                else:
                    current_chunk = p
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def send_dual_notification(
        self,
        title: str,
        summary: str,
        details: str,
        markdown_content: str,
        url: Optional[str] = None,
        btntxt: str = "立即操作",
        to_user: str = ""
    ) -> bool:
        """
        双通道推送:
        1. 企业微信端: 发送 Markdown 富文本 (享受极致排版、加粗、列表与代码块)
        2. 个人微信端: 发送 Textcard 原生卡片 (个人微信插件/微工作台原生完整支持展现)
        """
        logger.info("[WeComClient] 正在执行双通道推送 (1. 企微Markdown -> 2. 微信原生卡片)...")
        # 1. 企微端发送 Markdown
        chunks = self._split_markdown_chunks(markdown_content)
        ok_md = True
        for i, chunk in enumerate(chunks):
            if i > 0:
                time.sleep(0.3)
                chunk = f"> *(接上条...)*\n\n{chunk}"
            ok = self.send_markdown(chunk, to_user=to_user)
            ok_md = ok_md and ok

        # 停顿 0.6 秒确保时序平稳
        time.sleep(0.6)

        # 2. 个人微信端发送原生文本卡片 (TextCard)
        target_url = url or "https://work.weixin.qq.com"
        description = f"<div class=\"gray\">{summary}</div><div class=\"normal\">{details}</div>"
        ok_card = self.send_textcard(
            title=title[:120],
            description=description[:512],
            url=target_url,
            btntxt=btntxt[:8],
            to_user=to_user
        )

        return ok_md or ok_card
