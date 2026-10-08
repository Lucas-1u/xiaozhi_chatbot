/**
 * ================================================================
 * 左侧会话抽屉 — sidebar.js
 * ================================================================
 * 会话列表渲染 + 抽屉开合：
 * - 会话列表渲染 / 创建 / 切换 / 删除
 * - 桌面端：鼠标蹭到窗口**最左边** → 会话列表从左侧滑出；移开 → 自动收回
 * - 移动端（无 hover）：点 ☰ 打开，点遮罩关闭
 *
 * 右侧「工具调用」抽屉在 toolpanel.js 里，逻辑相同但独立。
 */

// ========== DOM 元素引用 ==========
const sessionList = document.getElementById("session-list");
const btnNewSession = document.getElementById("btn-new-session");
const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");  // 仅移动端
const sidebarPin = document.getElementById("sidebar-pin");               // 桌面端图钉
const sidebar = document.getElementById("sidebar");

// ========== 抽屉开合状态 ==========
const SIDEBAR_WIDTH = 264;        // 与 sidebar.css 的 width 保持一致
const EDGE_TRIGGER_PX = 1;        // 贴边触发宽度（抽屉未开时只有这么窄）
const HIDE_DELAY = 220;           // 鼠标离开后延迟多久收回（毫秒），防抖动

let sidebarPinned = false;        // 是否被 ☰ 钉住
let hideTimer = null;

/** 移动端（窄屏）没有 hover 热区，靠按钮开合 */
function isMobileViewport() {
    return window.innerWidth <= 768;
}

function isSidebarOpen() {
    return sidebar.classList.contains("is-open") || sidebar.classList.contains("open");
}

function openSidebar() {
    if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
    }
    sidebar.classList.add("is-open");
    sidebar.classList.add("open");   // 兼容移动端
}

function hideSidebarNow() {
    sidebar.classList.remove("is-open");
    sidebar.classList.remove("open");
}

/**
 * 延迟收回：鼠标在抽屉和热区之间移动、或点击瞬间都会有一小段"空档"，
 * 立刻收回会闪一下，所以给个缓冲。
 */
function scheduleHideSidebar() {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
        if (sidebarPinned) return;
        hideSidebarNow();
        hideTimer = null;
    }, HIDE_DELAY);
}

/**
 * 固定 / 取消固定会话列表
 * 桌面端：图钉（在会话列表标题栏里）；移动端：左上角 ☰
 *
 * 为什么桌面端不放 ☰：侧栏从左边滑出后会把屏幕最左边整条盖住，
 * 放在那里的 ☰ 根本点不到。所以固定入口挪进侧栏自己的标题栏。
 */
function toggleSidebar() {
    if (isMobileViewport()) {
        if (isSidebarOpen()) {
            closeSidebar();
        } else {
            openSidebar();
            showOverlay();
        }
        return;
    }

    // 桌面端：切换「钉住」
    sidebarPinned = !sidebarPinned;
    document.body.classList.toggle("sidebar-pinned", sidebarPinned);
    sidebar.classList.toggle("is-pinned", sidebarPinned);
    setSidebarPinVisual(sidebarPinned);

    if (sidebarPinned) {
        openSidebar();
    } else {
        scheduleHideSidebar();
    }
    localStorage.setItem("xiaoZhi_sidebarPinned", sidebarPinned ? "1" : "0");
}

/** 同步图钉按钮的高亮状态（桌面端） */
function setSidebarPinVisual(pinned) {
    if (sidebarPin) sidebarPin.classList.toggle("is-active", pinned);
    // 移动端 ☰ 也顺带标一下（只是视觉，桌面端它本来就隐藏）
    if (btnToggleSidebar) btnToggleSidebar.classList.toggle("is-active", pinned);
}

/**
 * 桌面端 hover 触发（左边）。
 *
 * 触发宽度**分两种情况**，这是关键：
 *   - 抽屉没开时：只有鼠标贴到**最外侧 EDGE_TRIGGER_PX（1px）**才滑出
 *     （屏幕边缘光标会被系统挡住，甩一下就能命中）
 *   - 抽屉已开时：整个抽屉宽度 + 1px 都算"在里面"，否则鼠标在抽屉里
 *     一动就被判成"离开了" → 抽屉不停自己收回（抖动）
 *
 * 另一个关键点：**用鼠标坐标判断，不用 mouseenter / mouseleave**。
 * 抽屉滑出的瞬间会盖住那条细条，元素层面会立刻触发 mouseleave，
 * 结果抽屉刚出来就收回去。坐标判断没有这个问题。
 */
function initSidebarHover() {
    if (!sidebar) return;

    document.addEventListener("mousemove", (e) => {
        // 移动端不启用 hover 行为
        if (isMobileViewport()) return;
        // 钉住状态下不响应鼠标
        if (sidebarPinned) return;

        const open = isSidebarOpen();
        // 未展开：clientX < 1 即只有最外侧那一像素（x=0）触发
        // 已展开：整个抽屉宽度都算"在里面"
        const inZone = open
            ? e.clientX <= (SIDEBAR_WIDTH + EDGE_TRIGGER_PX)
            : e.clientX < EDGE_TRIGGER_PX;

        if (inZone) {
            openSidebar();
        } else if (open) {
            scheduleHideSidebar();
        }
    });

    // 鼠标"离开窗口"时立刻收回（否则切到别的程序后抽屉还挂着）
    document.addEventListener("mouseleave", () => {
        if (sidebarPinned || isMobileViewport()) return;
        hideSidebarNow();
    });

    // 键盘可达：Esc 关闭
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && isSidebarOpen()) {
            unpinSidebar();
            hideSidebarNow();
        }
    });
}

