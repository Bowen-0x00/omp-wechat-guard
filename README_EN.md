# OMP WeChat Guard

[English](./README_EN.md) | [简体中文](./README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: >=3.10](https://img.shields.io/badge/Python->=3.10-blue.svg)](https://www.python.org/)
[![OMP: Extension](https://img.shields.io/badge/OMP-Extension-purple.svg)](https://github.com/Bowen-0x00/omp-wechat-guard)
[![Platform: Linux | Windows | macOS](https://img.shields.io/badge/Platform-Linux%20|%20Windows%20|%20macOS-green.svg)](https://github.com/Bowen-0x00/omp-wechat-guard)

**OMP WeChat Guard** is a cross-machine, multi-platform remote notification and interactive control system built specifically for `omp` (AI Coding Agent Harness) via WeChat Work (WeCom) and Personal WeChat.

Whether your `omp` runs on a **local Windows workstation**, a **remote Redhat/Linux server**, or a **macOS laptop**, simply install the lightweight extension plugin. Whenever the Agent prompts for user confirmation (`ask` options) or stops unexpectedly due to upstream API errors or network drops, it immediately **pushes notifications across both Enterprise WeChat and Personal WeChat**. You can reply with numbers directly in the chat dialogue to choose an option, or tap the card link to open the mobile **Collab Web Console** for live stream viewing and control!

---

## ✨ Key Features

- 🔔 **Dual-Channel Notification (WeChat Work + Personal WeChat Coverage)**:
  - **WeChat Work Client**: Delivers formatted Markdown with code blocks, bold titles, and clean lists.
  - **Personal WeChat (WeChat Plugin / Workstation)**: Delivers native TextCard cards simultaneously, completely solving the issue where Markdown appears blank or unformatted in personal WeChat!
- 💬 **Direct Interactive Reply in Chat**:
  - Receive an option card and reply directly with the number (e.g. `1`, `2`) or freeform text to immediately inject your decision back into omp.
- 📱 **One-Click Collab Mobile Web Console**:
  - Notification cards embed end-to-end encrypted Collab deep links. Tap to open the mobile browser console and interact live on your phone.
- ⚠️ **Unexpected Stop & Upstream API Error Alerts**:
  - Detects upstream LLM API errors (503 Service Unavailable, 429 Rate Limit, timeouts) and connection drops in real-time, with built-in 60s cooldown flood protection.
- 🌐 **Automatic Multi-Host Identification (Host Tag)**:
  - Automatically identifies where the session originates (e.g. `[Redhat Server]`, `[Local Windows Workstation]`, `[MacBook]`), clearly displaying the hostname and working directory.
- 🎛️ **Full Remote Control Commands**:
  - Supports `/status` query, `/help` guide, `/collab` link retrieval, `/on` and `/off` toggles, `/timer 30m` quiet timer, and more.

---

## 🏛️ System Architecture

```
                ┌────────────────────────────────────────────────────────┐
                │             User Mobile WeChat / WeCom App             │
                │     (Receive dual cards / Reply numbers or commands)   │
                └────────────────────────────────────────────────────────┘
                                ▲                        │
    Dual push (Markdown + Card) │                        │ Reply: 1, 2, /status, etc.
                                │                        ▼
                ┌────────────────────────────────────────────────────────┐
                │             Cloud Relay & Gateway (Aliyun)             │
                │        http://your-server-domain.com/wechat (Port 80 -> 8088)     │
                │   Decrypts & dispatches to omp_wechat_guard (8089)     │
                └────────────────────────────────────────────────────────┘
                                ▲                        │
           Events (ask / error) │                        │ Forward: POST /command
                                │                        ▼
┌────────────────────────────────────────────────────────────────────────┐
│  omp_wechat_guard Service (Python Backend :8089)                       │
│                                                                        │
│  1. WeComClient: Manages access token, dual-dispatches Markdown & Card │
│  2. GuardService: Command routing, pending question tracking, cooldown │
│  3. HTTP Server: Receives WeChat commands & cross-node events (/event) │
└────────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ Cross-host HTTP report (ask / error / status)
┌────────────────────────────────────────────────────────────────────────┐
│  Worker Nodes (Cross-Platform Execution Environments)                  │
│                                                                        │
│  • Node A: Remote Linux Server (Redhat / Ubuntu / CentOS)              │
│  • Node B: Local Windows Workstation (PowerShell / WSL / Zed / VSCode) │
│  • Node C: macOS Laptop                                                │
│                                                                        │
│  • omp Native Extension (~/.omp/agent/extensions/omp-wechat-guard.ts): │
│    - Intercepts tool_call: Extracts ask options & resolves Collab link │
│    - Observes agent_end: Catches stopReason: "error" & reports alert   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 WeChat Command Reference

| Command | Description | Example |
| :--- | :--- | :--- |
| **/help** | Display command reference and instructions | `/help` or `help` |
| **/status** | View active session, current status, pending options, and Collab link | `/status` |
| **/collab** | Re-fetch and push the active mobile Collab Web Console link | `/collab` |
| **/on** | Enable WeChat push notifications | `/on` |
| **/off** | Pause WeChat push notifications | `/off` |
| **/timer &lt;duration&gt;** | Set quiet countdown or scheduled enable | `/timer 30m` (quiet for 30m)<br/>`/timer 1h` (quiet for 1 hour) |
| **Number (e.g. 1, 2)** | When an option card is active, reply with the index to confirm | `1` or `2` |
| **Text content** | Submit custom text response back into omp | `Proceed with --skip-tests` |


---

## 🌐 Generic WeChat Notification API

Beyond omp's own interactive interception, this project exposes a **generic HTTP notification endpoint**. Any external tool, shell script, Cron job, or monitoring daemon can send alerts with **dual-channel delivery (WeChat Work Markdown + Personal WeChat TextCard)**!

### Endpoint Details
* **URL**: `POST http://your-server-domain.com/api/guard/notify` (or internal `http://127.0.0.1:8089/api/notify`)
* **Format**: `application/json`
* **Authentication**: Pass header `X-API-Secret: <token>`, or include `"secret": "<token>"` in the JSON body

### Parameter Reference
| Field | Type | Required | Description | Example |
| :--- | :--- | :--- | :--- | :--- |
| **title** | string | Yes | Notification title | `"H100 GPU is now idle"` |
| **content** | string | Yes | Body content in Markdown format (supports code blocks, bold, lists) | `"GPU utilization dropped below 5%..."` |
| **level** | string | No | Alert level: `"info"`, `"success"`, `"warning"`, `"error"` (default `"info"`, auto-prepends emoji) | `"success"` |
| **host** | string | No | Host or device label | `"Redhat -> H100"` |
| **summary** | string | No | Short subtitle for personal WeChat card (auto-generated if omitted) | `"8x H100 GPUs ready for jobs"` |
| **url** | string | No | Target URL for the action button | `"http://grafana.internal/gpu"` |
| **btntxt** | string | No | Button label (max 8 characters, default `"View Details"`) | `"Submit Job"` |
| **to_user** | string | No | Target WeChat user ID (default `@all`) | `"@all"` |

### cURL / Bash Example
```bash
curl -X POST "http://your-server-domain.com/api/guard/notify" \
  -H "Content-Type: application/json" \
  -H "X-API-Secret: YOUR_CUSTOM_API_SECRET" \
  -d '{
    "title": "H100 GPUs are Idle",
    "level": "success",
    "host": "Redhat -> H100",
    "content": "GPU 0~7 utilization has dropped to 0%, memory released. Ready for training jobs!\n\n- **GPU 0**: 0% (1.2GB/80GB)\n- **GPU 1**: 0% (1.2GB/80GB)",
    "summary": "H100 node 8x GPUs ready",
    "url": "https://work.weixin.qq.com"
  }'
```

### Python Example
```python
import requests

resp = requests.post(
    "http://your-server-domain.com/api/guard/notify",
    headers={
        "Content-Type": "application/json",
        "X-API-Secret": "YOUR_CUSTOM_API_SECRET"
    },
    json={
        "title": "Training Job Finished",
        "level": "success",
        "host": "Redhat",
        "content": "### 🎉 Qwen-7B Fine-Tuning Complete\n• **Duration**: 3h 24m\n• **Final Loss**: `0.842`\n• **Checkpoint**: `/data/models/ckpt-final`"
    }
)
print(resp.json())  # {"code": 0, "msg": "Notification sent", "notified": True}
```

---

## 💡 Practical Example: Monitoring H100 GPU Idle State from Redhat

To monitor `ssh H100` GPU availability and notify via WeChat when compute becomes idle, an out-of-the-box script is provided in `examples/gpu_monitor.py`.

### Quick Start
On your Redhat server:
```bash
# 1. Go to examples directory
cd /path/to/omp-wechat-guard/examples

# 2. Test WeChat connectivity card
python gpu_monitor.py --target "ssh H100" --test-notify

# 3. Launch background monitoring (probes GPU utilization via SSH every 60s)
nohup python gpu_monitor.py \
  --target "ssh H100" \
  --interval 60 \
  --threshold 5.0 \
  --api-url "http://your-server-domain.com/api/guard/notify" \
  > gpu_monitor.log 2>&1 &
```
*When GPU utilization on `H100` drops below 5%, your WeChat will immediately receive a dual-channel card alert!*
---

## 📦 Installation & Setup

### 1. Cloud Guard Service Deployment (Linux / VPS)

The guard service receives WeChat callbacks and makes authorized calls to the WeCom API:

```bash
# Clone the repository
git clone https://github.com/Bowen-0x00/omp-wechat-guard.git
cd omp-wechat-guard

# Install Python dependencies
pip install -r requirements.txt

# Create your actual config (NOTE: Never commit config.yaml with real secrets)
cp config.example.yaml config.yaml
vim config.yaml  # Fill in your WeCom app credentials
```

Start the service:
```bash
# Run directly
python run.py --mode server --port 8089

# Or run as a systemd service (see deploy/omp_wechat_guard.service)
sudo cp deploy/omp_wechat_guard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now omp_wechat_guard
```

### 2. Install omp Extension on Worker Nodes

Copy the extension file into `~/.omp/agent/extensions/` on any machine where you run `omp`:

#### Option A: Linux / macOS Server (e.g. Redhat, Ubuntu)
```bash
# 1. Copy the extension file
mkdir -p ~/.omp/agent/extensions
cp extension/omp-wechat-guard.ts ~/.omp/agent/extensions/

# 2. (Optional) Customize the host label in ~/.omp/agent/wechat-guard.json:
cat << 'EOF' > ~/.omp/agent/wechat-guard.json
{
  "tag": "Redhat Server",
  "url": "http://your-server-domain.com/api/guard",
  "secret": "your_api_secret_token"
}
EOF
```

#### Option B: Windows Local Workstation
Run the batch script:
```text
extension\install_extension.bat
```
Or manually copy `extension/omp-wechat-guard.ts` into `%USERPROFILE%\.omp\agent\extensions\`.

---

## ⚙️ WeChat Work Configuration

1. **Create an App**: In WeChat Work admin ([work.weixin.qq.com](https://work.weixin.qq.com)), navigate to **App Management** and create a self-built app (e.g. `omp Remote Guard`).
2. **Configure Trusted IP (Crucial ⚠️)**:
   Under the app's **Developer Interface** -> **Enterprise Trusted IP**, enter the public outbound IP of your guard server.
3. **Configure Callback Server**:
   Under **Receive Messages** -> **Set API Reception**, enter your gateway URL (`http://your-domain.com/wechat`), Token, and EncodingAESKey.

---

## 🧪 Testing & Verification

```bash
# 1. Run local test suite
python -m pytest tests -v

# 2. Send test card to verify WeChat connectivity
python run.py --mode test-push

# 3. Inside an omp session, type /wechat test to verify extension reporting
/wechat test
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
