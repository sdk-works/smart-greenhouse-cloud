#!/usr/bin/env bash
# T2 WS 候选运行时验收的**起停辅助**（2026-10-24 夜间补：原来只有 ws_runtime_check.py，
# 怎么起实例全靠手写命令，不可复现）。
#
#   ./ws_runtime_start.sh start   # 起独立 redis(6399) + release-ws 实例(8011)
#   ./ws_runtime_start.sh seed    # 把三条测试 token 写进 redis（被遗忘的一步，见下）
#   ./ws_runtime_start.sh check   # 跑 ws_runtime_check.py（5 条协议级断言）
#   ./ws_runtime_start.sh stop    # 按 pidfile 停，按 PID 校验，不用 pkill -f
#
# 为什么要有 seed：2026-10-24 夜间复跑时，前 3 项全部失败——不是 WS 候选坏了，
# 而是**验收脚本依赖的三条 token 从来没有被固化下来**：它假定 redis 6399 里已经有
# `auth:token:{tok-a,tok-b,tok-noco}` → 测试库里的 wstest-a / wstest-b / wstest-noco
# （后三者种子数据在测试库里是有的）。redis 是每次新起的，key 自然没有了。
# 原先把这一步敲在手边，等于"这次绿过不等于下次能绿"。现在固化成一个子命令。
#
# 守卫：
#   * 端口 8011 / 6399 已被占用就直接失败，不抢端口、不杀已有进程；
#   * 只连测试库 greenhouse_nightly_test，MQTT_BROKER_PORT=1（不可达守卫）；
#   * 不碰 /opt/greenhouse、不碰生产服务（8000）。
set -uo pipefail

BASE=/opt/greenhouse-nightly
SRC=/opt/greenhouse
WS=$BASE/release-ws                 # = ac133fa 发布候选
PORT=${PORT:-8011}
REDIS_PORT=${REDIS_PORT:-6399}
PIDFILE=$BASE/logs/ws-runtime.pids
LOG=$BASE/logs/ws-uvicorn-$PORT.log
RLOG=$BASE/logs/ws-redis-$REDIS_PORT.log

port_busy() { ss -lntp 2>/dev/null | grep -q ":$1 "; }

DSN=$(grep -E '^DATABASE_URL=' "$SRC/.env" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')
SYNC=$(grep -E '^DATABASE_SYNC_URL=' "$SRC/.env" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')

case "${1:-}" in
start)
  port_busy "$PORT" && { echo "端口 $PORT 已被占用，拒绝启动"; exit 1; }
  port_busy "$REDIS_PORT" && { echo "端口 $REDIS_PORT 已被占用，拒绝启动"; exit 1; }
  echo "候选 HEAD: $(git -C "$WS" rev-parse --short HEAD)（期望 ac133fa）"
  cd "$WS" || exit 1
  redis-server --port "$REDIS_PORT" --save '' --daemonize no >>"$RLOG" 2>&1 &
  RP=$!
  NIGHTLY_READONLY=0 \
  DATABASE_URL="${DSN%/*}/greenhouse_nightly_test" \
  DATABASE_SYNC_URL="${SYNC%/*}/greenhouse_nightly_test" \
  REDIS_URL="redis://localhost:$REDIS_PORT/0" \
  MQTT_BROKER_HOST=127.0.0.1 MQTT_BROKER_PORT=1 \
  MQTT_CLIENT_ID=nightly-ws-check \
  WS_AUTH_RECHECK_SECONDS=2 \
  /opt/greenhouse/venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port "$PORT" \
    >"$LOG" 2>&1 &
  UP=$!
  echo "$RP $UP" >"$PIDFILE"
  code=""
  for _ in $(seq 1 40); do
    code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || true)
    [ "$code" = "200" ] && break
    sleep 1
  done
  echo "redis PID=$RP  uvicorn PID=$UP  /health=$code"
  echo "日志：$LOG / $RLOG"
  [ "$code" = "200" ] || { echo "实例未就绪，检查日志"; tail -5 "$LOG"; exit 1; }
  ;;
seed)
  # 三个 user_id 对测试库里既有的种子用户；对不上会退化为 4401（无效 token），
  # 所以这里顺手校验一遍，**别让"没种上"被读成"鉴权通过"**。
  port_busy "$REDIS_PORT" || { echo "redis $REDIS_PORT 没起来"; exit 1; }
  redis-cli -p "$REDIS_PORT" ping >/dev/null || { echo "redis 无响应"; exit 1; }
  redis-cli -p "$REDIS_PORT" set auth:token:tok-a   aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa >/dev/null
  redis-cli -p "$REDIS_PORT" set auth:token:tok-b   bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb >/dev/null
  redis-cli -p "$REDIS_PORT" set auth:token:tok-noco cccccccc-3333-4333-8333-cccccccccccc >/dev/null
  for t in tok-a tok-b tok-noco; do
    echo "$t -> $(redis-cli -p "$REDIS_PORT" get auth:token:$t)"
  done
  ;;
check)
  WS_AUTH_RECHECK_SECONDS=2 /opt/greenhouse/venv/bin/python "$BASE/ws_runtime_check.py"
  ;;
stop)
  [ -f "$PIDFILE" ] || { echo "没有 pidfile：$PIDFILE"; exit 0; }
  read -r RP UP <"$PIDFILE"
  for p in "$UP" "$RP"; do
    if [ -n "${p:-}" ] && kill -0 "$p" 2>/dev/null; then
      cmd=$(tr '\0' ' ' </proc/"$p"/cmdline 2>/dev/null)
      case "$cmd" in
        *uvicorn*|*redis-server*) kill "$p" && echo "已停 PID=$p (${cmd:0:60})" ;;
        *) echo "PID $p 不是本次起的实例（${cmd:0:60}），拒绝杀" ;;
      esac
    else
      echo "PID $p 已退出"
    fi
  done
  rm -f "$PIDFILE"
  sleep 1
  echo "残留：8011=$(ss -lntp 2>/dev/null | grep -c ':$PORT ')  6399=$(ss -lntp 2>/dev/null | grep -c ':$REDIS_PORT ')"
  ;;
*) echo "usage: $0 {start|check|stop}"; exit 2 ;;
esac
