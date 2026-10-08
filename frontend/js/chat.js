/**
 * ================================================================
 * 聊天模块 — chat.js
 * ================================================================
 * 负责聊天区域的所有交互：
 * - 消息发送与显示
 * - SSE 流式解析（delta / tool_call / tool_result / done / error）
 * - DOM 更新
 */

// ========== DOM 引用 ==========
const messageList = document.getElementById("message-list");
const messageInput = document.getElementById("message-input");
const btnSend = document.getElementById("btn-send");
const webSearchToggle = document.getElementById("toggle-web-search");
const thinkingToggle = document.getElementById("toggle-thinking");

// ========== 流式状态 ==========
let currentStreamingBubble = null;
let currentStreamingContent = "";
let currentEventType = "delta";

// 停止生成：保存当前请求的中断控制器
let chatAbortController = null;
let generating = false;          // 是否正在流式生成（决定发送按钮是「发送」还是「停止」）

// 思考过程（仅开启「深度思考」时会有内容）
let currentReasoningContent = "";
let currentReasoningBlock = null;
let currentReasoningContentEl = null;


// ========== 顶部功能开关（联网搜索 / 深度思考） ==========
// 开关状态存 localStorage，刷新页面后保持用户的选择。
// 两个开关默认都关闭：
//   - 联网搜索：按次额外计费，避免无意中产生费用
//   - 深度思考：会把首字响应从约 0.6 秒拖到 5-6 秒，需要时才开
const TOGGLES = [
    {
        el: webSearchToggle,
        storageKey: "xiaozhi_web_search_enabled",
        name: "联网搜索",
        icon: "🌐",
    },
    {
        el: thinkingToggle,
        storageKey: "xiaozhi_thinking_enabled",
        name: "深度思考",
        icon: "🧠",
    },
];

/** 读取联网搜索开关状态 */
function isWebSearchEnabled() {
    return !!(webSearchToggle && webSearchToggle.checked);
}

/** 读取深度思考开关状态 */
function isThinkingEnabled() {
    return !!(thinkingToggle && thinkingToggle.checked);
}

/** 初始化所有顶部开关：恢复上次状态 + 监听切换 */
function initToggles() {
    TOGGLES.forEach(({ el, storageKey, name, icon }) => {
        if (!el) {
            console.warn(`未找到「${name}」开关元素`);
            return;
        }

        // 恢复上次的选择
        try {
            if (localStorage.getItem(storageKey) === "1") {
                el.checked = true;
            }
        } catch (e) {
            console.warn(`读取「${name}」开关状态失败:`, e);
        }

        // 切换时持久化
        el.addEventListener("change", () => {
            try {
                localStorage.setItem(storageKey, el.checked ? "1" : "0");
            } catch (e) {
                console.warn(`保存「${name}」开关状态失败:`, e);
            }
            console.log(`${icon} ${name}:`, el.checked ? "已开启" : "已关闭");
        });

        console.log(`${icon} ${name}开关就绪，当前:`, el.checked ? "开启" : "关闭");
    });
}


// ========== 初始化 ==========
function initChat() {
    // 发送按钮身兼两职：平时「发送」，生成中变「停止」
    btnSend.addEventListener("click", () => {
        if (generating) {
            stopGenerating();
        } else {
            handleSend();
        }
    });
    messageInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (generating) return;   // 生成中回车不重发
            handleSend();
        }
    });
    messageInput.addEventListener("input", () => autoResize(messageInput));

    // 文件上传（支持多选 + 拖拽）
    const btnUpload = document.getElementById("btn-upload");
    const fileInput = document.getElementById("file-input");
    const inputArea = document.getElementById("input-area");
    btnUpload.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", handleFileUpload);

    // 拖拽文件到输入区即可上传（多文件）
    if (inputArea) {
        let dragDepth = 0;
        const showDrag = (on) => inputArea.classList.toggle("drag-over", on);

        ["dragenter", "dragover"].forEach((evt) => {
            inputArea.addEventListener(evt, (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (e.type === "dragenter") dragDepth++;
                showDrag(true);
            });
        });
        ["dragleave", "dragend"].forEach((evt) => {
            inputArea.addEventListener(evt, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dragDepth = Math.max(0, dragDepth - 1);
                if (dragDepth === 0) showDrag(false);
            });
        });
        inputArea.addEventListener("drop", (e) => {
            e.preventDefault();
            e.stopPropagation();
            dragDepth = 0;
            showDrag(false);
            const files = e.dataTransfer && e.dataTransfer.files;
            if (files && files.length) uploadFiles(files);
        });
    }

    // 顶部功能开关（联网搜索 / 深度思考）
    initToggles();
}