/** 取消左栏钉住 */
function unpinSidebar() {
    if (!sidebarPinned) return;
    sidebarPinned = false;
    document.body.classList.remove("sidebar-pinned");
    sidebar.classList.remove("is-pinned");
    setSidebarPinVisual(false);
    localStorage.setItem("xiaoZhi_sidebarPinned", "0");
}

/** 恢复上次钉住状态 */
function restoreSidebarPinned() {
    if (isMobileViewport() || !sidebar) return;
    if (localStorage.getItem("xiaoZhi_sidebarPinned") === "1") {
        sidebarPinned = true;
        document.body.classList.add("sidebar-pinned");
        sidebar.classList.add("is-pinned");
        setSidebarPinVisual(true);
        openSidebar();
    }
}

/**
 * 初始化侧栏模块的事件监听
 */
function initSidebar() {
    // 新建会话按钮
    btnNewSession.addEventListener("click", createCurrentSession);

    // 桌面端：会话列表标题栏里的图钉；移动端：☰ 开合
    if (sidebarPin) sidebarPin.addEventListener("click", toggleSidebar);
    if (btnToggleSidebar) btnToggleSidebar.addEventListener("click", toggleSidebar);

    // 桌面端 hover 热区
    initSidebarHover();
    restoreSidebarPinned();
}

/**
 * 加载并渲染会话列表
 */
async function loadSessionList() {
    try {
        const sessions = await listSessions();
        renderSessionList(sessions);
    } catch (error) {
        console.error("加载会话列表失败:", error);
        sessionList.innerHTML = `<p class="empty-hint">加载失败：${error.message}</p>`;
    }
}

/**
 * 渲染会话列表 DOM
 * @param {Array} sessions — 会话对象数组
 */
function renderSessionList(sessions) {
    if (!sessions || sessions.length === 0) {
        sessionList.innerHTML = `<p class="empty-hint">暂无会话，点击 ＋ 创建</p>`;
        return;
    }

    sessionList.innerHTML = sessions
        .map((s) => {
            const isActive = s.id === AppState.currentSessionId;
            const title = escapeHtml(s.title || "新对话");
            return `
                <div class="session-item ${isActive ? "active" : ""}"
                     data-session-id="${s.id}"
                     onclick="switchSession('${s.id}')">
                    <span class="session-title">${title}</span>
                    <button class="btn-delete"
                            title="删除会话"
                            onclick="event.stopPropagation(); deleteCurrentSession('${s.id}')">
                        ✕
                    </button>
                </div>
            `;
        })
        .join("");
}

/**
 * 创建新会话并切换过去
 */
async function createCurrentSession() {
    try {
        const session = await createSession();
        AppState.currentSessionId = session.id;
        clearMessages();
        await loadSessionList();
        // 移动端自动关闭侧栏
        closeSidebar();
    } catch (error) {
        console.error("创建会话失败:", error);
        alert("创建会话失败：" + error.message);
    }
}

/**
 * 切换到指定会话
 * @param {string} sessionId — 会话 ID
 */
async function switchSession(sessionId) {
    if (sessionId === AppState.currentSessionId) return;  // 已经是当前会话

    AppState.currentSessionId = sessionId;
    await loadSessionMessages(sessionId);
    await loadSessionList();
    closeSidebar();
}

/**
 * 删除指定会话
 * @param {string} sessionId — 会话 ID
 */
async function deleteCurrentSession(sessionId) {
    if (!confirm("确定要删除这个会话吗？删除后不可恢复。")) return;

    try {
        await deleteSession(sessionId);

        // 如果删除的是当前会话，切换到第一个可用会话或清空
        if (sessionId === AppState.currentSessionId) {
            AppState.currentSessionId = null;
            clearMessages();

            // 尝试切换到列表中第一个会话
            const sessions = await listSessions();
            if (sessions && sessions.length > 0) {
                await switchSession(sessions[0].id);
            }
        }

        await loadSessionList();
    } catch (error) {
        console.error("删除会话失败:", error);
        alert("删除会话失败：" + error.message);
    }
}

/**
 * 显示遮罩层（仅移动端用：点空白处关闭侧栏）
 */
function showOverlay() {
    let overlay = document.getElementById("sidebar-overlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "sidebar-overlay";
        overlay.addEventListener("click", closeSidebar);
        document.body.appendChild(overlay);
    }
    overlay.classList.add("visible");
}

/**
 * 关闭侧栏（移动端：点遮罩；桌面端：Esc）
 */
function closeSidebar() {
    if (isMobileViewport()) {
        hideSidebarNow();
        const overlay = document.getElementById("sidebar-overlay");
        if (overlay) overlay.classList.remove("visible");
    } else {
        unpinSidebar();
        hideSidebarNow();
    }
}
