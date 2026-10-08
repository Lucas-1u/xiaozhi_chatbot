/**
 * ================================================================
 * API 请求模块 — api.js
 * ================================================================
 * 封装所有与后端 API 的通信逻辑，包括：
 * - 普通 REST 请求（会话 CRUD、记忆管理）
 * - SSE 流式请求（聊天消息）
 *
 * 后端地址通过相对路径访问，部署时由 Nginx 反代到同一域名下。
 */

// ========== 基础配置 ==========
// 后端 API 基础路径（同域部署，使用相对路径）
const API_BASE = "/api";

// ========== 访问口令 ==========
const AUTH_TOKEN_KEY = "xiaoZhi_accessToken";

function getAccessToken() {
    return localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

function setAccessToken(token) {
    if (token) {
        localStorage.setItem(AUTH_TOKEN_KEY, token);
    } else {
        localStorage.removeItem(AUTH_TOKEN_KEY);
    }
}

/**
 * 统一的请求入口：自动带上访问口令，并把 401 转成可识别的错误。
 *
 * 所有业务请求都必须走这里（而不是直接 fetch），否则服务端设了口令后会 401。
 */
async function authFetch(path, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    const token = getAccessToken();
    if (token) headers["X-Access-Token"] = token;

    const response = await fetch(path, Object.assign({}, options, { headers }));

    if (response.status === 401) {
        // 口令失效/未设置：交给上层弹输入框
        const err = new Error("需要访问口令");
        err.code = "AUTH_REQUIRED";
        throw err;
    }
    return response;
}

/**
 * 问后端：这个站要不要口令？本地 token 还有效吗？
 * @returns {Promise<{required: boolean, valid: boolean}>}
 */
async function checkAuthStatus() {
    const resp = await fetch(API_BASE + "/auth/status");
    if (!resp.ok) return { required: false, valid: true };
    const data = await resp.json();
    if (!data.required) return { required: false, valid: true };

    // 需要口令：拿本地 token 试探一个业务接口
    if (!getAccessToken()) return { required: true, valid: false };
    try {
        const probe = await authFetch(API_BASE + "/memory");
        return { required: true, valid: probe.ok };
    } catch (e) {
        return { required: true, valid: false };
    }
}

/**
 * 提交口令换 token
 * @returns {Promise<boolean>} 口令是否正确
 */
async function submitAccessPassword(password) {
    const resp = await fetch(API_BASE + "/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
    });
    if (!resp.ok) return false;
    const data = await resp.json();
    setAccessToken(data.token || "");
    return true;
}

/**
 * 通用 GET 请求
 * @param {string} path — API 路径，如 "/sessions"
 * @returns {Promise<object>} — 解析后的 JSON 响应
 */
async function apiGet(path) {
    const response = await authFetch(API_BASE + path);
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `请求失败: ${response.status}`);
    }
    return response.json();
}

/**
 * 通用 POST 请求
 * @param {string} path — API 路径
 * @param {object} body — 请求体（会被 JSON 序列化）
 * @returns {Promise<object>} — 解析后的 JSON 响应
 */
async function apiPost(path, body = {}) {
    const response = await authFetch(API_BASE + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `请求失败: ${response.status}`);
    }
    return response.json();
}

/**
 * 通用 DELETE 请求
 * @param {string} path — API 路径
 * @returns {Promise<object>} — 解析后的 JSON 响应
 */
async function apiDelete(path) {
    const response = await authFetch(API_BASE + path, { method: "DELETE" });
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `请求失败: ${response.status}`);
    }
    return response.json();
}

// ========== 会话相关 API ==========

/** 创建新会话 */
async function createSession() {
    return apiPost("/sessions", { user_token: getUserToken() });
}

/** 列出我的会话 */
async function listSessions() {
    return apiGet("/sessions?user_token=" + encodeURIComponent(getUserToken()));
}

/** 获取单个会话（含消息历史） */
async function getSession(sessionId) {
    return apiGet(`/sessions/${sessionId}`);
}

/** 删除会话 */
async function deleteSession(sessionId) {
    return apiDelete(`/sessions/${sessionId}`);
}

// ========== 记忆相关 API ==========

/** 列出所有存储的记忆 */
async function listMemories() {
    return apiGet("/memory");
}

/** 删除指定记忆 */
async function deleteMemory(memoryId) {
    return apiDelete(`/memory/${memoryId}`);
}

// ========== 聊天 SSE 流式请求 ==========

/**
 * 发送聊天消息并返回 SSE 事件流
 *
 * 使用 fetch + ReadableStream 手动解析 SSE 事件，
 * 因为浏览器原生的 EventSource 不支持 POST 方法和自定义请求头。
 *
 * @param {string} sessionId — 会话 ID
 * @param {object} body — 请求体：{ content, enable_search, enable_thinking }
 * @param {AbortSignal} [signal] — 传入即可中断生成（「停止」按钮用）
 * @returns {Promise<ReadableStream>} — SSE 事件流
 */
async function sendMessage(sessionId, body, signal) {
    const response = await authFetch(`${API_BASE}/chat/${sessionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal,
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `请求失败: ${response.status}`);
    }

    // 返回 ReadableStream，由调用方逐块读取 SSE 事件
    return response.body;
}
