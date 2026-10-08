#!/bin/bash
# ================================================================
# 一键部署脚本（Windows / Git Bash 版） — deploy-win.sh
# ================================================================
# 为什么单独做一个：
#   Windows 的 Git Bash 不自带 rsync，原版 deploy.sh 跑不起来。
#   这里改用 tar 通过 SSH 管道上传（等价效果），并把远程操作合并成
#   尽量少的 SSH 连接，避免反复输密码。
#
# 使用方法（在项目根目录执行）：
#   bash deploy/deploy-win.sh YOUR_SERVER_IP chat.example.com
#
# 安全特性：
#   1. 上传前自动备份服务器上的代码目录（.tgz，可回滚）
#   2. Nginx 配置若被手动改过（如 certbot 的 HTTPS）→ 不覆盖，只备份 + 提示
#   3. 依赖走清华镜像；nginx -t 通过才 reload
#   4. 部署后自动健康检查
# ================================================================

set -e

if [ -z "$1" ]; then
    echo "用法: bash deploy/deploy-win.sh <ECS_IP> [DOMAIN]"
    echo "示例: bash deploy/deploy-win.sh YOUR_SERVER_IP chat.example.com"
    exit 1
fi

ECS_IP="$1"
DOMAIN="${2:-$1}"
SERVER_USER="root"
APP_DIR="/opt/chat-agent"
NGINX_CONF="/etc/nginx/sites-available/chat-agent.conf"
SERVICE_CONF="/etc/systemd/system/chat-agent.service"
TS="$(date +%Y%m%d-%H%M%S)"
SSH="ssh -o StrictHostKeyChecking=accept-new ${SERVER_USER}@${ECS_IP}"
SCP="scp -o StrictHostKeyChecking=accept-new"

echo "========================================"
echo " 小智 AI — 部署到 ${ECS_IP}"
echo " 域名: ${DOMAIN}"
echo " 时间: ${TS}"
echo "========================================"

# ----------------------------------------------------------------
# 0. 连通性检查
# ----------------------------------------------------------------
echo "[0/6] 检查连接..."
$SSH "echo '  已连接: '\$(hostname)"

# ----------------------------------------------------------------
# 1. 备份服务器上的现有代码（可回滚）
# ----------------------------------------------------------------
echo "[1/6] 备份服务器代码..."
$SSH "mkdir -p ${APP_DIR}/backups && tar czf ${APP_DIR}/backups/code-backup-${TS}.tgz -C ${APP_DIR} backend frontend requirements.txt 2>/dev/null || true; echo '  备份目录:'; ls -1t ${APP_DIR}/backups | head -3 | sed 's/^/    /'"

# ----------------------------------------------------------------
# 2. 上传代码（tar over SSH，不依赖 rsync）
# ----------------------------------------------------------------
echo "[2/6] 上传代码（backend / frontend / requirements.txt）..."
tar czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' \
    -C . backend frontend requirements.txt \
    | $SSH "mkdir -p ${APP_DIR} && tar xzf - -C ${APP_DIR} && mkdir -p ${APP_DIR}/frontend/plots && echo '  上传完成'"

# ----------------------------------------------------------------
# 3. 安装 Python 依赖
# ----------------------------------------------------------------
echo "[3/6] 安装/更新依赖..."
$SSH "${APP_DIR}/venv/bin/pip install -q -r ${APP_DIR}/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple" \
    && echo "  依赖已就绪（清华镜像）" \
    || echo "  ⚠️ 依赖安装失败（无新增依赖时可忽略，继续部署）"

# ----------------------------------------------------------------
# 4. systemd 服务（内容没变就不动；变了先备份）
# ----------------------------------------------------------------
echo "[4/6] 检查 systemd 服务配置..."
$SCP -q deploy/chat-agent.service ${SERVER_USER}@${ECS_IP}:/tmp/chat-agent.service.new
$SSH "if [ -f ${SERVICE_CONF} ] && diff -q ${SERVICE_CONF} /tmp/chat-agent.service.new >/dev/null; then echo '  无变化，跳过'; rm -f /tmp/chat-agent.service.new; else if [ -f ${SERVICE_CONF} ]; then cp -f ${SERVICE_CONF} ${SERVICE_CONF}.bak-${TS}; echo \"  已备份旧配置到 ${SERVICE_CONF}.bak-${TS}\"; fi; cp -f /tmp/chat-agent.service.new ${SERVICE_CONF} && rm -f /tmp/chat-agent.service.new && systemctl daemon-reload && systemctl enable chat-agent >/dev/null 2>&1 && echo '  已更新'; fi"

