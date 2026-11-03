/**
 * omp 企业微信守护扩展 (omp-wechat-guard)
 * 
 * 功能:
 * 1. 拦截 ask 工具调用：提取问题与选项，附带 Collab 网页控制台链接，实时推送到微信。
 * 2. 微信远程应答自动注入：用户在微信对话框回复数字序号或内容时，自动解决待办选项，驱动 omp 继续执行！
 * 3. 监听会话停止：上游 API 报错停止时发送预警卡片；最终输出命中自定义关键词/正则 (如 "请回复") 时推送回复提醒，
 *    微信直接回复文字即作为新消息发送给 omp。
 * 4. 多端多主机统一接入：自动识别所在主机标签 (如 Redhat、Windows、Mac) 并上报。
 * 5. 注册 /wechat 斜杠命令，随时检查连接、测试推送、管理关键词规则或自更新 (/wechat update)。
 */

import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import * as cp from "node:child_process";
import { fileURLToPath } from "node:url";

const GITHUB_RAW_URL = "https://raw.githubusercontent.com/Bowen-0x00/omp-wechat-guard/main/extension/omp-wechat-guard.ts";

interface GuardConfig {
  tag?: string;
  url?: string;
  secret?: string;
  reply_patterns?: string[];
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

const CONFIG_PATH = path.join(os.homedir(), ".omp", "agent", "wechat-guard.json");

function readConfigFile(): GuardConfig {
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      const parsed: unknown = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
      if (parsed && typeof parsed === "object") return parsed as GuardConfig;
    }
  } catch {
    // 忽略配置文件读取异常
  }
  return {};
}

function loadGuardConfig(): { tag: string; url: string; secret: string } {
  const cfg = readConfigFile();
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

// -----------------------------------------------------------------------------
// 自定义输出关键词规则 (wechat-guard.json -> reply_patterns)
//   普通字符串: 子串匹配 (忽略大小写)，如 "请回复"
//   /正则/标志: 正则匹配，如 "/请(直接)?回复/"、"/^等待.*确认$/m"
// 每次会话停止时重新读取配置文件，修改后无需重启 omp。
// -----------------------------------------------------------------------------

interface CompiledPattern {
  source: string;
  regex: RegExp;
}

function compilePattern(source: string): CompiledPattern {
  const m = /^\/(.+)\/([a-z]*)$/s.exec(source);
  if (m) {
    // 去掉 g/y，避免 RegExp.test 的 lastIndex 状态
    return { source, regex: new RegExp(m[1], m[2].replace(/[gy]/g, "")) };
  }
  return { source, regex: new RegExp(source.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i") };
}

function loadReplyPatterns(): { patterns: CompiledPattern[]; errors: string[] } {
  const raw = readConfigFile().reply_patterns;
  const patterns: CompiledPattern[] = [];
  const errors: string[] = [];
  if (!Array.isArray(raw)) return { patterns, errors };
  for (const p of raw) {
    if (typeof p !== "string" || !p.trim()) continue;
    try {
      patterns.push(compilePattern(p));
    } catch (e) {
      errors.push(`${p} (${e instanceof Error ? e.message : String(e)})`);
    }
  }
  return { patterns, errors };
}

function saveReplyPatterns(list: string[]): void {
  let raw: Record<string, unknown> = {};
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      const parsed: unknown = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
      if (parsed && typeof parsed === "object") raw = parsed as Record<string, unknown>;
    }
  } catch {
    // 配置损坏时仅写入规则字段，避免抛错
  }
  raw.reply_patterns = list;
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(raw, null, 2) + "\n", "utf-8");
}

function extractText(message: unknown): string {
  if (!message || typeof message !== "object" || !("content" in message)) return "";
  const content = message.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  let text = "";
  for (const c of content) {
    if (c && typeof c === "object" && "type" in c && c.type === "text" && "text" in c && typeof c.text === "string") {
      text += c.text + "\n";
    }
  }
  return text.trim();
}

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
  totalOptions: number,
  notifyFn: (msg: string, type?: "info" | "warning" | "error") => void
): () => void {
  let isDone = false;
  const timer = setInterval(async () => {
    if (isDone) return;
    const ans = await fetchPendingAnswer();
    if (ans && !isDone) {
      isDone = true;
      clearInterval(timer);

      // 情况 A: 用户提供了自定义文本输入 (Other / 自定义内容)
      if (ans.custom_input) {
        notifyFn(`✍️ 收到微信自定义输入: [${ans.custom_input}]，正在选择 Other 并注入提交...`, "info");

        // 1. 移动光标至 "Other (type your own)" 并按回车进入文本输入态
        // 在 omp TUI 中，Other 位于所有预设选项之后，故需下移 totalOptions 次
        const steps = Math.max(1, totalOptions);
        let navSeq = "";
        for (let i = 0; i < steps; i++) {
          navSeq += "\x1b[B";
        }
        navSeq += "\r";

        try {
          process.stdin.emit("data", Buffer.from(navSeq));
        } catch {}

        // 2. 延迟 300ms 等待输入框挂载完毕，注入文本并回车提交
        setTimeout(() => {
          try {
            process.stdin.emit("data", Buffer.from(`${ans.custom_input}\r`));
          } catch {}
        }, 300);
        return;
      }

      // 情况 B: 用户选择了既定选项序号 (1, 2, ...)
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
        } catch {}
      }, 150);
    }
  }, 1000);

  return () => {
    isDone = true;
    clearInterval(timer);
  };
}

