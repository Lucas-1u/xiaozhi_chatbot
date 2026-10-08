/* ============================================================================
 * toolpanel.js — 工具调用过程面板
 * ----------------------------------------------------------------------------
 * 显示小智这一轮对话里调用了哪些工具、传了什么参数、跑了多久、结果如何。
 * 面板位于右侧侧边栏底部，随侧边栏一起滑出 / 收回。
 *
 * 数据来源：
 *   1. 实时：SSE 的 tool_call / tool_result 事件
 *   2. 历史：GET /api/sessions/{id} 返回的 tool_calls_json + tool 消息
 *      （数据库里本来就有，用来防止模型「只说不做」，这里顺手拿来显示）
 *
 * 设计要点：
 *   - 面板可折叠，折叠状态存 localStorage（换会话 / 刷新后保持）
 *   - 新增一条记录时，若当前正展开则自动滚到底部
 * ==========================================================================*/

// ---------- 内部状态 ----------
let toolRecords = [];      // 当前会话的工具调用记录
let panelCollapsed = true;  // 面板是否折叠

// 工具元信息：展示名、颜色、参数摘要怎么抽
const TOOL_META = {
    run_python: {
        label: "运行 Python",
        icon: "🐍",
        desc: "在服务器上执行代码，用于读文件、计算、统计",
    },
    plot_chart: {
        label: "生成图表",
        icon: "📊",
        desc: "返回图表配置，由浏览器渲染成可交互图表",
    },
};

// ---------- 工具函数 ----------

/** 毫秒 → 人话 */
function formatElapsed(ms) {
    if (ms === undefined || ms === null) return "";
    if (ms < 1000) return Math.round(ms) + " ms";
    if (ms < 60000) return (ms / 1000).toFixed(1) + " s";
    return Math.floor(ms / 60000) + " 分 " + Math.round((ms % 60000) / 1000) + " 秒";
}

/** 字节 → 人话 */
function formatBytes(n) {
    if (!n) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
}

/**
 * 把工具参数压成一行人能读的摘要
 */
function summarizeArgs(toolName, args) {
    if (!args || typeof args !== "object") return "";

    if (toolName === "run_python") {
        const code = (args.code || "").trim();
        if (!code) return "";
        // 去掉注释和空行，取前几行有效代码
        const lines = code.split("\n")
            .map((l) => l.trim())
            .filter((l) => l && !l.startsWith("#"));
        const head = lines.slice(0, 3).join(" ");
        return head.length > 120 ? head.slice(0, 120) + "…" : head;
    }

    if (toolName === "plot_chart") {
        const bits = [];
        if (args.chart_type) bits.push(args.chart_type);
        if (args.title) bits.push(args.title);
        if (Array.isArray(args.categories)) bits.push(args.categories.length + " 个类目");
        if (Array.isArray(args.series)) bits.push(args.series.length + " 个系列");
        return bits.join(" · ");
    }

    // 其他工具：列出键名
    const keys = Object.keys(args);
    return keys.length ? keys.join("、") : "";
}

/**
 * 把工具结果压成一行摘要（成功/失败 + 关键信息）
 */
function summarizeResult(resultStr, toolName) {
    if (!resultStr) return { ok: true, text: "" };
    let obj;
    try {
        obj = JSON.parse(resultStr);
    } catch (e) {
        return { ok: true, text: String(resultStr).slice(0, 80) };
    }

    if (obj && obj.success === false) {
        return { ok: false, text: (obj.error || "执行失败").slice(0, 100) };
    }

    if (obj && obj.output) {
        return { ok: true, text: String(obj.output).replace(/\s+/g, " ").slice(0, 100) };
    }
    if (obj && obj.charts && obj.charts.length) {
        const types = obj.charts.map((c) => c.type).join("、");
        return { ok: true, text: "已生成 " + obj.charts.length + " 张图表（" + types + "）" };
    }
    if (obj && obj.plots && obj.plots.length) {
        return { ok: true, text: "已生成 " + obj.plots.length + " 张图片" };
    }
    return { ok: true, text: "" };
}