# ---- 检查 unit 指定的运行用户是否存在（缺失会导致服务 217/USER 反复重启）----
# 默认【不自动创建】：只告警并中止，由使用者决定。
# 想自动创建时显式加参数：AUTO_CREATE_USER=1 bash deploy/deploy-win.sh ...
SVC_USER=$($SSH "sed -n 's/^User=//p' ${SERVICE_CONF} 2>/dev/null | head -1" | tr -d '\r')
if [ -n "$SVC_USER" ] && [ "$SVC_USER" != "root" ]; then
    USER_EXISTS=$($SSH "getent passwd ${SVC_USER} >/dev/null && echo yes || echo no" | tr -d '\r')
    if [ "$USER_EXISTS" = "yes" ]; then
        echo "  运行用户 ${SVC_USER} 已存在 ✅"
    elif [ "${AUTO_CREATE_USER:-0}" = "1" ]; then
        echo "  运行用户 ${SVC_USER} 不存在 → 按 AUTO_CREATE_USER=1 创建受限系统账号"
        $SSH "useradd -r -s /bin/false ${SVC_USER} && echo '    已创建（系统账号：不可登录、无 sudo、非管理员）'"
    else
        echo ""
        echo "  ⚠️  已中止：unit 里指定的运行用户 '${SVC_USER}' 在服务器上不存在，服务会启动失败。"
        echo "     该账号是【受限系统账号】：不可登录、无 sudo、不是管理员，仅用于跑本服务。"
        echo "     确认后手动创建："
        echo "       ssh ${SERVER_USER}@${ECS_IP} 'useradd -r -s /bin/false ${SVC_USER}'"
        echo "     或本次部署允许自动创建："
        echo "       AUTO_CREATE_USER=1 bash deploy/deploy-win.sh ${ECS_IP} ${DOMAIN}"
        echo ""
        exit 1
    fi
    $SSH "chown -R ${SVC_USER}:${SVC_USER} ${APP_DIR}/data ${APP_DIR}/tmp-recycle 2>/dev/null; echo '    运行时目录已授权'"
fi

# ----------------------------------------------------------------
# 5. Nginx 配置（安全模式：不覆盖自定义改动）
# ----------------------------------------------------------------
echo "[5/6] 检查 Nginx 配置（安全模式）..."
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

$SCP -q deploy/chat-agent.conf ${SERVER_USER}@${ECS_IP}:/tmp/chat-agent.conf.new
$SSH "sed -i 's/your-domain.com/${DOMAIN}/g' /tmp/chat-agent.conf.new"

NGINX_RESULT=$($SSH "set -e
CONF=${NGINX_CONF}
NEW=/tmp/chat-agent.conf.new
if [ -f \$CONF ]; then
  if diff -q \$CONF \$NEW >/dev/null; then
    rm -f \$NEW
    echo '相同，无需改动'
  else
    cp -f \$CONF \${CONF}.bak-${TS}
    echo \"检测到自定义改动，已备份到 \${CONF}.bak-${TS}，本次不覆盖\"
    echo \"新模板留在服务器: \$NEW（需要时手动 diff 合并）\"
  fi
else
  cp -f \$NEW \$CONF && rm -f \$NEW
  ln -sf \$CONF /etc/nginx/sites-enabled/\$(basename \$CONF)
  echo '首次安装完成'
fi
if nginx -t >/dev/null 2>&1; then systemctl reload nginx; echo '  nginx -t 通过，已重载'; else echo '  ❌ nginx -t 失败（站点仍在使用旧配置，未重载）'; exit 1; fi")
echo "$NGINX_RESULT" | sed 's/^/  /'

# ----------------------------------------------------------------
# 6. 重启服务 + 健康检查
# ----------------------------------------------------------------
echo "[6/6] 重启服务并做健康检查..."
$SSH "systemctl restart chat-agent; sleep 5
echo -n '  服务状态: '; systemctl is-active chat-agent
H=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health)
if [ \"\$H\" = \"200\" ]; then
  echo '  /api/health:              200 ✅'
  echo -n '  /js/vendor/echarts.min.js: '; curl -s -o /dev/null -w '%{http_code} %{size_download} 字节\n' http://127.0.0.1:8000/js/vendor/echarts.min.js
  echo -n '  /js/charts.js:             '; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/js/charts.js
else
  echo \"  ⚠️  /api/health 返回 \$H，服务可能没起来。最近日志：\"
  journalctl -u chat-agent -n 15 --no-pager
fi"

echo ""
echo "========================================"
echo " ✅ 部署流程结束"
echo "========================================"
echo ""
echo "回滚提示："
echo "  代码备份: ${APP_DIR}/backups/code-backup-${TS}.tgz"
echo "  Nginx 备份: ${NGINX_CONF}.bak-${TS}（仅当检测到自定义改动时生成）"
echo "  查看日志: ssh ${SERVER_USER}@${ECS_IP} 'journalctl -u chat-agent -n 50 --no-pager'"
echo ""
echo "浏览器验收（重要）：Ctrl+F5 强刷 → 上传数据文件 → 让它画图 → 刷新页面看图还在不在"
echo ""
if [ "$DOMAIN" != "$ECS_IP" ]; then
    echo "访问地址: https://${DOMAIN}"
fi
