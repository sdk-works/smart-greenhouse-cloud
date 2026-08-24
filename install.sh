#!/bin/bash
# =============================================================
# Smart Greenhouse Platform - 一键安装部署脚本
# 适用环境: Ubuntu 22.04
# 不需要 Docker，直接原生运行
# =============================================================

set -e

INSTALL_DIR="/opt/greenhouse"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$PROJECT_DIR/install.log"

# 颜色输出
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $1" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$LOG_FILE"; }
error()   { echo -e "${RED}[✗]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }
section() { echo -e "\n${BLUE}═══ $1 ═══${NC}" | tee -a "$LOG_FILE"; }

echo "================================================" | tee "$LOG_FILE"
echo "  🌿 Smart Greenhouse Platform 安装脚本"        | tee -a "$LOG_FILE"
echo "  $(date)"                                       | tee -a "$LOG_FILE"
echo "================================================" | tee -a "$LOG_FILE"

# ── 检查 root 权限 ──────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    error "请使用 sudo 运行此脚本: sudo bash install.sh"
fi

# ── 1. 系统依赖 ─────────────────────────────────────
section "1/7  安装系统依赖"
apt-get update -qq 2>&1 | tail -1
apt-get install -y \
    curl wget git build-essential \
    python3 python3-pip python3-venv python3-dev \
    libpq-dev gcc \
    nginx \
    nodejs npm \
    2>&1 | grep -E "^(Setting up|已安装|installed)" | head -20 || true
info "系统依赖安装完成"

# ── 2. PostgreSQL ────────────────────────────────────
section "2/7  安装配置 PostgreSQL"
if ! command -v psql &>/dev/null; then
    apt-get install -y postgresql postgresql-contrib 2>&1 | tail -3
fi
systemctl enable postgresql --quiet
systemctl start postgresql

# 创建数据库和用户
sudo -u postgres psql -c "CREATE USER greenhouse WITH PASSWORD 'SET_LOCALLY_DO_NOT_COMMIT';" 2>/dev/null || \
    warn "用户 greenhouse 已存在，跳过"
sudo -u postgres psql -c "CREATE DATABASE greenhouse OWNER greenhouse;" 2>/dev/null || \
    warn "数据库 greenhouse 已存在，跳过"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE greenhouse TO greenhouse;" 2>/dev/null || true
info "PostgreSQL 配置完成 (用户:greenhouse 密码:SET_LOCALLY_DO_NOT_COMMIT 数据库:greenhouse)"

# ── 3. Redis ─────────────────────────────────────────
section "3/7  安装配置 Redis"
if ! command -v redis-server &>/dev/null; then
    apt-get install -y redis-server 2>&1 | tail -3
fi
systemctl enable redis-server --quiet
systemctl start redis-server
redis-cli ping > /dev/null && info "Redis 运行正常" || warn "Redis 启动异常"

# ── 4. EMQX MQTT Broker ──────────────────────────────
section "4/7  安装配置 EMQX"
if ! command -v emqx &>/dev/null; then
    EMQX_VERSION="5.4.0"
    EMQX_PKG="emqx-${EMQX_VERSION}-ubuntu22.04-amd64.deb"
    EMQX_URL="https://www.emqx.com/zh/downloads/broker/${EMQX_VERSION}/emqx-${EMQX_VERSION}-ubuntu22.04-amd64.deb"

    warn "正在下载 EMQX ${EMQX_VERSION}..."
    if wget -q --show-progress -O "/tmp/$EMQX_PKG" "$EMQX_URL"; then
        dpkg -i "/tmp/$EMQX_PKG" 2>&1 | tail -3
        rm -f "/tmp/$EMQX_PKG"
        info "EMQX 安装完成"
    else
        warn "EMQX 下载失败，尝试备用源..."
        EMQX_URL2="https://github.com/emqx/emqx/releases/download/v${EMQX_VERSION}/emqx-${EMQX_VERSION}-ubuntu22.04-amd64.deb"
        wget -q --show-progress -O "/tmp/$EMQX_PKG" "$EMQX_URL2" || \
            error "EMQX 下载失败，请手动安装: https://www.emqx.com/zh/downloads/broker"
        dpkg -i "/tmp/$EMQX_PKG" 2>&1 | tail -3
        rm -f "/tmp/$EMQX_PKG"
    fi
fi
systemctl enable emqx --quiet
systemctl start emqx || warn "EMQX 启动失败，可手动运行: sudo systemctl start emqx"

# 等待 EMQX HTTP API 就绪，然后创建 MQTT 认证用户
echo -n "    等待 EMQX 就绪 "
for i in $(seq 1 30); do
    if curl -sf http://localhost:18083/api/v5/status > /dev/null 2>&1; then
        echo " ✅"
        break
    fi
    echo -n "."
    sleep 2
done

# 通过 EMQX HTTP API 创建内置数据库认证用户 admin/public
EMQX_API="http://localhost:18083/api/v5"
EMQX_AUTH="admin:greenhouse2024"
# 先启用内置数据库认证
curl -sf -u "$EMQX_AUTH" -X POST "$EMQX_API/authentication" \
    -H "Content-Type: application/json" \
    -d '{"backend":"built_in_database","mechanism":"password_based","password_hash_algorithm":{"name":"bcrypt"},"user_id_type":"username"}' \
    > /dev/null 2>&1 || true
# 创建 MQTT 用户 admin/public
curl -sf -u "$EMQX_AUTH" -X POST "$EMQX_API/authentication/password_based:built_in_database/users" \
    -H "Content-Type: application/json" \
    -d '{"user_id":"admin","password":"public","is_superuser":true}' \
    > /dev/null 2>&1 && info "EMQX MQTT 用户 admin/public 创建成功" || warn "EMQX 用户创建失败，可能已存在"

info "EMQX 配置完成 (MQTT:1883  控制台:18083  admin/greenhouse2024)"

# ── 5. 后端 Python 环境 ──────────────────────────────
section "5/7  配置 Python 后端"
mkdir -p "$INSTALL_DIR/uploads"

# 复制后端代码
cp -r "$PROJECT_DIR/backend/"* "$INSTALL_DIR/" 2>/dev/null || true

# 创建虚拟环境
cd "$INSTALL_DIR"
python3 -m venv venv
source venv/bin/activate

# 配置 pip 国内镜像
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple/ 2>/dev/null || true
pip install --upgrade pip -q
pip install -r requirements.txt -q 2>&1 | tail -5
info "Python 依赖安装完成"

# 创建 .env 配置文件
cat > "$INSTALL_DIR/.env" <<EOF
APP_NAME=Smart Greenhouse Platform
DEBUG=false
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
DATABASE_URL=postgresql+asyncpg://greenhouse:SET_LOCALLY_DO_NOT_COMMIT@localhost:5432/greenhouse
DATABASE_SYNC_URL=postgresql://greenhouse:SET_LOCALLY_DO_NOT_COMMIT@localhost:5432/greenhouse
REDIS_URL=redis://localhost:6379/0
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
MQTT_USERNAME=admin
MQTT_PASSWORD=SET_LOCALLY_DO_NOT_COMMIT
UPLOAD_DIR=$INSTALL_DIR/uploads
UPLOAD_URL_PREFIX=http://localhost:8000/uploads
CORS_ORIGINS=["http://localhost:3000","http://localhost","http://localhost:80"]
EOF
info "后端配置文件已生成"

# ── 6. 前端构建 ──────────────────────────────────────
section "6/7  构建 React 前端"
cd "$PROJECT_DIR/frontend"

# 配置 npm 国内镜像
npm config set registry https://registry.npmmirror.com/ 2>/dev/null || true

info "安装前端依赖（可能需要 3-5 分钟）..."
npm install --silent 2>&1 | tail -3

info "构建前端静态文件..."
REACT_APP_API_URL=/api/v1 npm run build 2>&1 | tail -5

# 复制构建产物
mkdir -p /var/www/greenhouse
cp -r build/* /var/www/greenhouse/
info "前端构建完成"

# ── 7. Nginx 配置 ────────────────────────────────────
section "7/7  配置 Nginx"
cat > /etc/nginx/sites-available/greenhouse <<'NGINX'
server {
    listen 80;
    server_name _;

    # 前端静态文件
    location / {
        root /var/www/greenhouse;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # 后端 API 反向代理
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        client_max_body_size 50M;
    }

    # API 文档
    location ~ ^/(docs|redoc|openapi.json|health) {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    # WebSocket
    location /api/v1/ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }

    # 上传文件
    location /uploads/ {
        proxy_pass http://127.0.0.1:8000/uploads/;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/greenhouse /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
info "Nginx 配置完成"

# ── 创建 systemd 服务 ────────────────────────────────
cat > /etc/systemd/system/greenhouse.service <<EOF
[Unit]
Description=Smart Greenhouse Backend
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=www-data
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$INSTALL_DIR/venv/bin
EnvironmentFile=-$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 设置目录权限
chown -R www-data:www-data "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"

systemctl daemon-reload
systemctl enable greenhouse --quiet
systemctl start greenhouse
sleep 3

# ── 检查服务状态 ─────────────────────────────────────
echo ""
section "检查服务状态"

check_service() {
    local name=$1
    local check=$2
    if eval "$check" > /dev/null 2>&1; then
        info "$name 运行正常 ✅"
    else
        warn "$name 可能未就绪（稍后再试）⚠️"
    fi
}

check_service "PostgreSQL" "pg_isready -U greenhouse -d greenhouse"
check_service "Redis"      "redis-cli ping"
check_service "EMQX"       "systemctl is-active emqx"
check_service "后端 API"   "curl -sf http://localhost:8000/health"
check_service "Nginx"      "curl -sf http://localhost"

LOCAL_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "================================================"
echo -e "  ${GREEN}🎉 安装完成！${NC}"
echo "================================================"
echo ""
echo "  访问地址："
echo "  🌐 平台前端    http://$LOCAL_IP"
echo "  📡 后端 API    http://$LOCAL_IP/docs"
echo "  📊 EMQX 控制台 http://$LOCAL_IP:18083  (admin/greenhouse2024)"
echo ""
echo "  常用命令："
echo "  查看后端日志:  sudo journalctl -u greenhouse -f"
echo "  重启后端:      sudo systemctl restart greenhouse"
echo "  重启所有:      sudo bash $PROJECT_DIR/restart.sh"
echo ""
echo "  日志文件: $LOG_FILE"
echo "================================================"
