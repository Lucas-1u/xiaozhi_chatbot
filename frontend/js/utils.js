/**
 * ================================================================
 * 工具函数模块 — utils.js
 * ================================================================
 * 提供全局通用的工具函数，被其他 JS 模块依赖。
 * 必须最先加载（在 HTML 中排在第一位）。
 */

/**
 * HTML 转义 — 防止 XSS 攻击
 * 将用户输入中的特殊字符转为 HTML 实体，避免注入脚本
 * @param {string} str — 需要转义的字符串
 * @returns {string} — 转义后的安全字符串
 */
function escapeHtml(str) {
    const div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

/**
 * 防抖函数 — 限制高频事件的触发频率
 * 常用于搜索输入框、窗口 resize 等场景
 * @param {Function} fn — 需要防抖的函数
 * @param {number} delay — 延迟毫秒数，默认 300ms
 * @returns {Function} — 包装后的防抖函数
 */
function debounce(fn, delay = 300) {
    let timer = null;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

/**
 * 格式化时间 — 将 ISO 时间戳转为用户友好的显示
 * @param {string} isoString — ISO 8601 格式的时间字符串
 * @returns {string} — 格式化后的时间，如 "14:32" 或 "昨天 14:32"
 */
function formatTime(isoString) {
    const date = new Date(isoString);
    const now = new Date();
    const hours = date.getHours().toString().padStart(2, "0");
    const minutes = date.getMinutes().toString().padStart(2, "0");
    const time = `${hours}:${minutes}`;

    // 判断是否是今天
    if (date.toDateString() === now.toDateString()) {
        return time;
    }

    // 判断是否是昨天
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
        return `昨天 ${time}`;
    }

    // 更早的日期
    const month = (date.getMonth() + 1).toString().padStart(2, "0");
    const day = date.getDate().toString().padStart(2, "0");
    return `${month}-${day} ${time}`;
}

/**
 * 生成唯一 ID — 简单的 UUID v4 替代方案
 * 用于前端临时标识消息、DOM 元素等
 * @returns {string} — 随机 ID，如 "a3f8c2d1"
 */
function generateId() {
    return Date.now().toString(36) + Math.random().toString(36).substr(2, 8);
}

/**
 * 自动调整 textarea 高度 — 根据内容撑高输入框
 * @param {HTMLTextAreaElement} textarea — 目标 textarea 元素
 */
function autoResize(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
}
