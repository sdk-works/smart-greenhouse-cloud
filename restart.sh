#!/bin/bash
# 重启所有服务
echo ">>> 重启所有服务..."
sudo systemctl restart postgresql
sudo systemctl restart redis-server
sudo systemctl restart emqx
sudo systemctl restart greenhouse
sudo systemctl restart nginx
echo "✅ 全部重启完成"
echo ""
sudo systemctl status greenhouse --no-pager -l | head -20
