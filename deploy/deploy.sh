#!/bin/bash
# ================================================================
# 一键部署脚本（安全版） — deploy.sh
# ================================================================
# 使用方法：
#   bash deploy/deploy.sh <ecs-ip> [domain]
#
# 示例：
#   bash deploy/deploy.sh 123.45.67.89 chat.example.com
#
# 相比旧版更安全的地方：
#   1. 部署前自动备份服务器上的 Nginx 配置（带时间戳）
#   2. 如果服务器 Nginx 配置已被手动改动（例如 certbot 加了 HTTPS），
#      本次【不会覆盖】，只把新模板放到 /tmp 并提示你手动合并
#      —— 旧版直接 scp 覆盖，会把 certbot 的 HTTPS 改动冲掉
#   3. pip 优先走清华镜像（国内 ECS 更快），失败也不阻断部署
#   4. 部署后自动健康检查：服务状态 / 图表库文件 / charts.js
# ================================================================

set -e

if [ -z "$1" ]; then
    echo "用法: bash deploy/deploy.sh <ECS_IP> [DOMAIN]"
    echo "示例: bash deploy/deploy.sh 123.45.67.89 chat.example.com"
    exit 1
fi

ECS_IP="$1"
DOMAIN="${2:-$1}"
SERVER_USER="root"
APP_DIR="/opt/chat-agent"
NGINX_CONF="/etc/nginx/sites-available/chat-agent.conf"
NGINX_TMP="/tmp/chat-agent.conf.new"
SERVICE_CONF="/etc/systemd/system/chat-agent.service"
SERVICE_TMP="/tmp/chat-agent.service.new"
TS="$(date +%Y%m%d-%H%M%S)"
SSH="ssh -o StrictHostKeyChecking=accept-new ${SERVER_USER}@${ECS_IP}"
SCP="scp -o StrictHostKeyChecking=accept-new"

echo "========================================"
echo " 小智 AI — 部署到 ${ECS_IP}"
echo " 域名: ${DOMAIN}"
echo " 时间: ${TS}"
echo "========================================"

# ----------------------------------------------------------------
# 1. 同步代码
# ----------------------------------------------------------------
echo "[1/6] 同步代码到 ECS..."
rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude 'data/' \
    --exclude 'venv/' \
    --exclude '.venv/' \
    ./backend/ ${SERVER_USER}@${ECS_IP}:${APP_DIR}/backend/

rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    ./frontend/ ${SERVER_USER}@${ECS_IP}:${APP_DIR}/frontend/

rsync -avz requirements.txt ${SERVER_USER}@${ECS_IP}:${APP_DIR}/

# 图表输出目录（服务端绘图开启时才用；提前建好，避免目录缺失）
$SSH "mkdir -p ${APP_DIR}/frontend/plots"

# ----------------------------------------------------------------
# 2. 安装 Python 依赖
# ----------------------------------------------------------------
echo "[2/6] 安装/更新 Python 依赖..."
if $SSH "${APP_DIR}/venv/bin/pip install -q -r ${APP_DIR}/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"; then
    echo "  依赖已就绪（清华镜像）"
else
    echo "  ⚠️ 镜像源安装失败，尝试默认源..."
    if $SSH "${APP_DIR}/venv/bin/pip install -q -r ${APP_DIR}/requirements.txt"; then
        echo "  依赖已就绪（默认源）"
    else
        echo "  ⚠️ 依赖安装失败（多半是网络问题）。若本次没有新增依赖，可以继续。"
    fi
fi

# ----------------------------------------------------------------
# 3. 更新 systemd 服务（内容没变就不动）
# ----------------------------------------------------------------
echo "[3/6] 检查 systemd 服务配置..."
$SCP deploy/chat-agent.service ${SERVER_USER}@${ECS_IP}:${SERVICE_TMP}
if $SSH "test -f ${SERVICE_CONF} && diff -q ${SERVICE_CONF} ${SERVICE_TMP} >/dev/null"; then
    echo "  服务配置无变化，跳过"
    $SSH "rm -f ${SERVICE_TMP}"
else
    $SSH "cp -f ${SERVICE_TMP} ${SERVICE_CONF} && rm -f ${SERVICE_TMP} && systemctl daemon-reload && systemctl enable chat-agent"
    echo "  服务配置已更新"
fi