// ========== 多文件上传 ==========

/** 单次上传的文件数量上限（与服务端保持一致） */
const MAX_UPLOAD_FILES = 20;

/** 把字节数转成人话 */
function formatSize(bytes) {
    if (!bytes && bytes !== 0) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

/**
 * 在聊天区插入一个附件卡片（展示本次上传了哪些文件）
 */
function appendAttachmentCard(payload) {
    const div = document.createElement("div");
    div.className = "message assistant attachment-msg";

    const card = document.createElement("div");
    card.className = "attachment-card";

    const head = document.createElement("div");
    head.className = "attachment-head";
    head.innerHTML =
        '<span class="attachment-title">📎 已上传 ' + payload.ok + " 个文件</span>" +
        (payload.batchId
            ? '<span class="attachment-batch">批次 ' + payload.batchId + "</span>"
            : "");
    card.appendChild(head);

    payload.files.forEach((f) => {
        const row = document.createElement("div");
        row.className = "attachment-item";

        const name = document.createElement("span");
        name.className = "attachment-name";
        name.textContent = f.original_name || f.filename;
        name.title = f.path || f.filename;

        const meta = document.createElement("span");
        meta.className = "attachment-meta";
        const bits = [formatSize(f.size)];
        if (f.columns && f.columns.length) {
            bits.push(f.columns.length + " 列");
        }
        if (typeof f.rows === "number") {
            bits.push(f.rows + " 行");
        }
        meta.textContent = bits.join(" · ");

        const cols = document.createElement("span");
        cols.className = "attachment-cols";
        cols.textContent = (f.columns || []).slice(0, 8).join("、");

        row.appendChild(name);
        row.appendChild(meta);
        if ((f.columns || []).length) row.appendChild(cols);

        // 数据体检结果：列类型 + 数值范围 + 告警
        const profileEl = buildProfileBlock(f.profile);
        if (profileEl) row.appendChild(profileEl);

        card.appendChild(row);
    });

    if (payload.errors && payload.errors.length) {
        const errBox = document.createElement("div");
        errBox.className = "attachment-errors";
        errBox.textContent =
            "未上传：" + payload.errors.map((e) => e.original_name + "（" + e.reason + "）").join("；");
        card.appendChild(errBox);
    }

    const tip = document.createElement("div");
    tip.className = "attachment-tip";
    tip.textContent =
        payload.files.length > 1
            ? "这些是分片时，直接说「把它们合并后画个柱状图」即可。"
            : "直接说要怎么分析这份数据即可。";
    card.appendChild(tip);

    div.appendChild(card);
    messageList.appendChild(div);
    messageList.scrollTop = messageList.scrollHeight;
}


/**
 * 把后端返回的「数据体检」结果渲染成一小块摘要
 *
 * profile 结构（见 backend/routes/upload.py 的 _profile）：
 *   { column_types: {列名: 类型}, numeric_stats: {列名: {min,max,mean,outliers}},
 *     missing: {列名: 数量}, warnings: [...] }
 */
function buildProfileBlock(profile) {
    if (!profile || !profile.columns || !profile.columns.length) return null;

    const wrap = document.createElement("div");
    wrap.className = "attachment-profile";

    // 1) 字段类型概览：数值 3 · 文本 8 · 日期 2
    const types = profile.column_types || {};
    const counts = {};
    Object.values(types).forEach((t) => { counts[t] = (counts[t] || 0) + 1; });
    const typeLine = Object.entries(counts)
        .map(([t, n]) => t + " " + n)
        .join(" · ");
    if (typeLine) {
        const el = document.createElement("div");
        el.className = "profile-line";
        el.textContent = "字段类型：" + typeLine;
        wrap.appendChild(el);
    }

    // 2) 数值列范围（最多展示 3 列，避免刷屏）
    const stats = profile.numeric_stats || {};
    const statKeys = Object.keys(stats).slice(0, 3);
    if (statKeys.length) {
        const el = document.createElement("div");
        el.className = "profile-line";
        el.textContent = "数值范围：" + statKeys.map((k) => {
            const s = stats[k];
            return k + " " + s.min + "~" + s.max;
        }).join("；");
        wrap.appendChild(el);
    }

    // 3) 告警
    (profile.warnings || []).slice(0, 3).forEach((w) => {
        const el = document.createElement("div");
        el.className = "profile-warn";
        el.textContent = "⚠ " + w;
        wrap.appendChild(el);
    });

    return wrap;
}


/**
 * 上传一批文件（多选或拖拽都走这里）
 */
async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    setInputEnabled(false);
    appendMessage("user", "📎 上传文件：" + files.map((f) => f.name).join("、"));

    try {
        const formData = new FormData();
        files.forEach((f) => formData.append("files", f));

        const resp = await authFetch("/api/upload/batch", { method: "POST", body: formData });
        let data;
        try {
            data = await resp.json();
        } catch (e) {
            throw new Error("服务器返回异常（HTTP " + resp.status + "）");
        }
        if (!resp.ok) throw new Error(data.detail || "上传失败");

        console.log("📦 批量上传成功:", data);
        appendAttachmentCard({
            ok: data.ok,
            batchId: data.batch_id,
            files: data.files || [],
            errors: data.errors || [],
        });

        // 把文件信息填到输入框，用户补充指令后发送
        if (data.files && data.files.length) {
            const lines = data.files.map(
                (f) => "  - " + f.filename + (f.rows ? "（" + f.rows + " 行）" : "")
            );
            const head =
                data.files.length > 1
                    ? "我上传了 " + data.files.length + " 个文件（同一批次，文件名有相同前缀）：\n"
                    : "我上传了文件：\n";
            messageInput.value = head + lines.join("\n") + "\n";
            autoResize(messageInput);
        }
    } catch (err) {
        console.error("上传失败:", err);
        appendMessage("assistant", "❌ 上传失败：" + err.message);
    } finally {
        setInputEnabled(true);
        const fileInput = document.getElementById("file-input");
        if (fileInput) fileInput.value = "";  // 允许重复选择同一文件
        messageInput.focus();
        const pos = messageInput.value.length;
        messageInput.setSelectionRange(pos, pos);
    }
}


