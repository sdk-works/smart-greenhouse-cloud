#!/bin/bash
# =============================================================
# Smart Greenhouse - 一键更新脚本
# 应用所有 Bug 修复，无需重新安装
# 用法: sudo bash update.sh
# =============================================================

set -e
INSTALL_DIR="/opt/greenhouse"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
section() { echo -e "\n${BLUE}═══ $1 ═══${NC}"; }

if [ "$EUID" -ne 0 ]; then
    error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
    error "请用 sudo 运行: sudo bash update.sh"
fi

echo "================================================"
echo "  🌿 Smart Greenhouse Platform 更新脚本"
echo "  $(date)"
echo "================================================"

# ── 1. 更新后端代码 ──────────────────────────────────
section "1/6  更新后端代码"
cp -r "$PROJECT_DIR/backend/." "$INSTALL_DIR/"
# 补全缺失的 __init__.py
touch "$INSTALL_DIR/app/__init__.py"
touch "$INSTALL_DIR/app/core/__init__.py"
touch "$INSTALL_DIR/app/models/__init__.py"
touch "$INSTALL_DIR/app/mqtt/__init__.py"
touch "$INSTALL_DIR/app/schemas/__init__.py"
chown -R www-data:www-data "$INSTALL_DIR"
info "后端代码已更新"

# ── 2. 修复 systemd 服务（强制单 Worker）───────────────
section "2/6  修复 systemd 服务配置"
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
ExecStart=$INSTALL_DIR/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --loop asyncio
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
info "systemd 服务已更新（单 Worker 模式）"

# ── 3. 修复 Nginx WebSocket 配置 ─────────────────────
section "3/6  修复 Nginx WebSocket 配置"
cat > /etc/nginx/sites-available/greenhouse <<'NGINX'
server {
    listen 80;
    server_name _;

    location / {
        root /var/www/greenhouse;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        client_max_body_size 50M;
    }

    location ~ ^/(docs|redoc|openapi.json|health) {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    # WebSocket - 必须用 "Upgrade" 大写
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

    location /uploads/ {
        proxy_pass http://127.0.0.1:8000/uploads/;
    }
}
NGINX
nginx -t && systemctl reload nginx
info "Nginx 配置已修复"

# ── 4. 配置 EMQX MQTT 认证 ───────────────────────────
section "4/6  配置 EMQX MQTT 认证用户"
# 等待 EMQX API 就绪
echo -n "    检查 EMQX 状态 "
for i in $(seq 1 20); do
    if curl -sf http://localhost:18083/api/v5/status > /dev/null 2>&1; then
        echo " ✅"
        break
    fi
    echo -n "."
    sleep 2
done

EMQX_API="http://localhost:18083/api/v5"
EMQX_AUTH="admin:greenhouse2024"

# 尝试启用内置数据库认证
curl -sf -u "$EMQX_AUTH" -X POST "$EMQX_API/authentication" \
    -H "Content-Type: application/json" \
    -d '{"backend":"built_in_database","mechanism":"password_based","password_hash_algorithm":{"name":"bcrypt"},"user_id_type":"username"}' \
    > /dev/null 2>&1 || true

# 创建/更新 MQTT 用户
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -u "$EMQX_AUTH" -X POST "$EMQX_API/authentication/password_based:built_in_database/users" \
    -H "Content-Type: application/json" \
    -d '{"user_id":"admin","password":"public","is_superuser":true}' 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "409" ]; then
    info "EMQX MQTT 用户 admin/public 已配置"
else
    # 尝试更新
    curl -sf -u "$EMQX_AUTH" -X PUT "$EMQX_API/authentication/password_based:built_in_database/users/admin" \
        -H "Content-Type: application/json" \
        -d '{"password":"public","is_superuser":true}' > /dev/null 2>&1 || true
    warn "EMQX 用户配置完成（HTTP $HTTP_CODE）"
fi

# ── 5. 重新构建前端 ──────────────────────────────────
section "5/6  重新构建前端"
cd "$PROJECT_DIR/frontend"
npm config set registry https://registry.npmmirror.com/ 2>/dev/null || true

# 只有 package.json 变化时才重装依赖
if [ ! -d node_modules ]; then
    info "安装前端依赖..."
    npm install --silent 2>&1 | tail -3
fi

info "构建前端..."
REACT_APP_API_URL=/api/v1 npm run build 2>&1 | tail -5
cp -r build/* /var/www/greenhouse/
info "前端构建完成"

# ── 6. 重启服务 ──────────────────────────────────────
section "6/6  重启服务"
systemctl restart greenhouse
sleep 3

# 检查启动状态
if systemctl is-active greenhouse > /dev/null 2>&1; then
    info "后端服务启动成功"
else
    echo ""
    warn "后端服务启动异常，查看日志："
    journalctl -u greenhouse -n 30 --no-pager
    exit 1
fi

# 等待 API 就绪
echo -n "    等待 API 就绪 "
for i in $(seq 1 20); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo " ✅"
        break
    fi
    echo -n "."
    sleep 2
done


LOCAL_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "================================================"
echo -e "  ${GREEN}🎉 更新完成！${NC}"
echo "================================================"
echo ""
echo "  访问地址："
echo "  🌐 平台前端    http://$LOCAL_IP"
echo "  📡 API 文档    http://$LOCAL_IP/docs"
echo "  📊 EMQX 控制台 http://$LOCAL_IP:18083"
echo ""
echo "  测试 MQTT 数据链路："
echo "  cd $PROJECT_DIR/backend"
echo "  python3 simulator.py"
echo ""
echo "  查看后端日志："
echo "  sudo journalctl -u greenhouse -f"
echo "================================================"

# ── 修复上传目录权限 ──────────────────────────────────────────
echo ""
echo ">>> 修复上传目录权限..."
mkdir -p /opt/greenhouse/uploads
chown -R www-data:www-data /opt/greenhouse/uploads
chmod -R 755 /opt/greenhouse/uploads
info "上传目录权限已修复"

# ── 安装新增 Python 依赖 ──────────────────────────────────────
echo ""
echo ">>> 检查新增依赖..."
cd "$INSTALL_DIR"
source venv/bin/activate
pip install python-multipart --break-system-packages -q 2>/dev/null || true
info "依赖检查完成"

# ======================= ✅ 自动启动 TCP 9999 图片服务（内置在 update 里）=======================
section "7/6  启动 TCP 图片接收服务 (端口 9999)"

# 复制正确的 tcp 文件
cp -f "$PROJECT_DIR/backend/app/api/tcp_image_server.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/tcp_image_server.py"
chown www-data:www-data "$INSTALL_DIR/tcp_image_server.py"

# 创建系统服务
cat > /etc/systemd/system/greenhouse-tcp.service <<EOF
[Unit]
Description=Smart Greenhouse TCP Image Server
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/tcp_image_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# 启动并开机自启
systemctl daemon-reload
systemctl enable greenhouse-tcp
systemctl restart greenhouse-tcp

sleep 2
if systemctl is-active greenhouse-tcp >/dev/null 2>&1; then
    info "✅ TCP 图片服务已启动 (端口 9999)"
else
    warn "⚠️ TCP 服务启动失败"
fi