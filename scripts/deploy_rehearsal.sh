#!/usr/bin/env bash
# T2 部署演练：在【临时副本】上模拟 merge --no-ff → 起实例 → /health 校验 → reset --hard 回滚。
# 全程不碰 /opt/greenhouse（只从它 clone），不碰生产服务，只用测试库。
set -uo pipefail

SRC=/opt/greenhouse
BASTE_REV=a01bb33
TMP=${TMP_DIR:-/tmp/deploy-rehearsal}
PORT=${PORT:-8012}
LOG=/opt/greenhouse-nightly/logs/deploy-rehearsal.log

step() { echo; echo "===== $* ====="; }

step "0. 前置核对（生产未被动过）"
echo "生产 HEAD: $(git -C $SRC rev-parse --short HEAD)  分支: $(git -C $SRC rev-parse --abbrev-ref HEAD)"
echo "生产服务: $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health) (/health)"
echo "端口 $PORT 占用情况: $(ss -lntp 2>/dev/null | grep -c ":$PORT ")"

step "1. 临时副本（git clone，只读源）"
rm -rf "$TMP"
git clone -q --no-local "$SRC" "$TMP" 2>>"$LOG" || { echo "clone 失败"; exit 1; }
cd "$TMP" || exit 1
echo "副本 HEAD: $(git rev-parse --short HEAD)（应为 $BASTE_REV）"

step "2. merge --no-ff 发布候选（release/ws-only = ac133fa）"
git merge --no-ff origin/release/ws-only -m "rehearsal: 合并 WS 候选" 2>&1 | tail -5
echo "合并后 HEAD: $(git rev-parse --short HEAD)"
git log --oneline -1

step "3. 起实例（测试库 + 独立 redis）并校验 /health"
DSN=$(grep -E '^DATABASE_URL=' $SRC/.env | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')
SYNC=$(grep -E '^DATABASE_SYNC_URL=' $SRC/.env | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')
NIGHTLY_READONLY=0 \
DATABASE_URL="${DSN%/*}/greenhouse_nightly_test" \
DATABASE_SYNC_URL="${SYNC%/*}/greenhouse_nightly_test" \
REDIS_URL=redis://localhost:6399/0 \
MQTT_BROKER_HOST=127.0.0.1 MQTT_BROKER_PORT=1 \
MQTT_CLIENT_ID=nightly-rehearsal \
nohup /opt/greenhouse/venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port "$PORT" \
  > /opt/greenhouse-nightly/logs/rehearsal-uvicorn.log 2>&1 &
PID=$!
echo "实例 PID=$PID"
for i in $(seq 1 30); do
  code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || true)
  [ "$code" = "200" ] && break
  sleep 1
done
echo "/health = $code"
echo "--- WS 路由运行时校验（真实握手）---"
# 修于 2026-10-25 夜间：原来这一步是
#   curl openapi.json | grep -o '"/api/v1/ws/realtime"'
# 而 **FastAPI 的 OpenAPI 里不含 WebSocket 路由**（docs/openapi.yaml 里也搜不到 /ws/），
# 所以这条 grep 永远匹配不到、永远输出空行，脚本又不因它失败 —— 一个从不生效的检查。
# 换成真实握手：不带 token 连 /api/v1/ws/realtime，期望服务端 accept 后 close(4401)。
# 该分支在 token 为空时直接返回 None，**不查 redis**，因此不依赖 6399 是否起过。
WSBASE=ws://127.0.0.1:$PORT /opt/greenhouse/venv/bin/python /opt/greenhouse-nightly/ws_route_probe.py "$PORT"
RC_WS=$?
echo "WS 路由握手退出码=$RC_WS（0=已挂载并按 4401 拒绝未认证连接）"
echo "--- 实例日志 ---"
tail -5 /opt/greenhouse-nightly/logs/rehearsal-uvicorn.log

step "4. 停实例"
kill "$PID" 2>/dev/null; sleep 2
echo "端口 $PORT 仍占用: $(ss -lntp 2>/dev/null | grep -c ":$PORT ")"

step "5. 回滚：reset --hard $BASTE_REV"
git reset --hard "$BASTE_REV" 2>&1 | tail -2
echo "回滚后 HEAD: $(git rev-parse --short HEAD)"
echo "工作区是否干净: $(git status --porcelain | wc -l) 处改动"
git log --oneline -1

step "6. 附加：night/integration 合入 master 的冲突预检（不落盘）"
git merge --no-commit --no-ff origin/night/integration >/tmp/merge-dryrun.txt 2>&1
rc=$?
echo "merge --no-commit 退出码=$rc"
tail -5 /tmp/merge-dryrun.txt
git merge --abort 2>/dev/null || git reset --hard "$BASTE_REV" >/dev/null 2>&1
echo "预检后 HEAD: $(git rev-parse --short HEAD)，改动: $(git status --porcelain | wc -l) 处"

step "7. 收尾核对（生产仍未被动过）"
echo "生产 HEAD: $(git -C $SRC rev-parse --short HEAD)"
echo "生产服务 /health: $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health)"
echo "临时副本留在 $TMP（可自行 rm -rf）"