/**
 * 文件选择框 change 事件：多选时一次性上传全部
 */
async function handleFileUpload(e) {
    const files = e.target.files;
    if (!files || !files.length) return;

    if (files.length > MAX_UPLOAD_FILES) {
        appendMessage(
            "assistant",
            "❌ 一次最多上传 " + MAX_UPLOAD_FILES + " 个文件（当前选了 " + files.length + " 个）"
        );
        e.target.value = "";
        return;
    }
    await uploadFiles(files);
}


/**
 * 在聊天区插入一张图表
 */
function appendPlot(url) {
    const div = document.createElement("div");
    div.className = "message assistant";

    const img = document.createElement("img");
    img.src = url;
    img.className = "plot-image";
    img.loading = "lazy";
    img.alt = "图表";

    div.appendChild(img);
    messageList.appendChild(div);
    messageList.scrollTop = messageList.scrollHeight;
}


// ========== 发送消息 ==========
async function handleSend() {
    const content = messageInput.value.trim();
    if (!content) return;

    // 如果没有会话，先创建
    if (!AppState.currentSessionId) {
        try {
            await createCurrentSession();
        } catch (e) {
            console.error("创建会话失败:", e);
            alert("创建会话失败: " + e.message);
            return;
        }
    }

    console.log("📤 发送消息, session:", AppState.currentSessionId, "content:", content);

    // 禁用输入
    setInputEnabled(false);
    setGenerating(true);

    // 显示用户消息
    appendMessage("user", content);
    messageInput.value = "";
    autoResize(messageInput);

    // 「停止生成」用的中断控制器
    chatAbortController = new AbortController();

    try {
        // 发起 SSE 请求
        const sessionId = AppState.currentSessionId;
        const useWebSearch = isWebSearchEnabled();
        const useThinking = isThinkingEnabled();

        console.log(
            "⚙️ 本次请求开关 — 联网搜索:",
            useWebSearch ? "开" : "关",
            "| 深度思考:",
            useThinking ? "开" : "关"
        );

        const stream = await sendMessage(
            sessionId,
            {
                content: content,
                enable_search: useWebSearch,
                enable_thinking: useThinking,
            },
            chatAbortController.signal
        );

        // 读取流式响应
        await readSSEStream(stream);
    } catch (error) {
        if (error.name === "AbortError") {
            // 用户主动点了「停止」——不是错误，保留已生成的内容
            console.log("⏹ 用户已停止生成");
            if (currentStreamingBubble) {
                currentStreamingBubble.classList.remove("streaming");
                const el = currentStreamingBubble.querySelector(".message-content");
                if (el && !el.textContent.trim()) {
                    el.textContent = "（已停止）";
                }
            }
            appendStoppedNotice();
        } else {
            console.error("❌ 聊天错误:", error);
            appendMessage("assistant", "❌ 出错了：" + error.message);
        }
    } finally {
        chatAbortController = null;
        setGenerating(false);
        setInputEnabled(true);
        messageInput.focus();
    }
}


