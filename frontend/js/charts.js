/**
 * ================================================================
 * 图表渲染模块 — charts.js
 * ================================================================
 * 负责把后端 plot_chart 工具下发的 ECharts 配置渲染成可交互图表。
 *
 * 与原来「服务器画好图片」的区别：
 * - 后端只给配置（JSON），画图发生在这里（用户浏览器）
 * - 图表可交互：悬停看数值、点图例筛选、可导出 PNG
 * - 不占用服务器算力，缩放不糊，中文显示正常
 *
 * 依赖：js/vendor/echarts.min.js（本地内置，不走 CDN）
 */

// 主色与后端保持一致的备选色板（后端 option 里已带 color，这里只做兜底）
const CHART_FALLBACK_COLORS = [
    "#4F46E5", "#10B981", "#F59E0B", "#EF4444",
    "#06B6D4", "#8B5CF6", "#EC4899", "#84CC16",
];

// 已渲染的图表实例（用于窗口缩放时统一 resize）
const chartInstances = [];


/**
 * 在聊天区插入一张可交互图表
 * @param {Object} spec 后端下发的图表配置 { type, title, option }
 */
function appendChart(spec) {
    if (!spec || !spec.option) {
        console.warn("图表配置为空，跳过渲染:", spec);
        return;
    }

    // ---- 外层气泡 ----
    const wrapper = document.createElement("div");
    wrapper.className = "message assistant chart-message";

    const box = document.createElement("div");
    box.className = "chart-box";

    // ---- 顶部工具条：标题 + 导出按钮 ----
    const toolbar = document.createElement("div");
    toolbar.className = "chart-toolbar";

    const titleEl = document.createElement("span");
    titleEl.className = "chart-title";
    titleEl.textContent = spec.title || "图表";

    const exportBtn = document.createElement("button");
    exportBtn.className = "chart-btn";
    exportBtn.type = "button";
    exportBtn.textContent = "⬇ 导出 PNG";

    toolbar.appendChild(titleEl);
    toolbar.appendChild(exportBtn);
    box.appendChild(toolbar);

    // ---- 画布容器 ----
    const canvas = document.createElement("div");
    canvas.className = "chart-canvas";
    box.appendChild(canvas);

    wrapper.appendChild(box);
    messageList.appendChild(wrapper);
    messageList.scrollTop = messageList.scrollHeight;

    // ---- 渲染 ----
    if (typeof echarts === "undefined") {
        canvas.innerHTML =
            '<div class="chart-error">⚠️ 图表库未加载（js/vendor/echarts.min.js），请检查静态资源</div>';
        console.error("echarts 未定义，无法渲染图表");
        return;
    }

    let chart = null;
    try {
        chart = echarts.init(canvas, null, { renderer: "canvas" });

        const option = Object.assign({}, spec.option);
        // 兜底：主题色 / 背景
        if (!option.color) option.color = CHART_FALLBACK_COLORS;
        option.backgroundColor = "#ffffff";

        chart.setOption(option);
        chartInstances.push(chart);
    } catch (e) {
        console.error("图表渲染失败:", e);
        canvas.innerHTML =
            '<div class="chart-error">⚠️ 图表渲染失败：' + escapeHtml(e.message) + "</div>";
        return;
    }

    // ---- 导出 PNG ----
    exportBtn.addEventListener("click", function () {
        try {
            const url = chart.getDataURL({ pixelRatio: 2, backgroundColor: "#ffffff" });
            const a = document.createElement("a");
            a.href = url;
            a.download = (spec.title || "chart") + ".png";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } catch (e) {
            console.error("导出失败:", e);
            alert("导出失败：" + e.message);
        }
    });

    return chart;
}


/**
 * 窗口尺寸变化时，让所有图表跟着自适应
 * （用防抖避免拖动窗口时疯狂重绘）
 */
(function initChartResize() {
    let timer = null;
    window.addEventListener("resize", function () {
        if (timer) clearTimeout(timer);
        timer = setTimeout(function () {
            chartInstances.forEach(function (c) {
                try { c.resize(); } catch (e) { /* 实例可能已销毁，忽略 */ }
            });
        }, 150);
    });
})();