/** HTML 转义，防止参数里的尖括号破坏 DOM */
function esc(text) {
    return String(text == null ? "" : text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// ---------- 渲染 ----------

function getPanelBody() {
    return document.getElementById("tool-panel-body");
}

function renderToolPanel() {
    const body = getPanelBody();
    if (!body) return;

    const badge = document.getElementById("tool-panel-badge");
    if (badge) {
        badge.textContent = toolRecords.length;
        badge.classList.toggle("has-records", toolRecords.length > 0);
    }

        // 移动端浮动按钮上的角标
    const mobileBadge = document.getElementById("btn-tool-drawer-count");
    if (mobileBadge) {
        mobileBadge.textContent = toolRecords.length;
        mobileBadge.hidden = toolRecords.length === 0;
    }

    if (toolRecords.length === 0) {
        body.innerHTML = '<p class="tool-panel-empty">本会话还没有工具调用记录</p>';
        return;
    }

    // 按调用顺序从旧到新列（最新的在最下面，符合"刚刚发生了什么"的直觉）
    const html = toolRecords.map((rec, idx) => {
        const meta = TOOL_META[rec.name] || { label: rec.name, icon: "🔧", desc: "" };
        const statusCls = rec.state === "running" ? "running"
            : (rec.ok === false ? "failed" : "done");
        const statusText = rec.state === "running" ? "执行中…"
            : (rec.ok === false ? "失败" : "完成");
        const elapsed = formatElapsed(rec.elapsedMs);
        const resultLine = rec.resultText
            ? '<div class="tool-rec-result ' + (rec.ok === false ? "is-error" : "") + '">' + esc(rec.resultText) + "</div>"
            : "";
        const argLine = rec.argsSummary
            ? '<div class="tool-rec-args" title="' + esc(rec.argsSummary) + '">' + esc(rec.argsSummary) + "</div>"
            : "";

        return (
            '<div class="tool-rec ' + statusCls + '" data-idx="' + idx + '">' +
                '<div class="tool-rec-head">' +
                    '<span class="tool-rec-idx">' + (idx + 1) + "</span>" +
                    '<span class="tool-rec-icon">' + meta.icon + "</span>" +
                    '<span class="tool-rec-name">' + esc(meta.label) + "</span>" +
                    '<span class="tool-rec-code">' + esc(rec.name) + "</span>" +
                    '<span class="tool-rec-status">' + statusText + (elapsed ? " · " + elapsed : "") + "</span>" +
                "</div>" +
                argLine +
                resultLine +
            "</div>"
        );
    }).join("");

    body.innerHTML = html;

    // 展开状态下自动滚到底部，跟着最新调用走
    if (!panelCollapsed) {
        body.scrollTop = body.scrollHeight;
    }
}

// ---------- 对外接口（供 chat.js 调用） ----------

/**
 * 记录一次工具调用（收到 SSE tool_call 时）
 */
function toolPanelTrackCall(toolCallId, toolName, toolArgs) {
    // 同 id 只记一次（防御性重复）
    const existing = toolRecords.findIndex((r) => r.callId === toolCallId);
    if (existing !== -1) {
        toolRecords[existing].name = toolName;
        toolRecords[existing].argsSummary = summarizeArgs(toolName, toolArgs);
        renderToolPanel();
        return;
    }
    toolRecords.push({
        callId: toolCallId,
        name: toolName,
        argsSummary: summarizeArgs(toolName, toolArgs),
        state: "running",
        ok: null,
        elapsedMs: null,
        resultText: "",
    });
    renderToolPanel();
}

/**
 * 记录工具结果（收到 SSE tool_result 时）
 */
function toolPanelTrackResult(toolCallId, resultStr, elapsedMs) {
    let rec = toolRecords.find((r) => r.callId === toolCallId);
    if (!rec) {
        // 没收到过 tool_call 就先收到结果（少见），补一条记录
        rec = {
            callId: toolCallId,
            name: "未知工具",
            argsSummary: "",
            state: "running",
            ok: null,
            elapsedMs: null,
            resultText: "",
        };
        toolRecords.push(rec);
    }
    const sum = summarizeResult(resultStr, rec.name);
    rec.state = "done";
    rec.ok = sum.ok;
    rec.resultText = sum.text;
    rec.elapsedMs = (elapsedMs === undefined || elapsedMs === null) ? null : elapsedMs;
    renderToolPanel();
}

/**
 * 从历史消息重建面板
 * 调用时机：切换会话 / 加载历史时（数据库里本来就有工具调用痕迹）
 */
function toolPanelRebuild(messages) {
    toolRecords = [];
    if (!Array.isArray(messages)) {
        renderToolPanel();
        return;
    }

    // tool 消息的摘要：tool_call_id → summary
    const resultById = {};
    for (const msg of messages) {
        if (msg.role !== "tool") continue;
        let id = null;
        if (msg.tool_calls_json) {
            try {
                id = JSON.parse(msg.tool_calls_json).tool_call_id;
            } catch (e) { /* 忽略 */ }
        }
        if (id) resultById[id] = { summary: msg.content || "", time: msg.created_at || "" };
    }

    // assistant 的 tool_calls_json：[{id, function:{name, arguments}}]
    for (const msg of messages) {
        if (msg.role !== "assistant" || !msg.tool_calls_json) continue;
        let calls;
        try {
            calls = JSON.parse(msg.tool_calls_json);
        } catch (e) {
            continue;
        }
        if (!Array.isArray(calls)) continue;

        for (const call of calls) {
            const fn = call.function || {};
            let args = {};
            try {
                args = JSON.parse(fn.arguments || "{}");
            } catch (e) { /* 忽略 */ }
            const r = resultById[call.id];
            const sum = summarizeResult(r ? r.summary : "", fn.name);
            toolRecords.push({
                callId: call.id,
                name: fn.name,
                argsSummary: summarizeArgs(fn.name, args),
                state: "done",
                ok: sum.ok,
                elapsedMs: null,          // 历史记录里没有耗时数据
                resultText: sum.text,
                time: msg.created_at || "",
            });
        }
    }

    renderToolPanel();
}

/**
 * 清空面板（新建会话时）
 */
function toolPanelClear() {
    toolRecords = [];
    renderToolPanel();
}

// ---------- 抽屉开合（右侧，与左侧会话抽屉同一套交互） ----------

const TOOL_DRAWER_WIDTH = 360;     // 与 sidebar.css 的 #tool-drawer width 保持一致
const TOOL_EDGE_TRIGGER_PX = 1;    // 贴边触发宽度（未展开时只有这么窄）
const TOOL_HIDE_DELAY = 220;

let toolDrawerPinned = false;
let toolHideTimer = null;

function isMobileViewport() {
    return window.innerWidth <= 768;
}

function getToolDrawer() {
    return document.getElementById("tool-drawer");
}

function isToolDrawerOpen() {
    const d = getToolDrawer();
    return !!d && d.classList.contains("is-open");
}

function openToolDrawer() {
    const d = getToolDrawer();
    if (!d) return;
    if (toolHideTimer) {
        clearTimeout(toolHideTimer);
        toolHideTimer = null;
    }
    d.classList.add("is-open");
}

function hideToolDrawerNow() {
    const d = getToolDrawer();
    if (d) d.classList.remove("is-open");
}

function scheduleHideToolDrawer() {
    if (toolHideTimer) clearTimeout(toolHideTimer);
    toolHideTimer = setTimeout(() => {
        if (toolDrawerPinned) return;
        hideToolDrawerNow();
        toolHideTimer = null;
    }, TOOL_HIDE_DELAY);
}

/** 固定 / 取消固定工具栏（图钉按钮） */
function toggleToolDrawerPin() {
    const d = getToolDrawer();
    if (!d) return;

    if (isMobileViewport()) {
        // 移动端：图钉当"开/关"用
        if (isToolDrawerOpen()) {
            hideToolDrawerNow();
        } else {
            openToolDrawer();
        }
        return;
    }

    toolDrawerPinned = !toolDrawerPinned;
    document.body.classList.toggle("tool-drawer-pinned", toolDrawerPinned);
    d.classList.toggle("is-pinned", toolDrawerPinned);
    const pin = document.getElementById("tool-drawer-pin");
    if (pin) pin.classList.toggle("is-active", toolDrawerPinned);
    localStorage.setItem("xiaoZhi_toolDrawerPinned", toolDrawerPinned ? "1" : "0");

    if (toolDrawerPinned) {
        openToolDrawer();
    } else {
        scheduleHideToolDrawer();
    }
}

/**
 * 桌面端：鼠标贴到窗口最右边（1px）→ 工具抽屉滑出。
 *
 * 触发宽度分两种情况（和左侧会话抽屉同一套逻辑）：
 *   - 未展开：只有最外侧 1px 触发，避免"鼠标还没到边上抽屉就弹出来"
 *   - 已展开：整个抽屉宽度都算"在里面"，否则鼠标一动就误判离开 → 抖动
 *
 * 同样用**坐标判断**而不是 mouseenter/leave（抽屉滑出会盖住触发条）。
 */
function initToolDrawerHover() {
    const drawer = getToolDrawer();
    if (!drawer) return;

    document.addEventListener("mousemove", (e) => {
        if (isMobileViewport()) return;
        if (toolDrawerPinned) return;

        const open = isToolDrawerOpen();
        // 未展开：只有窗口最右侧那一像素触发
        // 已展开：整个抽屉宽度都算"在里面"
        const inZone = open
            ? e.clientX >= (window.innerWidth - TOOL_DRAWER_WIDTH - TOOL_EDGE_TRIGGER_PX)
            : e.clientX >= (window.innerWidth - TOOL_EDGE_TRIGGER_PX);

        if (inZone) {
            openToolDrawer();
        } else if (open) {
            scheduleHideToolDrawer();
        }
    });

    // 恢复上次的固定状态
    if (!isMobileViewport() && localStorage.getItem("xiaoZhi_toolDrawerPinned") === "1") {
        toolDrawerPinned = true;
        document.body.classList.add("tool-drawer-pinned");
        drawer.classList.add("is-pinned");
        const pin = document.getElementById("tool-drawer-pin");
        if (pin) pin.classList.add("is-active");
        openToolDrawer();
    }

    // Esc 关闭工具抽屉（与左侧会话抽屉各自响应，两边互不干扰）
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape") return;
        if (isMobileViewport()) {
            if (isToolDrawerOpen()) hideToolDrawerNow();
            return;
        }
        if (!isToolDrawerOpen()) return;

        if (toolDrawerPinned) {
            toolDrawerPinned = false;
            document.body.classList.remove("tool-drawer-pinned");
            drawer.classList.remove("is-pinned");
            const pin = document.getElementById("tool-drawer-pin");
            if (pin) pin.classList.remove("is-active");
            localStorage.setItem("xiaoZhi_toolDrawerPinned", "0");
        }
        if (toolHideTimer) clearTimeout(toolHideTimer);
        hideToolDrawerNow();
    });
}

function initToolPanel() {
    const drawer = getToolDrawer();
    const pin = document.getElementById("tool-drawer-pin");
    const mobileBtn = document.getElementById("btn-tool-drawer");

    if (!drawer) return;

    // 图钉：桌面端固定 / 移动端开合
    if (pin) pin.addEventListener("click", toggleToolDrawerPin);

    // 移动端浮动开关
    if (mobileBtn) mobileBtn.addEventListener("click", toggleToolDrawerPin);

    // 桌面端 hover 触发
    initToolDrawerHover();

    // 移动端右侧浮动按钮的样式（桌面端隐藏）
    drawer.addEventListener("mouseenter", () => {
        if (!isMobileViewport()) return;
    });

    renderToolPanel();
}