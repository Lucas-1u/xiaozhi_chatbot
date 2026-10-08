/**
 * ================================================================
 * 应用主入口 — app.js
 * ================================================================
 * 全局状态管理和模块初始化。
 */

const AppState = {
    currentSessionId: null,
};

/**
 * 获取或生成用户隐私令牌
 * 每个浏览器自动生成唯一 ID，会话与令牌绑定，互相看不到
 */
function getUserToken() {
    let token = localStorage.getItem("user_token");
    if (!token) {
        token = "u_" + Date.now().toString(36) + Math.random().toString(36).substr(2, 8);
        localStorage.setItem("user_token", token);
    }
    return token;
}

document.addEventListener("DOMContentLoaded", async () => {
    console.log("🚀 小智 AI 聊天助手 前端启动中...");
    console.log("🔑 用户令牌:", getUserToken());

    // 先过访问口令这关（后端没设口令时会直接放行）
    const authed = await ensureAccess();
    if (!authed) return;

    initChat();
    initSidebar();
    if (typeof initToolPanel === "function") initToolPanel();

    await loadSessionList();

    try {
        const sessions = await listSessions();
        if (sessions && sessions.length > 0) {
            await switchSession(sessions[0].id);
        }
    } catch (error) {
        console.warn("无法连接后端:", error.message);
    }

    console.log("✅ 前端初始化完成");
});


/**
 * 访问口令关卡。
 *
 * 流程：问后端要不要口令 → 不要就直接过；要就先拿本地 token 试一下，
 * 不通就弹输入框，输对了换到 token 存本地，下次直接进。
 *
 * @returns {Promise<boolean>} 是否可以继续初始化
 */
async function ensureAccess() {
    const gate = document.getElementById("auth-gate");
    let status;
    try {
        status = await checkAuthStatus();
    } catch (e) {
        console.warn("无法确认访问口令状态，按不需要处理:", e);
        return true;
    }

    if (!status.required) {
        console.log("🔓 后端未启用访问口令");
        return true;
    }

    if (status.valid) {
        console.log("🔓 本地访问令牌有效");
        return true;
    }

    // 需要输口令
    if (!gate) return true;
    gate.hidden = false;

    return new Promise((resolve) => {
        const form = document.getElementById("auth-form");
        const input = document.getElementById("auth-password");
        const btn = document.getElementById("auth-submit");
        const errEl = document.getElementById("auth-error");

        input.focus();

        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            const password = input.value;
            if (!password) return;

            btn.disabled = true;
            btn.textContent = "验证中…";
            errEl.hidden = true;

            try {
                const ok = await submitAccessPassword(password);
                if (ok) {
                    gate.hidden = true;
                    console.log("✅ 访问口令通过");
                    resolve(true);
                    return;
                }
                errEl.hidden = false;
                input.select();
            } catch (error) {
                errEl.textContent = "连接失败：" + error.message;
                errEl.hidden = false;
            } finally {
                btn.disabled = false;
                btn.textContent = "进入";
            }
        });
    });
}
