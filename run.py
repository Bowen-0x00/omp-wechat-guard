"""omp_wechat_guard 启动与调试入口."""

import sys
import argparse
from loguru import logger

from guard.config import load_config
from guard.wechat_client import WeComClient
from guard.service import GuardService
from guard.server import run_server
from guard.monitor import LocalOmpMonitor


def main():
    parser = argparse.ArgumentParser(description="omp 企业微信推送与远程控制助手")
    parser.add_argument("--config", "-c", help="配置文件路径", default=None)
    parser.add_argument("--mode", choices=["server", "monitor", "test-push", "status"], default="server",
                        help="运行模式 (server: 服务端模式, monitor: 本地监控模式, test-push: 测试推送, status: 查看状态)")
    parser.add_argument("--port", "-p", type=int, help="覆盖端口", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.port:
        cfg.server.port = args.port

    logger.info("=" * 60)
    logger.info("🚀 omp 企业微信推送与远程助手启动中")
    logger.info(f"模式: {args.mode} | AgentId: {cfg.wechat.agent_id} | 端口: {cfg.server.port}")
    logger.info("=" * 60)

    if args.mode == "test-push":
        client = WeComClient(cfg.wechat)
        logger.info("发送企业微信连通性测试消息...")
        test_content = """### 🔔 omp 企业微信远程助手连通测试
• **状态**: 成功连接企业微信官方 API
• **功能**: 
  1. 待选选项卡片实时推送
  2. 任务异常中断与上游报错提醒
  3. 手机端一键 Collab 网页控制台
  4. 对话框指令控制 (`/status`, `/help`, `/on`, `/off`, `/timer`)"""
        ok = client.send_markdown(test_content)
        if ok:
            logger.success("测试消息已发送！请在微信端查收。")
        else:
            logger.error("测试消息发送失败，请核对配置与『企业可信IP』白名单。")
        return

    if args.mode == "status":
        hosts = LocalOmpMonitor.get_active_collab_hosts()
        print(f"当前本地活跃的 Collab 会话数: {len(hosts)}")
        for h in hosts:
            print(f"- 会话: {h.get('sessionName')}, 繁忙: {h.get('busy')}, 等待输入: {h.get('inputRequired')}")
            print(f"  Collab URL: {h.get('url')}")
        return

    if args.mode == "monitor":
        monitor = LocalOmpMonitor(cfg)
        monitor.poll_loop(interval_seconds=3.0)
        return

    # 默认 server 模式
    service = GuardService(cfg)
    run_server(cfg, service)


if __name__ == "__main__":
    main()