/** 在消息区插一条"已停止"的轻提示 */
function appendStoppedNotice() {
    const div = document.createElement("div");
    div.className = "message assistant stopped-notice";
    div.textContent = "⏹ 已停止生成";
    messageList.appendChild(div);
    messageList.scrollTop = messageList.scrollHeight;
}


/**
 * 停止生成：中断当前流式请求
 */
function stopGenerating() {
    if (chatAbortController) {
        chatAbortController.abort();
    }
}


// ========== 读取 SSE 流 ==========
async function readSSEStream(stream) {
    if (!stream) {
        console.error("❌ stream 为 null");
        throw new Error("无法读取响应流");
    }

    console.log("📡 开始读取 SSE 流...");

    const reader = stream.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    // 创建 AI 气泡并加入页面
    currentStreamingBubble = createMessageBubble("assistant", "");
    messageList.appendChild(currentStreamingBubble);
    currentStreamingContent = "";

    // 重置思考过程状态
    currentReasoningContent = "";
    currentReasoningBlock = null;
    currentReasoningContentEl = null;

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                console.log("📡 流结束 (done=true)");
                break;
            }

            // 解码新收到的字节
            const text = decoder.decode(value, { stream: true });
            buffer += text;

            // 按双换行分割 SSE 消息
            const parts = buffer.split("\n\n");
            // 最后一段可能不完整，保留
            buffer = parts.pop() || "";

            for (const part of parts) {
                if (!part.trim()) continue;
                processSSEMessage(part);
            }
        }
    } finally {
        reader.releaseLock();
        // 兜底：流结束时确保思考区已收起
        collapseReasoning();
        if (currentStreamingBubble) {
            currentStreamingBubble.classList.remove("streaming-cursor");
        }
        currentStreamingBubble = null;
    }
}


