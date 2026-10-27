/**
 * omp 企业微信守护扩展 (omp-wechat-guard)
 * 
 * 功能:
 * 1. 拦截 ask 工具调用：提取问题与选项，附带 Collab 网页控制台链接，实时推送到微信。
 * 2. 监听任务异常与中断：当上游 API 报错或网络不可达导致 agent 异常停止时，发送预警卡片。
 * 3. 多端多主机统一接入：自动识别所在主机标签 (如 Redhat、Windows、Mac) 并上报。
 * 4. 注册 /wechat 斜杠命令，随时检查连接或测试推送。
 */

import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import * as cp from "node:child_process";

interface GuardConfig {
  tag?: string;
  url?: string;
  secret?: string;
}

function loadGuardConfig(): { tag: string; url: string; secret: string } {
  const home = os.homedir();
  const cfgPath = path.join(home, ".omp", "agent", "wechat-guard.json");
  let cfg: GuardConfig = {};
  if (fs.existsSync(cfgPath)) {
    try {
      cfg = JSON.parse(fs.readFileSync(cfgPath, "utf-8")) as GuardConfig;
    } catch {
      // 忽略配置文件读取异常
    }
  }

  // 友好主机名 (如果在 Redhat 等 Linux 上且主机名为 localhost，则智能使用常用标识)
  let defaultTag = os.hostname();
  if (defaultTag === "localhost" || defaultTag === "localhost.localdomain") {
    defaultTag = os.platform() === "linux" ? "Redhat-Server" : defaultTag;
  }

  return {
    tag: process.env.OMP_GUARD_TAG || cfg.tag || defaultTag,
    url: process.env.OMP_GUARD_URL || cfg.url || "http://your-server-domain.com/api/guard",
    secret: process.env.OMP_GUARD_SECRET || cfg.secret || "YOUR_CUSTOM_API_SECRET"
  };
}

const config = loadGuardConfig();

function getActiveCollabUrl(): string {
  // 1. 优先尝试使用 omp collab 命令行快速查询 (跨平台可靠)
  try {
    const listRaw = cp.execSync("omp collab list --json", {
      timeout: 1500,
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "ignore"]
    });
    const data = JSON.parse(listRaw);
    if (data && Array.isArray(data.hosts) && data.hosts.length > 0) {
      const instanceId = data.hosts[0].instanceId;
      if (instanceId && typeof instanceId === "string") {
        const link = cp.execSync(`omp collab link ${instanceId}`, {
          timeout: 1500,
          encoding: "utf-8",
          stdio: ["ignore", "pipe", "ignore"]
        }).trim();
        if (link.startsWith("http")) return link;
      }
    }
  } catch {
    // 命令行查询失败，回退到文件检查
  }

  // 2. 回退到检查 ~/.omp/run/collab-hosts 目录缓存
  try {
    const home = os.homedir();
    const hostsDir = path.join(home, ".omp", "run", "collab-hosts");
    if (!fs.existsSync(hostsDir)) return "";

    const files = fs.readdirSync(hostsDir);
    for (const f of files) {
      if (!f.endsWith(".json")) continue;
      const raw = fs.readFileSync(path.join(hostsDir, f), "utf-8");
      const meta = JSON.parse(raw);
      if (meta && typeof meta === "object" && "url" in meta && typeof meta.url === "string") {
        return meta.url;
      }
    }
  } catch {
    // 忽略异常
  }
  return "";
}

async function postEvent(type: string, data: Record<string, unknown>): Promise<boolean> {
  const fullData = {
    ...data,
    host_name: config.tag,
    platform: `${os.platform()} (${os.arch()})`
  };

  const urls = [
    `${config.url}/event`,
    "http://127.0.0.1:8089/api/event"
  ];

  for (const url of urls) {
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Secret": config.secret
        },
        body: JSON.stringify({ type, data: fullData }),
        signal: AbortSignal.timeout(3000)
      });
      if (resp.ok) {
        return true;
      }
    } catch {
      // 尝试下一个地址
    }
  }
  return false;
}

