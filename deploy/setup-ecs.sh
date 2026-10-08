#!/bin/bash
# ================================================================
# ECS 初始化脚本 — setup-ecs.sh
# ================================================================
# 在全新的阿里云 ECS（Ubuntu 22.04）上运行一次即可
# 使用方法：
#   scp deploy/setup-ecs.sh root@<ecs-ip>:/tmp/
#   ssh root@<ecs-ip> "bash /tmp/setup-ecs.sh"
# ================================================================

set -e  # 任何命令失败则立即退出

echo "========================================"
echo " 小智 AI 聊天助手 — ECS 初始化"
echo "========================================"

# ---------- 1. 更新系统 ----------
echo "[1/6] 更新系统包..."
apt update && apt upgrade -y

# ---------- 2. 安装依赖 ----------
echo "[2/6] 安装 Python、Nginx、certbot..."
apt install -y python3.11 python3.11-venv python3-pip nginx certbot python3-certbot-nginx

# ---------- 3. 创建应用目录和用户 ----------
echo "[3/6] 创建应用目录..."
mkdir -p /opt/chat-agent
mkdir -p /opt/chat-agent/data
mkdir -p /opt/chat-agent/tmp-recycle

# 创建专用用户（安全隔离）
if ! id "chat-agent" &>/dev/null; then
    useradd -r -s /bin/false chat-agent
fi
chown -R chat-agent:chat-agent /opt/chat-agent

# ---------- 4. Python 虚拟环境 ----------
echo "[4/6] 创建 Python 虚拟环境..."
python3.11 -m venv /opt/chat-agent/venv
/opt/chat-agent/venv/bin/pip install --upgrade pip
/opt/chat-agent/venv/bin/pip install fastapi uvicorn openai python-dotenv pydantic-settings aiosqlite

# ---------- 5. 防火墙 ----------
echo "[5/6] 配置防火墙..."
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw --force enable
ufw status verbose

# ---------- 6. 完成 ----------
echo "[6/6] 创建 .env 模板..."
cat > /opt/chat-agent/.env.example << 'EOF'
BAILIAN_API_KEY=sk-your-key-here
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BAILIAN_MODEL=qwen3.7-plus
RECYCLE_BIN_DIR=./tmp-recycle
LOG_LEVEL=INFO
EOF

echo ""
echo "========================================"
echo " ✅ ECS 初始化完成！"
echo "========================================"
echo ""
echo "下一步："
echo "1. 编辑 /opt/chat-agent/.env 填入真实 API Key"
echo "2. 运行部署脚本：bash deploy/deploy.sh <ecs-ip>"
echo ""