export default function (pi: ExtensionAPI) {
  // 会话停止后等待微信回复的轮询器 (每个会话独立，子代理会话不触发 session_stop)
  let activeReplyWaiter: { id: string; stop: () => void } | null = null;

  /** 停止等待微信回复；localHandled=true 表示用户已在本地继续，通知网关清除该待办。 */
  const cancelReplyWaiter = (localHandled: boolean) => {
    if (!activeReplyWaiter) return;
    const { id, stop } = activeReplyWaiter;
    activeReplyWaiter = null;
    stop();
    if (localHandled) void postEvent("resolved", { id });
  };

  const startReplyWaiter = (id: string, notifyFn: (msg: string, type?: "info" | "warning" | "error") => void) => {
    cancelReplyWaiter(false);
    let polling = false;
    const timer = setInterval(async () => {
      if (polling || activeReplyWaiter?.id !== id) return;
      polling = true;
      try {
        const ans = await fetchPendingAnswer();
        if (!ans || activeReplyWaiter?.id !== id) return;
        cancelReplyWaiter(false);
        const text = (ans.custom_input || ans.choice_label || "").trim();
        if (!text) return;
        notifyFn(`💬 收到微信回复: [${text}]，正在发送给 omp...`, "info");
        pi.sendUserMessage(text);
      } catch {
        // 保护会话不被轮询异常中断
      } finally {
        polling = false;
      }
    }, 2000);
    activeReplyWaiter = { id, stop: () => clearInterval(timer) };
  };

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
      let questions: unknown[] = [];
      if (event.input && typeof event.input === "object" && "questions" in event.input) {
        const candidate = event.input.questions;
        if (Array.isArray(candidate)) {
          questions = candidate;
        }
      }

      let totalOptions = 2;
      const firstQ = questions[0];
      if (firstQ && typeof firstQ === "object" && "options" in firstQ) {
        const optsCandidate = firstQ.options;
        if (Array.isArray(optsCandidate)) {
          totalOptions = optsCandidate.length;
        }
      }
      activeInjectorCleaner = startAnswerInjector(totalOptions, ctx.ui.notify.bind(ctx.ui));

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

  // 3. Agent 结束：清理选项注入器
  pi.on("agent_end", async () => {
    if (activeInjectorCleaner) {
      activeInjectorCleaner();
      activeInjectorCleaner = null;
    }
  });

  // 用户在本地继续对话 (或微信回复已注入) 时，停止等待微信回复
  pi.on("agent_start", async () => {
    cancelReplyWaiter(true);
  });

  pi.on("session_shutdown", async () => {
    cancelReplyWaiter(false);
  });

  // 4. 核心监控：主会话最终停止时 (session_stop 不会在子代理会话触发)
  //    - 上游 API 报错停止 -> 异常告警
  //    - 最终输出命中 reply_patterns -> 推送回复提醒，并等待微信回复注入
  //    该钩子在会话结算前被等待，因此上报一律不 await，避免拖慢停止。
  pi.on("session_stop", async (event, ctx) => {
    try {
      const lastMsg = event && typeof event === "object" && "last_assistant_message" in event
        ? event.last_assistant_message
        : undefined;
      if (!lastMsg || typeof lastMsg !== "object") return;

      const stopReason = "stopReason" in lastMsg && typeof lastMsg.stopReason === "string" ? lastMsg.stopReason : undefined;
      const text = extractText(lastMsg);
      const sessionName = ctx.cwd ? path.basename(ctx.cwd) : "omp 会话";

      if (stopReason === "error") {
        const errorMessage = "errorMessage" in lastMsg && typeof lastMsg.errorMessage === "string" ? lastMsg.errorMessage : "";
        ctx.ui.notify(`⚠️ [${config.tag}] 检测到任务异常停止，正在发送微信告警...`, "warning");
        void postEvent("error", {
          session_name: sessionName,
          cwd: ctx.cwd,
          error: "上游大模型 API 报错或网络连接异常中断",
          detail: (errorMessage || text).slice(0, 300),
          collab_url: getActiveCollabUrl()
        });
        return;
      }
      if (stopReason === "aborted" || !text) return;

      const { patterns, errors } = loadReplyPatterns();
      if (errors.length > 0) {
        ctx.ui.notify(`⚠️ wechat-guard 已跳过无效规则: ${errors.join("; ")}`, "warning");
      }
      const hit = patterns.find(p => p.regex.test(text));
      if (!hit) return;

      const id = `reply_${Date.now()}`;
      startReplyWaiter(id, ctx.ui.notify.bind(ctx.ui));
      ctx.ui.notify(`💬 [${config.tag}] 输出命中规则 ${hit.source}，正在推送回复提醒至微信...`, "info");
      void postEvent("reply", {
        id,
        session_name: sessionName,
        cwd: ctx.cwd,
        matched: hit.source,
        excerpt: text.slice(-500),
        collab_url: getActiveCollabUrl()
      }).then(ok => {
        if (ok) {
          ctx.ui.notify(`📱 [${config.tag}] 回复提醒已送达微信！在微信直接回复文字即可继续对话。`, "info");
        }
      });
    } catch {
      // 保护会话结算不被扩展异常阻断
    }
  });

  // 5. 注册 /wechat 斜杠命令
  pi.registerCommand("wechat", {
    description: "企业微信远程助手: test 发送测试卡片 | patterns 管理输出关键词规则 | update 更新扩展",
    handler: async (args, ctx) => {
      const trimmed = args.trim();
      const subcmd = trimmed.split(/\s+/)[0].toLowerCase();
      if (subcmd === "patterns") {
        const rest = trimmed.slice(subcmd.length).trim();
        const action = rest.split(/\s+/)[0].toLowerCase();
        const value = rest.slice(action.length).trim();
        const raw = readConfigFile().reply_patterns;
        const list = Array.isArray(raw) ? raw.filter((p): p is string => typeof p === "string") : [];

        if (action === "add" && value) {
          try {
            compilePattern(value);
          } catch (e) {
            ctx.ui.notify(`❌ 无效正则: ${e instanceof Error ? e.message : String(e)}`, "error");
            return;
          }
          if (!list.includes(value)) {
            list.push(value);
            saveReplyPatterns(list);
          }
          ctx.ui.notify(`✅ 已添加规则: ${value} (共 ${list.length} 条，立即生效)`, "info");
        } else if (action === "rm" && value) {
          const idx = /^\d+$/.test(value) ? Number(value) - 1 : list.indexOf(value);
          if (idx < 0 || idx >= list.length) {
            ctx.ui.notify(`❌ 未找到规则: ${value}`, "error");
            return;
          }
          const [removed] = list.splice(idx, 1);
          saveReplyPatterns(list);
          ctx.ui.notify(`🗑️ 已删除规则: ${removed}`, "info");
        } else if (action === "test" && value) {
          const { patterns, errors } = loadReplyPatterns();
          const hit = patterns.find(p => p.regex.test(value));
          const errNote = errors.length > 0 ? `\n⚠️ 无效规则: ${errors.join("; ")}` : "";
          ctx.ui.notify(hit ? `✅ 命中规则: ${hit.source}${errNote}` : `⚪ 未命中任何规则${errNote}`, "info");
        } else if (action === "" || action === "list") {
          const lines = list.length > 0
            ? list.map((p, i) => `  ${i + 1}. ${p}`).join("\n")
            : "  (未配置)";
          ctx.ui.notify(`📝 输出关键词规则 (${CONFIG_PATH} -> reply_patterns):\n${lines}`, "info");
        } else {
          ctx.ui.notify(
            "用法:\n" +
            "  /wechat patterns                列出规则\n" +
            "  /wechat patterns add <规则>     添加 (普通文字=包含匹配, /正则/flags=正则)\n" +
            "  /wechat patterns rm <序号|规则>  删除\n" +
            "  /wechat patterns test <文本>    测试文本是否命中",
            "info"
          );
        }
        return;
      }
      if (subcmd === "update") {
        // 优先从网关下载 (各主机必然能访问)，失败再回退 GitHub
        const sources = [`${config.url}/extension`, GITHUB_RAW_URL];
        let latest = "";
        let from = "";
        for (const src of sources) {
          try {
            const resp = await fetch(src, { signal: AbortSignal.timeout(10000) });
            const text = resp.ok ? await resp.text() : "";
            if (text.includes('pi.registerCommand("wechat"')) {
              latest = text;
              from = src;
              break;
            }
          } catch {
            // 尝试下一个来源
          }
        }
        if (!latest) {
          ctx.ui.notify(`❌ 下载扩展失败，已尝试: ${sources.join(" , ")}`, "error");
          return;
        }

        let target = path.join(os.homedir(), ".omp", "agent", "extensions", "omp-wechat-guard.ts");
        try {
          const self = fileURLToPath(import.meta.url);
          if (fs.existsSync(self)) target = self;
        } catch {
          // 非文件加载时使用默认安装路径
        }
        const current = fs.existsSync(target) ? fs.readFileSync(target, "utf-8") : "";
        if (current === latest) {
          ctx.ui.notify(`✅ 扩展已是最新 (${target})`, "info");
          return;
        }
        fs.writeFileSync(`${target}.tmp`, latest, "utf-8");
        fs.renameSync(`${target}.tmp`, target);
        ctx.ui.notify(`✅ 扩展已更新: ${target}\n来源: ${from}\n请重启 omp 会话使新版本生效。`, "info");
        return;
      }
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
        ctx.ui.notify("用法: /wechat test (发送测试卡片) | /wechat patterns (管理输出关键词规则) | /wechat update (更新扩展)", "info");
      }
    }
  });
}