# ----------------------------------------------------------------
# 4. Nginx 配置（安全模式：不覆盖自定义改动）
# ----------------------------------------------------------------
echo "[4/6] 检查 Nginx 配置（安全模式）..."
# ---- 兼容两种命名：chat-agent.conf / chat-agent（不带后缀）----
NGINX_BASE="/etc/nginx/sites-available/chat-agent"
if $SSH "test -f ${NGINX_BASE}.conf"; then
    NGINX_CONF="${NGINX_BASE}.conf"
elif $SSH "test -f ${NGINX_BASE}"; then
    NGINX_CONF="${NGINX_BASE}"
else
    NGINX_CONF="${NGINX_BASE}.conf"
fi
echo "  目标配置文件: ${NGINX_CONF}"

$SCP deploy/chat-agent.conf ${SERVER_USER}@${ECS_IP}:${NGINX_TMP}
$SSH "sed -i 's/your-domain.com/${DOMAIN}/g' ${NGINX_TMP}"

NGINX_INSTALL=0
if $SSH "test -f ${NGINX_CONF}"; then
    if $SSH "diff -q ${NGINX_CONF} ${NGINX_TMP} >/dev/null"; then
        echo "  服务器配置与模板一致，无需改动"
        $SSH "rm -f ${NGINX_TMP}"
    else
        # 服务器上的配置被改过（例如 certbot 加的 HTTPS / 真实域名），保留它！
        $SSH "cp -f ${NGINX_CONF} ${NGINX_CONF}.bak-${TS}"
        echo "  ⚠️  检测到服务器 Nginx 配置已被自定义"
        echo "      已备份到: ${NGINX_CONF}.bak-${TS}"
        echo "      本次【不覆盖】，新模板放在服务器: ${NGINX_TMP}"
        echo "      如需对比合并，可执行："
        echo "        ssh ${SERVER_USER}@${ECS_IP} 'diff ${NGINX_CONF} ${NGINX_TMP}'"
    fi
else
    echo "  服务器上还没有该配置，执行首次安装"
    NGINX_INSTALL=1
fi

if [ "$NGINX_INSTALL" = "1" ]; then
    $SSH "cp -f ${NGINX_TMP} ${NGINX_CONF} && rm -f ${NGINX_TMP}"
    $SSH "ln -sf ${NGINX_CONF} /etc/nginx/sites-enabled/$(basename ${NGINX_CONF})"
fi

# 配置语法检查通过才重载
if $SSH "nginx -t"; then
    $SSH "systemctl reload nginx"
    echo "  Nginx 已重载"
else
    echo "  ❌ nginx -t 检查失败（站点仍在使用旧配置，未重载）"
    exit 1
fi

# ----------------------------------------------------------------
# 5. 重启应用服务
# ----------------------------------------------------------------
echo "[5/6] 重启应用服务..."
$SSH "systemctl restart chat-agent"
sleep 3
echo -n "  服务状态: "
$SSH "systemctl is-active chat-agent" || true

# ----------------------------------------------------------------
# 6. 健康检查
# ----------------------------------------------------------------
echo "[6/6] 健康检查..."
HEALTH=$($SSH "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health")
echo "  /api/health           → HTTP ${HEALTH}"

ECHARTS=$($SSH "curl -s -o /dev/null -w '%{http_code} %{size_download}' http://127.0.0.1:8000/js/vendor/echarts.min.js")
echo "  /js/vendor/echarts.min.js → ${ECHARTS}（期望 200 且约 1121883 字节）"

CHARTS_JS=$($SSH "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/js/charts.js")
echo "  /js/charts.js         → HTTP ${CHARTS_JS}"

echo ""
echo "========================================"
if [ "$HEALTH" = "200" ]; then
    echo " ✅ 部署完成"
else
    echo " ⚠️  服务未正常响应，请查看日志："
    echo "    ssh ${SERVER_USER}@${ECS_IP} 'journalctl -u chat-agent -n 50 --no-pager'"
fi
echo "========================================"
echo ""
echo "回滚提示："
echo "  Nginx 配置备份: ${NGINX_CONF}.bak-${TS}（如果本次生成了备份）"
echo "  代码回滚: 用 git 切回上一个提交后重新执行本脚本"
echo ""
echo "浏览器验收：打开站点后按 Ctrl+F5 强刷，再上传数据文件生成图表，"
echo "           然后刷新页面看图表是否还在（验证历史图表持久化）。"
echo ""
if [ "$DOMAIN" != "$ECS_IP" ]; then
    echo "访问地址: https://${DOMAIN}"
fi
