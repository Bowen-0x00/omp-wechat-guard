/**
 * omp 企业微信守护扩展 (omp-wechat-guard)
 * 
 * 功能:
 * 1. 拦截 ask 工具调用：提取问题与选项，附带 Collab 网页控制台链接，实时推送到微信。
 * 2. 微信远程应答自动注入：用户在微信对话框回复数字序号或内容时，自动解决待办选项，驱动 omp 继续执行！
 * 3. 监听任务异常与中断：当上游 API 报错或网络不可达导致 agent 异常停止时，发送预警卡片。
 * 4. 多端多主机统一接入：自动识别所在主机标签 (如 Redhat、Windows、Mac) 并上报。
 * 5. 注册 /wechat 斜杠命令，随时检查连接或测试推送。
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

interface PendingAnswer {
  question_id?: string;
  choice_index?: number;
  choice_label?: string;
  custom_input?: string;
}

interface AskDialogOption {
  label: string;
  description?: string;
  preview?: string;
}

interface AskDialogQuestion {
  id: string;
  question: string;
  header?: string;
  options: AskDialogOption[];
  multi?: boolean;
  recommended?: number;
}

interface AskDialogResultItem {
  id: string;
  selectedOptions: string[];
  customInput?: string;
  note?: string;
}

interface AskDialogResult {
  kind?: "submit" | "chat";
  results: AskDialogResultItem[];
}

interface WrappedUIContext {
  __wechat_wrapped?: boolean;
  select?: (
    title: string,
    options: (string | { label: string; description?: string })[],
    opts?: { signal?: AbortSignal; timeout?: number }
  ) => Promise<string | undefined>;
  askDialog?: (
    questions: AskDialogQuestion[],
    opts?: { signal?: AbortSignal; timeout?: number; acceptImages?: boolean }
  ) => Promise<AskDialogResult | undefined>;
  notify: (message: string, type?: "info" | "warning" | "error") => void;
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

  // 友好主机名
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
  // 从 ~/.omp/run/collab-hosts 快速安全读取已发布的 Collab 信息 (纯文件读取，零死锁风险)
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

async function fetchPendingAnswer(): Promise<PendingAnswer | null> {
  const urls = [
    `${config.url}/pending`,
    "http://127.0.0.1:8089/api/pending"
  ];
  for (const url of urls) {
    try {
      const resp = await fetch(url, {
        method: "GET",
        headers: {
          "X-API-Secret": config.secret
        },
        signal: AbortSignal.timeout(1500)
      });
      if (resp.ok) {
        const json = await resp.json() as { code?: number; answer?: PendingAnswer };
        if (json && json.code === 0 && json.answer) {
          return json.answer;
        }
      }
    } catch {
      // 尝试下一个
    }
  }
  return null;
}

function pollForSelect(
  options: (string | { label: string; description?: string })[],
  localAbort: AbortController,
  signal?: AbortSignal,
  notifyFn?: (msg: string, type?: "info" | "warning" | "error") => void
): Promise<string> {
  const { promise, resolve, reject } = Promise.withResolvers<string>();
  let timer: NodeJS.Timeout | number | undefined = undefined;
  let finished = false;

  const cleanup = () => {
    finished = true;
    clearInterval(timer);
  };

  if (signal) {
    signal.addEventListener("abort", () => {
      cleanup();
      reject(signal.reason);
    }, { once: true });
  }

  const check = async () => {
    if (finished) return;
    const ans = await fetchPendingAnswer();
    if (ans && !finished) {
      cleanup();
      localAbort.abort(); // 关闭本地正在等待的弹窗

      let chosen = "";
      if (ans.choice_index !== undefined && ans.choice_index !== null) {
        const idx = ans.choice_index - 1;
        if (idx >= 0 && idx < options.length) {
          const opt = options[idx];
          chosen = typeof opt === "string" ? opt : opt.label;
        }
      }
      if (!chosen && ans.choice_label) {
        chosen = ans.choice_label;
      }
      if (!chosen && ans.custom_input) {
        chosen = ans.custom_input;
      }
      if (!chosen && options.length > 0) {
        const first = options[0];
        chosen = typeof first === "string" ? first : first.label;
      }

      if (notifyFn) {
        notifyFn(`✅ 已接收微信远程选择: [${chosen}]，正在继续推进...`, "info");
      }
      resolve(chosen);
    }
  };

  timer = setInterval(check, 1000);
  return promise;
}

function pollForAskDialog(
  questions: AskDialogQuestion[],
  localAbort: AbortController,
  signal?: AbortSignal,
  notifyFn?: (msg: string, type?: "info" | "warning" | "error") => void
): Promise<AskDialogResult> {
  const { promise, resolve, reject } = Promise.withResolvers<AskDialogResult>();
  let timer: NodeJS.Timeout | number | undefined = undefined;
  let finished = false;

  const cleanup = () => {
    finished = true;
    clearInterval(timer);
  };

  if (signal) {
    signal.addEventListener("abort", () => {
      cleanup();
      reject(signal.reason);
    }, { once: true });
  }

  const check = async () => {
    if (finished) return;
    const ans = await fetchPendingAnswer();
    if (ans && !finished) {
      cleanup();
      localAbort.abort(); // 关闭本地 TUI 弹窗

      let chosenDisplay = "";
      const results: AskDialogResultItem[] = questions.map((q) => {
        let chosenLabel = "";
        if (ans.choice_index !== undefined && ans.choice_index !== null) {
          const idx = ans.choice_index - 1;
          if (idx >= 0 && idx < q.options.length) {
            chosenLabel = q.options[idx].label;
          }
        }
        if (!chosenLabel && ans.choice_label) {
          chosenLabel = ans.choice_label;
        }

        if (chosenLabel) {
          chosenDisplay = chosenLabel;
          return {
            id: q.id,
            selectedOptions: [chosenLabel],
            customInput: undefined
          };
        } else {
          chosenDisplay = ans.custom_input || "自定义输入";
          return {
            id: q.id,
            selectedOptions: [],
            customInput: ans.custom_input || ""
          };
        }
      });

      if (notifyFn) {
        notifyFn(`✅ 已接收微信远程选择: [${chosenDisplay}]，正在继续推进...`, "info");
      }
      resolve({ results });
    }
  };

  timer = setInterval(check, 1000);
  return promise;
}

function wrapUI(ui: unknown, notifyFn?: (msg: string, type?: "info" | "warning" | "error") => void) {
  if (!ui || typeof ui !== "object") return;
  const wrapped = ui as WrappedUIContext;
  if (wrapped.__wechat_wrapped) return;
  wrapped.__wechat_wrapped = true;
  if (notifyFn) {
    notifyFn(`[DEBUG] wrapUI: select=${typeof wrapped.select}, askDialog=${typeof wrapped.askDialog}`, "info");
  }
  // 1. 拦截富文本 askDialog
  if (typeof wrapped.askDialog === "function") {
    const origAskDialog = wrapped.askDialog.bind(wrapped);
    wrapped.askDialog = async (questions, opts) => {
      const localAbort = new AbortController();
      const combinedSignal = opts?.signal
        ? AbortSignal.any([opts.signal, localAbort.signal])
        : localAbort.signal;

      const localPromise = origAskDialog(questions, { ...opts, signal: combinedSignal })
        .catch((err: unknown) => {
          if (localAbort.signal.aborted) {
            return new Promise<AskDialogResult | undefined>(() => {});
          }
          throw err;
        });

      const wechatPromise = pollForAskDialog(questions, localAbort, opts?.signal, wrapped.notify.bind(wrapped));

      return await Promise.race([localPromise, wechatPromise]);
    };
  }

  // 2. 拦截选择器 select
  if (typeof wrapped.select === "function") {
    const origSelect = wrapped.select.bind(wrapped);
    wrapped.select = async (title, options, opts) => {
      const localAbort = new AbortController();
      const combinedSignal = opts?.signal
        ? AbortSignal.any([opts.signal, localAbort.signal])
        : localAbort.signal;

      const localPromise = origSelect(title, options, { ...opts, signal: combinedSignal })
        .catch((err: unknown) => {
          if (localAbort.signal.aborted) {
            return new Promise<string | undefined>(() => {});
          }
          throw err;
        });

      const wechatPromise = pollForSelect(options, localAbort, opts?.signal, wrapped.notify.bind(wrapped));

      return await Promise.race([localPromise, wechatPromise]);
    };
  }
}

let activeInjectorCleaner: (() => void) | null = null;

function startAnswerInjector(
  notifyFn: (msg: string, type?: "info" | "warning" | "error") => void
): () => void {
  let isDone = false;
  const timer = setInterval(async () => {
    if (isDone) return;
    const ans = await fetchPendingAnswer();
    if (ans && !isDone) {
      isDone = true;
      clearInterval(timer);

      const choiceIdx = ans.choice_index ?? 1;
      const label = ans.choice_label || `选项 ${choiceIdx}`;

      notifyFn(`✅ 收到微信远程选择 [${choiceIdx}. ${label}]，正在自动注入终端并继续...`, "info");

      // 构造终端按键序列 (Down Arrow + Enter)
      const steps = Math.max(0, choiceIdx - 1);
      let seq = "";
      for (let i = 0; i < steps; i++) {
        seq += "\x1b[B";
      }
      seq += "\r";

      setTimeout(() => {
        try {
          process.stdin.emit("data", Buffer.from(seq));
        } catch {
          // 容错
        }
      }, 150);
    }
  }, 1000);

  return () => {
    isDone = true;
    clearInterval(timer);
  };
}

export default function (pi: ExtensionAPI) {
  // 1. 会话启动就绪
  pi.on("session_start", async (_event, ctx) => {
    try {
      wrapUI(ctx.ui, ctx.ui.notify.bind(ctx.ui));
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
      wrapUI(ctx.ui, ctx.ui.notify.bind(ctx.ui));
      if (activeInjectorCleaner) {
        activeInjectorCleaner();
        activeInjectorCleaner = null;
      }
      fetchPendingAnswer().catch(() => {});
      activeInjectorCleaner = startAnswerInjector(ctx.ui.notify.bind(ctx.ui));

      let questions: unknown[] = [];
      if (event.input && typeof event.input === "object" && "questions" in event.input) {
        const candidate = event.input.questions;
        if (Array.isArray(candidate)) {
          questions = candidate;
        }
      }

      const collabUrl = getActiveCollabUrl();
      const sessionName = ctx.cwd ? path.basename(ctx.cwd) : "omp 会话";

      ctx.ui.notify(`🔔 [${config.tag}] 选项已触发，正在推送至微信...`, "info");

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
          ctx.ui.notify(`📱 [${config.tag}] 选项已送达微信！您可直接在微信回复序号确认，或点击卡片链接操作。`, "info");
        }
      });
    }
  });

  // 3. 核心监控：当 Agent 结束或异常中断时
  pi.on("agent_end", async (event, ctx) => {
    if (activeInjectorCleaner) {
      activeInjectorCleaner();
      activeInjectorCleaner = null;
    }
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
