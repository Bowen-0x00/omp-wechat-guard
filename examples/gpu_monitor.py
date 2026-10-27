#!/usr/bin/env python3
"""
GPU 资源空闲监听工具 (支持远程 SSH 探测与企业微信双通道通知).

典型使用场景:
在 Redhat 服务器上运行此脚本，定时探测远程 H100 集群显卡是否空闲；
一旦检测到利用率或显存占用释放，立即通过 omp_wechat_guard 接口推送微信通知！

用法示例:
python gpu_monitor.py --target "ssh H100" --interval 30 --threshold 5
"""

import sys
import time
import argparse
import subprocess
from typing import List, Dict, Any, Optional
import requests


DEFAULT_API_URL = "http://your-server-domain.com/api/guard/notify"
DEFAULT_API_SECRET = "YOUR_CUSTOM_API_SECRET"


def query_gpus_via_ssh(ssh_target: str) -> Optional[List[Dict[str, Any]]]:
    """通过 SSH 执行 nvidia-smi 查询各 GPU 利用率与显存."""
    # 格式: index, name, utilization.gpu, memory.used, memory.total
    cmd = f'{ssh_target} "nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits"'
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            print(f"[-] 查询失败 (Exit {proc.returncode}): {proc.stderr.strip()}")
            return None

        lines = [line.strip() for line in proc.stdout.strip().split("\n") if line.strip()]
        gpus = []
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                gpus.append({
                    "index": parts[0],
                    "name": parts[1],
                    "util_gpu": float(parts[2]),
                    "mem_used": float(parts[3]),
                    "mem_total": float(parts[4])
                })
        return gpus
    except Exception as e:
        print(f"[-] 执行 SSH 探测异常: {e}")
        return None


def send_wechat_notification(
    title: str,
    content: str,
    summary: str = "",
    level: str = "success",
    host: str = "H100",
    api_url: str = DEFAULT_API_URL,
    api_secret: str = DEFAULT_API_SECRET
) -> bool:
    """调用 omp_wechat_guard 通用通知接口."""
    payload = {
        "title": title,
        "content": content,
        "summary": summary,
        "level": level,
        "host": host,
        "secret": api_secret
    }
    headers = {
        "Content-Type": "application/json",
        "X-API-Secret": api_secret
    }
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=10)
        res_data = resp.json()
        if res_data.get("code") == 0:
            print(f"[+] 微信推送成功: {title}")
            return True
        else:
            print(f"[-] 微信推送失败: {res_data}")
            return False
    except Exception as e:
        print(f"[-] 调用通知 API 异常: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="GPU 空闲自动监听与微信推送工具")
    parser.add_argument("--target", default="ssh H100", help="SSH 目标命令，默认 'ssh H100'")
    parser.add_argument("--interval", type=int, default=60, help="轮询检测间隔 (秒)，默认 60")
    parser.add_argument("--threshold", type=float, default=5.0, help="显卡利用率阈值 (%%)，低于此值判定为空闲，默认 5.0")
    parser.add_argument("--mem-threshold", type=float, default=2000.0, help="显存占用阈值 (MiB)，低于此值判定为空闲，默认 2000")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="omp_wechat_guard 通用通知接口地址")
    parser.add_argument("--api-secret", default=DEFAULT_API_SECRET, help="API 鉴权秘钥")
    parser.add_argument("--once", action="store_true", help="单次检测模式 (适合直接挂在 cron 作业中)")
    parser.add_argument("--test-notify", action="store_true", help="立即发送一条测试通知并退出")
    args = parser.parse_args()

    if args.test_notify:
        print("[*] 正在发送通用通知接口测试卡片...")
        send_wechat_notification(
            title="GPU 监听测试通知",
            content="这是一条来自 GPU 监听工具的通用通知接口联通性测试卡片！",
            summary="GPU 通用通知连通测试成功",
            level="info",
            host=args.target,
            api_url=args.api_url,
            api_secret=args.api_secret
        )
        return

    print("=" * 60)
    print(f"🚀 GPU 资源空闲监听工具已启动")
    print(f"• 探测目标: {args.target}")
    print(f"• 轮询间隔: {args.interval}s | 利用率阈值: <={args.threshold}% | 显存阈值: <={args.mem_threshold}MB")
    print(f"• 通知网关: {args.api_url}")
    print("=" * 60)

    last_idle_state = False

    while True:
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now_str}] 正在探测 {args.target} GPU 状态...")
        gpus = query_gpus_via_ssh(args.target)

        if gpus is not None:
            # 判断是否有显卡空闲
            idle_gpus = [g for g in gpus if g["util_gpu"] <= args.threshold and g["mem_used"] <= args.mem_threshold]
            total_gpus = len(gpus)
            is_idle = len(idle_gpus) > 0

            status_lines = []
            for g in gpus:
                flag = "🟢 空闲" if (g["util_gpu"] <= args.threshold and g["mem_used"] <= args.mem_threshold) else "🔴 繁忙"
                status_lines.append(
                    f"- GPU {g['index']} ({g['name']}): **{g['util_gpu']}%** 利用率 | 显存: {int(g['mem_used'])}MB / {int(g['mem_total'])}MB | {flag}"
                )

            table_text = "\n".join(status_lines)
            print(f"    检测结果: 共 {total_gpus} 张 GPU, 空闲 {len(idle_gpus)} 张")

            # 状态翻转触发通知: 从繁忙 -> 变为空闲
            if is_idle and not last_idle_state:
                print("[!] 🌟 检测到 GPU 资源已空闲！正在触发企业微信推送...")
                title = f"{args.target} 算力已空闲就绪！"
                summary = f"检测到 {len(idle_gpus)}/{total_gpus} 张 GPU 空闲，可开始作业"
                content = f"### 🚀 {args.target} 显卡资源已释放\n\n当前节点发现 **{len(idle_gpus)}** 张空闲显卡，可以立即提交训练或计算任务！\n\n**显卡详情**:\n{table_text}"

                send_wechat_notification(
                    title=title,
                    content=content,
                    summary=summary,
                    level="success",
                    host=args.target,
                    api_url=args.api_url,
                    api_secret=args.api_secret
                )

            last_idle_state = is_idle

        if args.once:
            break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
