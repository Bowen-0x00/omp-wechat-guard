"""核心业务与命令调度服务 (管理状态、命令分发、选项队列与通知推送)."""

import time
import re
from typing import Optional, Dict, Any, List
from loguru import logger
from .config import AppConfig
from .wechat_client import WeComClient


class GuardService:
    """omp 守护与远程交互服务."""

    def __init__(self, config: AppConfig, wechat_client: Optional[WeComClient] = None):
        self.config = config
        self.client = wechat_client or WeComClient(config.wechat)

        # 状态控制
        self.is_enabled: bool = config.guard.default_enabled
        self.quiet_until: float = 0.0

        # 当前等待确认的问题上下文
        self.pending_question: Optional[Dict[str, Any]] = None
        # 待消费的答复
        self.pending_answer: Optional[Dict[str, Any]] = None

        # 告警冷却缓存 (alert_key -> timestamp)
        self.alert_cooldowns: Dict[str, float] = {}

        # 最近一次已知会话状态
        self.session_info: Dict[str, Any] = {
            "session_name": "未知会话",
            "cwd": "",
            "model": "未知模型",
            "busy": False,
            "input_required": False,
            "collab_url": "",
            "updated_at": 0.0
        }

    # -------------------------------------------------------------------------
    # 状态与定时逻辑
    # -------------------------------------------------------------------------
    def is_notifications_active(self) -> bool:
        """检查当前是否处于通知开启状态 (且未在免打扰定时窗口内)."""
        if not self.is_enabled:
            return False
        now = time.time()
        if now < self.quiet_until:
            return False
        return True

    def set_quiet_timer(self, seconds: int) -> float:
        """设置免打扰时长 (秒)."""
        now = time.time()
        self.quiet_until = now + seconds
        return self.quiet_until

    def parse_duration(self, text: str) -> Optional[int]:
        """解析如 30m, 1h, 45s, 2d 的时长为秒数."""
        text = text.strip().lower()
        match = re.match(r"^(\d+)\s*([smhd]?)$", text)
        if not match:
            return None
        val, unit = match.groups()
        val = int(val)
        if unit == "s" or not unit:
            return val
        elif unit == "m":
            return val * 60
        elif unit == "h":
            return val * 3600
        elif unit == "d":
            return val * 86400
        return None

    # -------------------------------------------------------------------------
    # 微信指令处理
    # -------------------------------------------------------------------------
    def handle_wechat_command(self, raw_command: str, from_user: str = "") -> str:
        """处理来自企业微信用户在对话框中输入的指令或选项应答."""
        cmd = raw_command.strip()
        logger.info(f"[Service] 处理微信指令/输入: '{cmd}' 来自用户: {from_user or '未知'}")

        if not cmd:
            return ""

        # 1. 帮助命令
        if cmd in ("/help", "help", "帮助", "?", "？"):
            return self._handle_help_cmd()

        # 2. 状态查询命令
        if cmd in ("/status", "status", "状态"):
            return self._handle_status_cmd()

        # 3. Collab 链接查询
        if cmd in ("/collab", "collab", "链接"):
            return self._handle_collab_cmd()

        # 4. 开启通知命令
        if cmd in ("/on", "on", "/开启", "开启", "/start"):
            self.is_enabled = True
            self.quiet_until = 0.0
            reply = "🟢 **omp 远程通知已开启**\n当有选项待办或异常停止时，将实时推送卡片通知。"
            self.client.send_markdown(reply, to_user=from_user)
            return reply

        # 5. 关闭通知命令
        if cmd in ("/off", "off", "/关闭", "关闭", "/stop"):
            self.is_enabled = False
            self.quiet_until = 0.0
            reply = "🔴 **omp 远程通知已关闭**\n所有推送已暂停，您可以随时发送 `/on` 重新恢复。"
            self.client.send_markdown(reply, to_user=from_user)
            return reply

        # 6. 定时命令 (/timer ...)
        if cmd.startswith("/timer") or cmd.startswith("定时"):
            return self._handle_timer_cmd(cmd, from_user)

        # 7. 选项选择应答 (如果不是上述已知系统指令，则尝试作为当前等待选项的应答)
        return self._handle_option_selection(cmd, from_user)

    def _handle_help_cmd(self) -> str:
        """返回格式化的帮助指令菜单."""
        content = """### 🤖 omp 远程助手指令手册

• **/status** : 查看当前 omp 会话状态、运行阶段与待办选项
• **/collab** : 重新获取并推送手机端 Collab 网页控制台链接
• **/on** : 开启通知推送
• **/off** : 暂停通知推送
• **/timer 30m** : 设定 30 分钟免打扰 (或 `/timer on 1h` 1小时后开启)
• **数字序号/选项文本** : 当有待选弹窗时，直接回复数字 (如 `1`、`2`) 完成选择

*(提示: 推送卡片中也提供一键直达的 Collab 网页链接，点击即可在手机浏览器中实时操作)*"""
        self.client.send_dual_notification(
            title="🤖 omp 远程助手指令手册",
            summary="指令速查与远程控制指南",
            details="• /status: 查看运行状态<br/>• /collab: 获取控制台链接<br/>• /on /off: 推送开关<br/>• /timer: 免打扰定时<br/><div class=\"highlight\">👉 收到待选卡片时直接回复数字选择</div>",
            markdown_content=content,
            url=self.session_info.get("collab_url") or "https://work.weixin.qq.com",
            btntxt="查看详情"
        )
        return content

    def _handle_status_cmd(self) -> str:
        """生成详细运行状态报告."""
        now = time.time()
        status_icon = "🟢 开启中" if self.is_notifications_active() else ("⏸️ 免打扰中" if self.quiet_until > now else "🔴 已关闭")
        quiet_info = f" (免打扰至 {time.strftime('%H:%M:%S', time.localtime(self.quiet_until))})" if self.quiet_until > now else ""

        session_name = self.session_info.get("session_name") or "暂无活跃会话"
        model = self.session_info.get("model") or "未知"
        cwd = self.session_info.get("cwd") or "未知"
        is_busy = self.session_info.get("busy", False)
        is_waiting = bool(self.pending_question) or self.session_info.get("input_required", False)
        collab_url = self.session_info.get("collab_url", "")

        state_text = "🟡 等待选项确认 (Waiting for input)" if is_waiting else ("🔵 正在思考/执行任务中..." if is_busy else "⚪ 空闲就绪 (Idle)")

        lines = [
            "### 📊 omp 运行状态报告",
            f"• **监控开关**: {status_icon}{quiet_info}",
            f"• **当前状态**: {state_text}",
            f"• **会话名称**: {session_name}",
            f"• **工作目录**: `{cwd}`" if cwd else "",
            f"• **当前模型**: `{model}`",
        ]

        if self.pending_question:
            q = self.pending_question
            lines.append(f"\n━━━━━━━━━━━━━━━━━━\n**🔔 当前待办问题**: {q.get('question')}")
            for opt in q.get("options", []):
                rec = " *(Recommended)*" if opt.get("recommended") else ""
                lines.append(f"  **{opt.get('index')}**. {opt.get('label')}{rec}")
            lines.append("\n👉 **直接在微信回复序号 (如 `1`) 即可选择！**")

        if collab_url:
            lines.append(f"\n📱 **[点击打开手机端 Collab 网页控制台]({collab_url})**")

        content = "\n".join([line for line in lines if line])
        card_title = "📊 omp 运行状态报告"
        card_summary = f"状态: {state_text} | 开关: {status_icon}"
        card_details = f"<b>会话</b>: {session_name}<br/><b>模型</b>: {model}<br/>"
        if self.pending_question:
            card_details += f"<b>待办问题</b>: {self.pending_question.get('question')}<br/>"
            card_details += "<div class=\"highlight\">👉 微信直接回复序号即可选择</div>"
        elif collab_url:
            card_details += "<div class=\"highlight\">👉 点击下方按钮打开 Collab 控制台</div>"

        self.client.send_dual_notification(
            title=card_title,
            summary=card_summary,
            details=card_details,
            markdown_content=content,
            url=collab_url or "https://work.weixin.qq.com",
            btntxt="打开控制台" if collab_url else "查看详情"
        )
        return content

    def _handle_collab_cmd(self) -> str:
        """回复当前的 Collab 链接."""
        url = self.session_info.get("collab_url", "")
        if not url:
            content = "ℹ️ 当前会话暂未生成或启动 Collab 共享链接。\n您可以在终端输入 `/collab` 启动协同分享。"
        else:
            content = f"### 🔗 omp Collab 网页控制台\n\n可在手机浏览器或任意设备直接点击操作：\n\n👉 **[点击进入控制台]({url})**\n\n`{url}`"
        self.client.send_markdown(content)
        return content

    def _handle_timer_cmd(self, cmd: str, from_user: str) -> str:
        """解析 /timer 命令."""
        parts = cmd.split()
        if len(parts) < 2:
            reply = "⚠️ 请指定时长。格式示例：`/timer 30m` (免打扰30分钟) 或 `/timer on 1h` (1小时后自动开启)"
            self.client.send_markdown(reply, to_user=from_user)
            return reply

        mode = "quiet"
        duration_str = parts[1]
        if len(parts) >= 3:
            mode = parts[1].lower()
            duration_str = parts[2]

        secs = self.parse_duration(duration_str)
        if not secs or secs <= 0:
            reply = f"⚠️ 无法识别的时长: `{duration_str}`，有效单位：s(秒), m(分), h(小时), d(天)。示例: `/timer 30m`"
            self.client.send_markdown(reply, to_user=from_user)
            return reply

        if mode in ("quiet", "mute", "off", "免打扰"):
            self.is_enabled = True
            end_time = self.set_quiet_timer(secs)
            timestr = time.strftime("%H:%M:%S", time.localtime(end_time))
            reply = f"⏸️ **已开启免打扰** ({duration_str})。\n在此期间将暂停推送通知，预计将于 **{timestr}** 自动恢复通知。"
        else:
            self.is_enabled = True
            reply = f"⏱️ **定时设置成功**: 已设为 {duration_str} 后开启模式。"

        self.client.send_markdown(reply, to_user=from_user)
        return reply

    def _handle_option_selection(self, input_text: str, from_user: str) -> str:
        """将用户回复解析为选项回答."""
        if not self.pending_question:
            reply = f"ℹ️ 收到输入: `{input_text}`，但当前没有等待确认的选项。\n发送 `/status` 查看最新状态，或 `/help` 查看指令。"
            self.client.send_markdown(reply, to_user=from_user)
            return reply

        q = self.pending_question
        options = q.get("options", [])
        matched_option = None

        # 尝试匹配数字序号 (如 "1", "2")
        if input_text.isdigit():
            idx = int(input_text)
            for opt in options:
                if opt.get("index") == idx:
                    matched_option = opt
                    break

        # 尝试文本模糊匹配
        if not matched_option:
            norm_input = input_text.strip().lower()
            for opt in options:
                label = str(opt.get("label", "")).strip().lower()
                if norm_input == label or norm_input in label:
                    matched_option = opt
                    break

        if matched_option:
            choice_label = matched_option.get("label")
            choice_idx = matched_option.get("index")
            self.pending_answer = {
                "question_id": q.get("id"),
                "choice_index": choice_idx,
                "choice_label": choice_label,
                "custom_input": None,
                "answered_at": time.time(),
                "from_user": from_user
            }
            # 清除待选状态
            self.pending_question = None

            reply = f"✅ **已确认选择**: 【选项 {choice_idx}: {choice_label}】\n已回传给 omp 驱动后续流程！"
            logger.info(f"[Service] 成功选定选项: {choice_idx}. {choice_label}")
            self.client.send_dual_notification(
                title="✅ 选项确认已提交",
                summary=f"已选定: 选项 {choice_idx}",
                details=f"<b>选项</b>: {choice_label}<br/>已回传给 omp 驱动后续流程！",
                markdown_content=reply,
                url=self.session_info.get("collab_url") or "https://work.weixin.qq.com",
                btntxt="查看控制台",
                to_user=from_user
            )
            return reply
        else:
            # 允许用户作为自定义输入提交
            self.pending_answer = {
                "question_id": q.get("id"),
                "choice_index": None,
                "choice_label": None,
                "custom_input": input_text,
                "answered_at": time.time(),
                "from_user": from_user
            }
            self.pending_question = None

            reply = f"✍️ **已将您的自定义输入回传给 omp**:\n`{input_text}`\n正在继续执行..."
            logger.info(f"[Service] 提交自定义输入: {input_text}")
            self.client.send_dual_notification(
                title="✍️ 自定义输入已提交",
                summary="自定义输入已回传",
                details=f"<b>内容</b>: {input_text[:100]}<br/>已回传给 omp 驱动后续流程！",
                markdown_content=reply,
                url=self.session_info.get("collab_url") or "https://work.weixin.qq.com",
                btntxt="查看控制台",
                to_user=from_user
            )
            return reply

    # -------------------------------------------------------------------------
    # 外部事件接入 (来自本地 omp 扩展或监控器)
    # -------------------------------------------------------------------------
    def on_ask_event(self, data: Dict[str, Any]) -> bool:
        """处理 omp 弹出选项 (ask) 事件."""
        if not self.is_notifications_active():
            logger.info("[Service] 通知当前处于关闭或免打扰中，跳过推送选项弹窗")
            return False

        q_id = str(data.get("id", f"q_{int(time.time())}"))
        session_name = data.get("session_name") or self.session_info.get("session_name", "omp 会话")
        questions_raw = data.get("questions") or []
        collab_url = data.get("collab_url") or self.session_info.get("collab_url", "")

        if not questions_raw:
            return False

        first_q = questions_raw[0]
        q_title = first_q.get("question", "请确认操作选项")
        raw_options = first_q.get("options", [])
        recommended = first_q.get("recommended")

        formatted_options = []
        for i, opt in enumerate(raw_options, 1):
            if isinstance(opt, str):
                formatted_options.append({"index": i, "label": opt, "description": "", "recommended": (i - 1 == recommended)})
            elif isinstance(opt, dict):
                formatted_options.append({
                    "index": i,
                    "label": opt.get("label", ""),
                    "description": opt.get("description", ""),
                    "recommended": (i - 1 == recommended)
                })

        # 保存为当前等待确认的问题
        self.pending_question = {
            "id": q_id,
            "session_name": session_name,
            "question": q_title,
            "options": formatted_options,
            "collab_url": collab_url,
            "created_at": time.time()
        }
        self.session_info["input_required"] = True

        # 排版并推送微信卡片
        host_name = data.get("host_name", "")
        platform_name = data.get("platform", "")
        title_prefix = f"### 🔔 [{host_name}] omp 等待确认选项" if host_name else "### 🔔 omp 等待您的确认选项"

        lines = [
            title_prefix,
            f"• **来源主机**: `{host_name}` ({platform_name})" if host_name else "",
            f"• **会话**: {session_name}",
            f"• **问题**: **{q_title}**",
            "\n**可选方案**:"
        ]
        for opt in formatted_options:
            rec = " ⭐️ *(Recommended)*" if opt.get("recommended") else ""
            desc = f" - _{opt['description']}_" if opt.get("description") else ""
            lines.append(f"  **{opt['index']}. {opt['label']}**{rec}{desc}")

        lines.append("\n━━━━━━━━━━━━━━━━━━")
        lines.append("👉 **方式一 (最便捷)**: 直接在微信中回复数字序号 (如 `1`)")
        if collab_url:
            lines.append(f"👉 **方式二**: **[📱 打开手机端 Collab 控制台实时操作]({collab_url})**")

        content = "\n".join(lines)
        # 组装个人微信端卡片 (textcard) 格式
        card_title = f"🔔 [{host_name}] omp 待确认选项" if host_name else "🔔 omp 待确认选项"
        card_summary = f"会话: {session_name} | {q_title}"
        card_details_lines = [f"<b>问题</b>: {q_title}"]
        for opt in formatted_options:
            rec_mark = " (Recommended)" if opt.get("recommended") else ""
            card_details_lines.append(f"{opt['index']}. {opt['label']}{rec_mark}")
        card_details_lines.append("<div class=\"highlight\">👉 微信回复数字选择，或点击卡片打开控制台</div>")
        card_details = "<br/>".join(card_details_lines)

        return self.client.send_dual_notification(
            title=card_title,
            summary=card_summary,
            details=card_details,
            markdown_content=content,
            url=collab_url or "https://work.weixin.qq.com",
            btntxt="打开控制台" if collab_url else "查看详情"
        )

    def on_error_event(self, data: Dict[str, Any]) -> bool:
        """处理 omp 异常中断或停止事件 (上游 API 报错、网络不可达、异常挂起等)."""
        if not self.is_notifications_active():
            return False

        alert_type = data.get("error_type", "error")
        alert_key = f"alert_{alert_type}_{data.get('session_id', '')}"
        now = time.time()
        cooldown = self.config.guard.alert_cooldown_seconds

        if now - self.alert_cooldowns.get(alert_key, 0) < cooldown:
            logger.debug(f"[Service] 告警 [{alert_key}] 处于冷却中，跳过刷屏推送")
            return False

        self.alert_cooldowns[alert_key] = now

        session_name = data.get("session_name") or self.session_info.get("session_name", "omp 会话")
        error_msg = data.get("error", "上游 API 不可访问或连接异常中断")
        detail = data.get("detail", "")
        collab_url = data.get("collab_url") or self.session_info.get("collab_url", "")

        host_name = data.get("host_name", "")
        platform_name = data.get("platform", "")
        title_prefix = f"### ⚠️ [{host_name}] omp 任务异常停止提醒" if host_name else "### ⚠️ omp 任务异常停止提醒"

        lines = [
            title_prefix,
            f"• **来源主机**: `{host_name}` ({platform_name})" if host_name else "",
            f"• **会话**: {session_name}",
            f"• **原因**: <font color=\"warning\">{error_msg}</font>",
        ]
        if detail:
            lines.append(f"**详情**: `{detail[:200]}`")

        if collab_url:
            lines.append(f"\n👉 **[📱 点击打开 Collab 手机控制台排查]({collab_url})**")

        content = "\n".join(lines)
        card_title = f"⚠️ [{host_name}] omp 任务异常停止提醒" if host_name else "⚠️ omp 任务异常停止提醒"
        card_summary = f"会话: {session_name} | 异常报警"
        card_details = f"<b>原因</b>: <font color=\"red\">{error_msg}</font><br/>"
        if detail:
            card_details += f"<b>详情</b>: {detail[:150]}<br/>"
        card_details += "<div class=\"highlight\">👉 点击下方卡片按钮打开控制台排查</div>"

        return self.client.send_dual_notification(
            title=card_title,
            summary=card_summary,
            details=card_details,
            markdown_content=content,
            url=collab_url or "https://work.weixin.qq.com",
            btntxt="立即排查" if collab_url else "查看详情"
        )

    def update_session_status(self, data: Dict[str, Any]) -> None:
        """更新本地会话状态缓存."""
        for k in ("session_name", "cwd", "model", "busy", "input_required", "collab_url"):
            if k in data and data[k] is not None:
                self.session_info[k] = data[k]
        self.session_info["updated_at"] = time.time()

    def pop_pending_answer(self) -> Optional[Dict[str, Any]]:
        """获取并清除已就绪的答复 (供本地插件或客户端轮询拉取)."""
        ans = self.pending_answer
        self.pending_answer = None
        return ans

    def send_custom_notification(
        self,
        title: str,
        content: str,
        summary: str = "",
        details: str = "",
        url: str = "",
        btntxt: str = "查看详情",
        level: str = "info",
        host: str = "",
        to_user: str = ""
    ) -> bool:
        """
        通用通知推送接口 (供任意外部工具、脚本、Cron 作业如 GPU 监控调用).
        自动双通道推送: 企微 Markdown 富文本 + 个人微信 TextCard 原生卡片
        """
        if not self.is_notifications_active():
            logger.info("[Service] 守护服务处于暂停或免打扰中，跳过自定义通知推送")
            return False

        level_icons = {
            "info": "📢",
            "success": "🚀",
            "warning": "⚠️",
            "error": "🚨"
        }
        icon = level_icons.get(level.lower(), "📢")

        host_tag = f"[{host}] " if host else ""
        formatted_title = f"{icon} {host_tag}{title}"

        # 1. 组装 Markdown 富文本 (用于企业微信客户端)
        md_lines = [f"### {formatted_title}"]
        if host:
            md_lines.append(f"• **来源主机**: `{host}`")
        md_lines.append(f"• **推送时间**: `{time.strftime('%Y-%m-%d %H:%M:%S')}`\n")
        md_lines.append(content)
        if url:
            md_lines.append(f"\n👉 **[🔗 {btntxt}]({url})**")
        full_markdown = "\n".join(md_lines)

        # 2. 组装个人微信端 TextCard 卡片
        card_summary = summary or (f"{host}: {title}" if host else title)
        if not details:
            clean_content = content.replace("\n", "<br/>")
            details = f"<div class=\"normal\">{clean_content[:300]}</div>"
            if url:
                details += "<br/><div class=\"highlight\">👉 点击卡片快速访问</div>"

        logger.info(f"[Service] 执行通用通知推送: {formatted_title}")
        return self.client.send_dual_notification(
            title=formatted_title[:120],
            summary=card_summary[:100],
            details=details[:500],
            markdown_content=full_markdown,
            url=url or "https://work.weixin.qq.com",
            btntxt=btntxt[:8],
            to_user=to_user
        )
