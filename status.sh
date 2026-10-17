#!/bin/bash
# 查看所有服务状态
echo "================================================"
echo "  Smart Greenhouse Platform - 服务状态"
echo "================================================"

check() {
    local name=$1
    local cmd=$2
    if eval "$cmd" > /dev/null 2>&1; then
        echo "  ✅ $name"
    else
        echo "  ❌ $name （未运行）"
    fi
}

check "PostgreSQL"  "pg_isready -U greenhouse"
check "Redis"       "redis-cli ping"
check "EMQX"        "systemctl is-active emqx"
check "后端 API"    "curl -sf http://localhost:8000/health"
check "Nginx"       "curl -sf http://localhost"

echo ""
LOCAL_IP=$(hostname -I | awk '{print $1}')
echo "  🌐 前端:   http://$LOCAL_IP"
echo "  📡 API文档: http://$LOCAL_IP/docs"
echo "  📊 EMQX:   http://$LOCAL_IP:18083"
echo ""
echo "  查看后端日志: sudo journalctl -u greenhouse -f"