export default function (pi: ExtensionAPI) {
  // 1. 会话启动就绪
  pi.on("session_start", async (_event, ctx) => {
    try {
      const collabUrl = getActiveCollabUrl();
      await postEvent("status", {
        session_name: ctx.cwd ? path.basename(ctx.cwd) : "omp 会话",
        cwd: ctx.cwd,
        collab_url: collabUrl,
        busy: false,
        input_required: false
      });
    } catch {
      // 容错
    }
  });

  // 2. 核心拦截：当 Agent 准备弹出 ask 选项时
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName === "ask") {
      let questions: unknown[] = [];
      if (event.input && typeof event.input === "object" && "questions" in event.input) {
        const candidate = event.input.questions;
        if (Array.isArray(candidate)) {
          questions = candidate;
        }
      }

      const collabUrl = getActiveCollabUrl();
      const sessionName = ctx.cwd ? path.basename(ctx.cwd) : "omp 会话";

      ctx.ui.notify(`🔔 [${config.tag}] 正在推送选项至企业微信...`, "info");

      const toolCallId = "toolCallId" in event && typeof event.toolCallId === "string" 
        ? event.toolCallId 
        : String(Date.now());

      postEvent("ask", {
        id: toolCallId,
        session_name: sessionName,
        cwd: ctx.cwd,
        questions,
        collab_url: collabUrl
      }).then(ok => {
        if (ok) {
          ctx.ui.notify(`📱 [${config.tag}] 选项已送达企业微信！您可在微信回复序号，或点击卡片链接操作。`, "info");
        }
      });
    }
  });

  // 3. 核心监控：当 Agent 结束或异常中断时
  pi.on("agent_end", async (event, ctx) => {
    try {
      if (!event || typeof event !== "object" || !("lastAssistantMessage" in event)) return;
      const lastMsg = event.lastAssistantMessage;
      if (!lastMsg || typeof lastMsg !== "object") return;

      const stopReason = "stopReason" in lastMsg && typeof lastMsg.stopReason === "string" ? lastMsg.stopReason : undefined;
      const collabUrl = getActiveCollabUrl();
      const sessionName = ctx.cwd ? path.basename(ctx.cwd) : "omp 会话";

      // 判断是否因异常、网络错误或上游 API 报错停止
      if (stopReason === "error") {
        let errDetail = "";
        if ("content" in lastMsg && Array.isArray(lastMsg.content)) {
          for (const c of lastMsg.content) {
            if (c && typeof c === "object" && "type" in c && c.type === "text" && "text" in c && typeof c.text === "string") {
              errDetail += c.text + "\n";
            }
          }
        }

        ctx.ui.notify(`⚠️ [${config.tag}] 检测到任务异常停止，正在发送微信告警...`, "warning");
        await postEvent("error", {
          session_name: sessionName,
          cwd: ctx.cwd,
          error: "上游大模型 API 报错或网络连接异常中断",
          detail: errDetail.slice(0, 300),
          collab_url: collabUrl
        });
      }
    } catch {
      // 保护 agent 循环不被扩展异常阻断
    }
  });

  // 4. 注册 /wechat 斜杠命令
  pi.registerCommand("wechat", {
    description: "检查企业微信远程助手状态或发送测试卡片",
    handler: async (args, ctx) => {
      const subcmd = args.trim().toLowerCase();
      if (subcmd === "test") {
        ctx.ui.notify(`正在从 [${config.tag}] 发送测试卡片至微信...`, "info");
        const ok = await postEvent("ask", {
          id: "test_" + Date.now(),
          session_name: ctx.cwd ? path.basename(ctx.cwd) : "测试会话",
          cwd: ctx.cwd,
          questions: [{
            question: `【${config.tag} 测试】是否确认连接企业微信远程助手？`,
            options: [
              { label: "连接正常 (Recommended)", description: `已成功连通 ${config.tag}` },
              { label: "稍后配置", description: "跳过" }
            ],
            recommended: 0
          }],
          collab_url: getActiveCollabUrl()
        });
        if (ok) {
          ctx.ui.notify(`✅ [${config.tag}] 企业微信测试卡片发送成功！请检查手机微信。`, "info");
        } else {
          ctx.ui.notify(`❌ 连接网关 ${config.url} 失败，请检查网络或网关服务。`, "error");
        }
      } else {
        ctx.ui.notify("用法: /wechat test (发送测试卡片)", "info");
      }
    }
  });
}