// ========== 解析单条 SSE 消息 ==========
function processSSEMessage(raw) {
    const lines = raw.split("\n");
    let eventType = "delta";
    let dataStr = "";

    for (const line of lines) {
        if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
            dataStr = line.slice(6);
        }
    }

    if (!dataStr) return;

    let data;
    try {
        data = JSON.parse(dataStr);
    } catch (e) {
        console.warn("JSON 解析失败:", dataStr);
        return;
    }

    console.log("📨 SSE 事件:", eventType, data);

    switch (eventType) {
        case "reasoning":
            // 思考过程增量（仅在开启「深度思考」时出现）
            if (data.content) {
                appendReasoning(data.content);
            }
            break;

        case "delta":
            // 第一条正式回答到达 → 思考阶段结束，自动折叠思考区
            collapseReasoning();
            if (data.content) {
                currentStreamingContent += data.content;
                if (currentStreamingBubble) {
                    const el = currentStreamingBubble.querySelector(".message-content");
                    // 流式输出期间用 textContent（速度快、不闪烁）
                    // 流结束后再用 markdown 渲染
                    if (el) el.textContent = currentStreamingContent;
                }
                messageList.scrollTop = messageList.scrollHeight;
            }
            break;

        case "tool_call":
            console.log("🔧", data.tool_name);
            // 记入侧边栏「工具调用」面板（面板开关关闭时只是不显示，不影响这里）
            if (typeof toolPanelTrackCall === "function") {
                toolPanelTrackCall(data.tool_call_id, data.tool_name, data.tool_args);
            }
            break;

        case "tool_result":
            console.log("✅", data.tool_name);
            // 显示生成的图表（服务器画好的图片）
            if (data.plots && data.plots.length) {
                data.plots.forEach(function (url) { appendPlot(url); });
            }
            // 显示可交互图表（plot_chart 下发的是 ECharts 配置，由浏览器渲染）
            if (data.charts && data.charts.length) {
                data.charts.forEach(function (spec) { appendChart(spec); });
            }
            // 回填工具面板的状态、结果摘要和耗时
            if (typeof toolPanelTrackResult === "function") {
                toolPanelTrackResult(
                    data.tool_call_id, data.tool_result, data.elapsed_ms
                );
            }
            break;

        case "done":
            console.log("✅ 对话完成");
            // 流结束后：将累积文本转为 Markdown HTML
            if (currentStreamingBubble && currentStreamingContent) {
                const el = currentStreamingBubble.querySelector(".message-content");
                if (el) {
                    el.innerHTML = renderMarkdown(currentStreamingContent);
                }
            }
            // 刷新侧栏
            if (typeof loadSessionList === "function") {
                loadSessionList().catch(() => {});
            }
            break;

        case "error":
            appendMessage("assistant", "❌ " + (data.error || "未知错误"));
            break;
    }
}


// ========== 思考过程（深度思考开启时） ==========

/**
 * 懒创建思考区块，插入到 AI 气泡的「角色标签」与「正文」之间。
 * 只在第一次收到 reasoning 事件时创建。
 */
function ensureReasoningBlock() {
    if (currentReasoningBlock) return currentReasoningBlock;
    if (!currentStreamingBubble) return null;

    const block = document.createElement("div");
    block.className = "reasoning-block thinking";

    const header = document.createElement("div");
    header.className = "reasoning-header";

    const title = document.createElement("span");
    title.className = "reasoning-title";
    title.textContent = "💭 思考过程";

    const hint = document.createElement("span");
    hint.className = "reasoning-hint";
    hint.textContent = "收起";

    header.appendChild(title);
    header.appendChild(hint);

    const content = document.createElement("div");
    content.className = "reasoning-content";

    block.appendChild(header);
    block.appendChild(content);

    // 点击标题栏可手动展开 / 收起
    header.addEventListener("click", () => {
        const nowCollapsed = block.classList.toggle("collapsed");
        hint.textContent = nowCollapsed ? "展开" : "收起";
    });

    // 插到角色标签之后（也就是正文之前）
    const labelEl = currentStreamingBubble.querySelector(".role-label");
    if (labelEl && labelEl.nextSibling) {
        currentStreamingBubble.insertBefore(block, labelEl.nextSibling);
    } else {
        currentStreamingBubble.appendChild(block);
    }

    currentReasoningBlock = block;
    currentReasoningContentEl = content;
    return block;
}

/** 追加一段思考内容 */
function appendReasoning(text) {
    const block = ensureReasoningBlock();
    if (!block) return;

    currentReasoningContent += text;
    if (currentReasoningContentEl) {
        currentReasoningContentEl.textContent = currentReasoningContent;
    }
    messageList.scrollTop = messageList.scrollHeight;
}

/** 思考阶段结束 → 自动折叠（幂等，可安全重复调用） */
function collapseReasoning() {
    if (!currentReasoningBlock) return;

    currentReasoningBlock.classList.remove("thinking");

    if (currentReasoningBlock.classList.contains("collapsed")) return;

    currentReasoningBlock.classList.add("collapsed");
    const hint = currentReasoningBlock.querySelector(".reasoning-hint");
    if (hint) hint.textContent = "展开";
}


