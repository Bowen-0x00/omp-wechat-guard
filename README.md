# OMP WeChat Guard (企业微信远程守护与交互助手)

[English](./README_EN.md) | [简体中文](./README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: >=3.10](https://img.shields.io/badge/Python->=3.10-blue.svg)](https://www.python.org/)
[![OMP: Extension](https://img.shields.io/badge/OMP-Extension-purple.svg)](https://github.com/Bowen-0x00/omp-wechat-guard)
[![Platform: Linux | Windows | macOS](https://img.shields.io/badge/Platform-Linux%20|%20Windows%20|%20macOS-green.svg)](https://github.com/Bowen-0x00/omp-wechat-guard)

**OMP WeChat Guard** 是专为 `omp`（AI Coding Agent Harness）打造的跨机器、跨平台企业微信远程监控与交互控制系统。

无论你的 `omp` 运行在**本地 Windows 工作站**、**远程 Redhat/Linux 服务器**还是 **Mac 笔记本**上，只需安装轻量扩展插件，每当 Agent 弹出操作决策（`ask` 选项确认）或因上游 API 报错/网络不可达而异常停止时，都会**同时推送到企业微信客户端与个人微信**。你既可以在微信对话框中**直接回复数字序号做选择**，也可以点击卡片一键直达手机端 **Collab 网页控制台**进行实时查看与操作！

---

## ✨ 核心特性

- 🔔 **双通道通知机制（个人微信 + 企业微信全覆盖）**：
  - **企业微信客户端**：发送 Markdown 富文本，享受代码块、高亮与结构化排版。
  - **个人微信端（微信插件 / 微工作台）**：同步发送原生文本卡片（TextCard），彻底解决个人微信中 Markdown 显示空白或无法排版的问题！
- 💬 **微信对话框直接回复选择**：
  - 收到待选卡片后，直接在微信对话框中回复数字序号（如 `1`、`2`）或自定义文本，即可将决策瞬间注入 omp 推进执行。
- 📱 **Collab 手机网页控制台一键直达**：
  - 推送卡片内置当前会话的端到端加密 Collab 网页直链，点击即可在手机浏览器中实时查看流式输出并直接点选操作。
- ⚠️ **任务异常停止与上游报错预警**：
  - 实时捕获大模型上游 API 报错（503 服务不可用、429 超额、请求超时等）及网络中断故障，附带 60 秒防刷屏冷却保护。
- 🌐 **多主机节点自动标识（Multi-Host Tag）**：
  - 自动识别当前会话来源节点（如 `[Redhat 服务器]`、`[本地 Windows 工作站]`、`[MacBook]`），卡片清晰标明机器身份与工作路径。
- 🎛️ **全套微信远程指令控制**：
  - 支持 `/status` 状态查询、`/help` 帮助菜单、`/collab` 链接索取、`/on` 与 `/off` 开关、`/timer 30m` 免打扰定时等。

---

## 🏛️ 系统架构图

```
                ┌────────────────────────────────────────────────────────┐
                │                   用户手机端 微信 / 企微                │
                │        (接收双通道卡片通知 / 在对话框回复数字或指令)       │
                └────────────────────────────────────────────────────────┘
                                ▲                        │
     双通道推送 (Markdown+卡片)    │                        │ 用户回复: 1, 2, /status 等
                                │                        ▼
                ┌────────────────────────────────────────────────────────┐
                │                 云端网关与转发枢纽 (Aliyun)             │
                │        http://your-server-domain.com/wechat (端口 80 -> 8088)     │
                │       自动解密并路由至 omp_wechat_guard 后端 (8089)     │
                └────────────────────────────────────────────────────────┘
                                ▲                        │
        上报事件 (ask / error)   │                        │ 转发指令: POST /command
                                │                        ▼
┌────────────────────────────────────────────────────────────────────────┐
│  omp_wechat_guard 守护服务 (Python 后端 :8089)                          │
│                                                                        │
│  1. WeComClient: Token 自动维护，智能分发 Markdown 富文本与 TextCard 原生卡片│
│  2. GuardService: 命令分发、待选选项队列追踪、告警防刷屏冷却             │
│  3. HTTP 调度: 接收微信回调指令 (/command) 与 跨节点事件上报 (/api/event)│
└────────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ 跨主机 HTTP 上报 (ask / error / status)
┌────────────────────────────────────────────────────────────────────────┐
│  计算工作节点 (多主机跨平台运行环境)                                   │
│                                                                        │
│  • 节点 A: 远程 Linux 服务器 (如 Redhat / Ubuntu / CentOS)              │
│  • 节点 B: 本地 Windows 工作站 (PowerShell / WSL / Zed / VSCode)       │
│  • 节点 C: macOS 个人电脑                                              │
│                                                                        │
│  • omp 原生扩展插件 (~/.omp/agent/extensions/omp-wechat-guard.ts):      │
│    - 拦截 tool_call: 提取 ask 问题与选项，跨平台获取 Collab 网页控制台   │
│    - 监听 agent_end: 捕获异常停止原因与大模型报错并实时上报            │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 微信远程控制指令手册

| 指令 | 说明 | 示例 |
| :--- | :--- | :--- |
| **/help** | 查看指令列表与使用指南 | `/help` 或直接回复 `帮助` |
| **/status** | 查看当前活跃会话、运行状态、待选问题及 Collab 链接 | `/status` |
| **/collab** | 重新获取并推送当前会话的 Collab 手机端网页控制台链接 | `/collab` |
| **/on** | 开启微信通知推送 | `/on` |
| **/off** | 暂停微信通知推送 | `/off` |
| **/timer &lt;时长&gt;** | 设定免打扰倒计时或定时开启模式 | `/timer 30m`（免打扰30分钟后自动恢复）<br/>`/timer 1h`（免打扰1小时） |
| **数字序号 (如 1, 2)** | 当收到待选卡片时，直接回复数字完成确认 | `1` 或 `2` |
| **文字内容** | 自定义输入应答直接注入回传 | `确认执行并跳过测试` |


---

## 🌐 通用微信通知 API 接口

除了 omp 自身的交互拦截，本工程还对外暴露了**通用的 HTTP 通知推送接口**。任何服务器、Shell 脚本、Cron 定时任务或监控程序均可直接发起调用，享受**企业微信 Markdown + 个人微信 TextCard 原生双通道直推**！

### 接口说明
* **接口地址**：`POST http://your-server-domain.com/api/guard/notify`（或本地内网 `http://127.0.0.1:8089/api/notify`）
* **请求格式**：`application/json`
* **鉴权方式**：请求头携带 `X-API-Secret: <your_token>`，或在 JSON 体中附带 `"secret": "<your_token>"`

### 参数规范
| 字段 | 类型 | 必填 | 说明 | 示例 |
| :--- | :--- | :--- | :--- | :--- |
| **title** | string | 是 | 通知标题（建议简明扼要） | `"H100 显卡资源已释放"` |
| **content** | string | 是 | 通知正文（支持完整 Markdown 排版、代码块、列表） | `"检测到当前所有 GPU 利用率 < 5%..."` |
| **level** | string | 否 | 通知级别：`"info"`, `"success"`, `"warning"`, `"error"`（默认 `"info"`，自动附带图标） | `"success"` |
| **host** | string | 否 | 触发该通知的主机或设备标签 | `"Redhat -> H100"` |
| **summary** | string | 否 | 个人微信卡片副标题摘要（不填则自动生成） | `"8x H100 显卡空闲可提交作业"` |
| **url** | string | 否 | 卡片底部按钮跳转的目标 URL | `"http://grafana.internal/gpu"` |
| **btntxt** | string | 否 | 卡片按钮文字（最多 8 字，默认 `"查看详情"`） | `"立即提交作业"` |
| **to_user** | string | 否 | 指定推送的企业微信用户（默认 `@all`） | `"@all"` |

### 调用示例 (cURL / Bash)
```bash
curl -X POST "http://your-server-domain.com/api/guard/notify" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: YOUR_CUSTOM_API_SECRET" \
  -d '{
    "title": "H100 显卡已空闲",
    "level": "success",
    "host": "Redhat -> H100",
    "content": "GPU 0~7 利用率已归零，显存全部释放，可以启动训练作业！\n\n- **GPU 0**: 0% (1.2GB/80GB)\n- **GPU 1**: 0% (1.2GB/80GB)",
    "summary": "H100 节点 8 张显卡已空闲就绪",
    "url": "https://work.weixin.qq.com"
  }'
```

### 调用示例 (Python)
```python
import requests

resp = requests.post(
    "http://your-server-domain.com/api/guard/notify",
    headers={
        "Content-Type": "application/json",
        "X-API-Secret": "YOUR_CUSTOM_API_SECRET"
    },
    json={
        "title": "模型训练完成通知",
        "level": "success",
        "host": "Redhat",
        "content": "### 🎉 Qwen-7B 微调任务已结束\n• **总耗时**: 3h 24m\n• **最终 Loss**: `0.842`\n• **Checkpoint**: `/data/models/ckpt-final`"
    }
)
print(resp.json())  # {"code": 0, "msg": "Notification sent", "notified": True}
```

---

## 💡 实战案例：在 Redhat 上定时监听 H100 GPU 算力释放

针对“在 Redhat 服务器上定时检测 `ssh H100` GPU 是否空闲，一旦空闲立即微信通知”的常见需求，工程内置了现成的开箱即用脚本 `examples/gpu_monitor.py`。

### 运行步骤
在 Redhat 服务器上执行：
```bash
# 1. 进入 examples 目录
cd /path/to/omp-wechat-guard/examples

# 2. 测试微信连通性卡片
python gpu_monitor.py --target "ssh H100" --test-notify

# 3. 启动后台定时检测 (每 60 秒通过 SSH 检测一次显卡利用率与显存)
nohup python gpu_monitor.py \
  --target "ssh H100" \
  --interval 60 \
  --threshold 5.0 \
  --api-url "http://your-server-domain.com/api/guard/notify" \
  > gpu_monitor.log 2>&1 &
```
*当 `H100` 节点上的显卡利用率降低至 5% 以下时，您的微信将第一时间弹出醒目的双通道卡片，提醒您立即提交训练任务！*
---

## 📦 安装与部署指南

### 1. 云端守护服务部署（Linux / VPS）

守护服务负责接收微信回调指令与调用企业微信 API 发送消息：

```bash
# 克隆仓库
git clone https://github.com/Bowen-0x00/omp-wechat-guard.git
cd omp-wechat-guard

# 安装 Python 依赖
pip install -r requirements.txt

# 创建真实配置文件 (注意: 切勿将真实配置文件上传至代码仓库)
cp config.example.yaml config.yaml
vim config.yaml  # 填入您的企业微信应用凭据
```

启动服务：
```bash
# 直接运行
python run.py --mode server --port 8089

# 或使用 systemd 进行后台守护 (参考 deploy/omp_wechat_guard.service)
sudo cp deploy/omp_wechat_guard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now omp_wechat_guard
```

### 2. 在各主机安装 omp 扩展插件

无论是在本地电脑还是远程服务器上，只需将扩展插件复制到 `~/.omp/agent/extensions/` 即可即插即用：

#### 方案 A：Linux / macOS 服务器（如 Redhat、Ubuntu）
```bash
# 1. 复制扩展文件
mkdir -p ~/.omp/agent/extensions
cp extension/omp-wechat-guard.ts ~/.omp/agent/extensions/

# 2. (可选) 自定义该主机的微信显示标签，创建 ~/.omp/agent/wechat-guard.json：
cat << 'EOF' > ~/.omp/agent/wechat-guard.json
{
  "tag": "Redhat 服务器",
  "url": "http://your-server-domain.com/api/guard",
  "secret": "your_api_secret_token"
}
EOF
```

#### 方案 B：Windows 本地电脑
直接在 `extension` 目录下双击运行：
```text
extension\install_extension.bat
```
或手动将 `extension/omp-wechat-guard.ts` 复制至 `%USERPROFILE%\.omp\agent\extensions\`。

---

## ⚙️ 企业微信后台配置说明

1. **创建自建应用**：登录企业微信后台（[work.weixin.qq.com](https://work.weixin.qq.com)），在【应用管理】中创建一个自建应用（如 `omp 远程助手`）。
2. **配置企业可信 IP（非常重要 ⚠️）**：
   在应用详情页底部【开发者接口】->【企业可信IP】中，填入运行守护服务的服务器公网 IP，确保发信接口正常连通。
3. **设置接收消息服务器**：
   进入【接收消息】->【设置 API 接收】，填入网关接收 URL、Token 与 EncodingAESKey。

---

## 🧪 测试与校验

```bash
# 1. 运行本地单元测试套件
python -m pytest tests -v

# 2. 发送测试卡片验证微信端连通性
python run.py --mode test-push

# 3. 在 omp 交互会话中输入 /wechat test 验证扩展端上报
/wechat test
```

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 许可证开源。