// ========== 消息气泡 ==========
function appendMessage(role, content, timestamp) {
    const bubble = createMessageBubble(role, content, timestamp);
    messageList.appendChild(bubble);
    messageList.scrollTop = messageList.scrollHeight;
    return bubble;
}


function createMessageBubble(role, content, timestamp) {
    const div = document.createElement("div");
    div.className = "message " + role;

    const labels = {
        "user": "👤 你",
        "assistant": "🤖 小智",
        "tool-call": "🔧 工具",
    };

    const labelDiv = document.createElement("div");
    labelDiv.className = "role-label";
    labelDiv.textContent = labels[role] || role;

    const contentDiv = document.createElement("div");
    contentDiv.className = "message-content";
    contentDiv.textContent = content;

    const timeDiv = document.createElement("div");
    timeDiv.className = "timestamp";
    timeDiv.textContent = formatTime(timestamp || new Date().toISOString());

    div.appendChild(labelDiv);
    div.appendChild(contentDiv);
    div.appendChild(timeDiv);

    return div;
}


// ========== 工具函数 ==========
function setInputEnabled(enabled) {
    messageInput.disabled = !enabled;
    // 发送按钮不在这里禁用：生成中它要变成可点的「停止」按钮
    btnSend.disabled = false;
    const btnUpload = document.getElementById("btn-upload");
    if (btnUpload) btnUpload.disabled = !enabled;

    // 请求进行中禁用所有顶部开关，避免中途切换造成状态困惑
    TOGGLES.forEach(({ el }) => {
        if (!el) return;
        el.disabled = !enabled;
        const wrapper = el.closest(".search-toggle");
        if (wrapper) wrapper.classList.toggle("disabled", !enabled);
    });
}


/**
 * 切换「生成中」状态：发送按钮在「发送 ➤」和「停止 ⏹」之间切换
 */
function setGenerating(on) {
    generating = on;
    btnSend.textContent = on ? "⏹" : "➤";
    btnSend.title = on ? "停止生成（已生成的内容会保留）" : "发送消息";
    btnSend.classList.toggle("is-stopping", on);
}


function clearMessages() {
    messageList.innerHTML = `
        <div class="welcome-message">
            <p>👋 你好！我是 <strong>小智</strong>，你的 AI 助手。</p>
            <p>有什么可以帮助你的吗？</p>
        </div>
    `;
    // 新会话 = 工具调用记录也清空
    if (typeof toolPanelClear === "function") toolPanelClear();
}


async function loadSessionMessages(sessionId) {
    try {
        const session = await getSession(sessionId);
        messageList.innerHTML = "";

        // 用历史里已有的「工具调用痕迹」重建侧边栏面板
        if (typeof toolPanelRebuild === "function") {
            toolPanelRebuild(session.messages || []);
        }

        if (!session.messages || session.messages.length === 0) {
            clearMessages();
            return;
        }

        for (const msg of session.messages) {
            if (msg.role === "tool") {
                // 跳过工具消息，不显示
                continue;
            }
            if (msg.role === "assistant" && !msg.content) {
                // 只带 tool_calls 的助手消息（工具调用痕迹），没有正文，不显示空气泡
                continue;
            } else {
                const content = msg.content || "";
                const bubble = appendMessage(msg.role, content, msg.created_at);
                // 对 AI 消息渲染 Markdown
                if (msg.role === "assistant" && content) {
                    const el = bubble.querySelector(".message-content");
                    if (el) el.innerHTML = renderMarkdown(content);
                }
            }

            // 重新绘制历史图表：关闭浏览器 / 切换会话后，图表依然能看到
            if (msg.role === "assistant" && msg.charts_json) {
                try {
                    const specs = JSON.parse(msg.charts_json);
                    if (Array.isArray(specs)) {
                        specs.forEach((spec) => appendChart(spec));
                    }
                } catch (e) {
                    console.warn("历史图表配置解析失败:", e);
                }
            }
        }
    } catch (error) {
        console.error("加载消息失败:", error);
        clearMessages();
    }
}
