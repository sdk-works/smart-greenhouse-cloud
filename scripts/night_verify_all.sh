#!/usr/bin/env bash
# 夜间验收一键复跑（2026-10-24 夜间补）
#
# 为什么要有这个脚本：之前的验收散在若干条手写命令里（psql -f、python3 scripts/*.py、
# 先起实例再跑检查…），**命令本身没有被固化**，于是第二天复跑时会漏步骤
# （2026-10-24 当晚复跑 WS 验收就因为漏了"往 redis 种三条 token"这一步而假失败 3 项）。
# 本脚本把**本泊 6 项验收**串成一条命令，全部只读生产 + 测试库跑测试。
#
#   ./night_verify_all.sh             # 跑全套，输出落 logs/night-verify-<日期>.log
#   ./night_verify_all.sh --no-pytest # 跳过全量 pytest（已经在别处跑过时）
#
# 硬约束（脚本本身保证）：
#   * 生产侧**只有 SELECT**（各个脚本内部均无 DML/DDL，已人工核对）；
#   * 测试实例只用测试库 greenhouse_nightly_test，MQTT_BROKER_PORT=1（不可达守卫）；
#   * 起实例前先看端口，停实例按 PID 校验，不用 pkill -f。
set -uo pipefail

BASE=/opt/greenhouse-nightly
SRC=/opt/greenhouse
WT=$BASE/worktree
OUT=$BASE/logs/night-verify-$(date +%F).log
RUN_PYTEST=1
[ "${1:-}" = "--no-pytest" ] && RUN_PYTEST=0

DSN=$(grep -E '^DATABASE_URL=' "$SRC/.env" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')
SYNC=$(grep -E '^DATABASE_SYNC_URL=' "$SRC/.env" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')

hdr() { echo; echo "########## $* ##########"; }

{
  echo "夜间验收一键复跑 $(date -Is)  主机=$(hostname)  生产 HEAD=$(git -C "$SRC" rev-parse --short HEAD)"
  echo "候选 HEAD(release-ws)=$(git -C "$BASE/release-ws" rev-parse --short HEAD)  worktree HEAD=$(git -C "$WT" rev-parse --short HEAD)"

  hdr "0. 生产未被动过的前置核对"
  echo "生产分支: $(git -C "$SRC" rev-parse --abbrev-ref HEAD) @ $(git -C "$SRC" rev-parse --short HEAD)"
  echo "生产 /health: $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health)"
  echo "端口占用 8011=$(ss -lntp 2>/dev/null | grep -c ':8011 ') 8012=$(ss -lntp 2>/dev/null | grep -c ':8012 ') 6399=$(ss -lntp 2>/dev/null | grep -c ':6399 ')（起实例前必须全为 0）"

  hdr "1. T1 断流取证（生产只读 SQL）"
  # SQL 文件随日期走；找不到就退回 2026-10-24 那份（内容按 now() 相对取值，每日可复用）
  T1SQL=$BASE/t1_refresh_$(date +%Y%m%d).sql
  [ -f "$T1SQL" ] || T1SQL=$BASE/t1_refresh_20261024.sql
  timeout 120 sudo -n -u postgres psql -d greenhouse -P pager=off -f "$T1SQL" 2>&1

  hdr "2. T1 断流三态（outage_watch，生产只读 + 本地 jsonl 落盘）"
  (cd "$WT" && timeout 180 /opt/greenhouse/venv/bin/python scripts/outage_watch.py --db-url "$SYNC" --device JY001 --period 60 2>&1)

  hdr "3. T1 恢复验收（verify_recovery，生产只读）"
  (cd "$WT" && timeout 180 /opt/greenhouse/venv/bin/python scripts/verify_recovery.py --db-url "$SYNC" --device JY001 \
      --outage-start '2026-10-23T08:54:18+08:00' --period 60 2>&1)

  hdr "4. T3 004 归属分级（生产只读 dry-run，一行不写）"
  (cd "$WT" && timeout 300 /opt/greenhouse/venv/bin/python scripts/backfill_attribution.py --db-url "$SYNC" --dry-run --sample 3 2>&1)

  hdr "5. T6 006 迁移前置列比对（生产只读）"
  (cd "$WT" && timeout 120 /opt/greenhouse/venv/bin/python scripts/check_mqtt_credentials_schema.py --db-url "$SYNC" 2>&1)

  hdr "6. T2 WS 候选 ac133fa 运行时验收（测试库 + 独立 redis，起完即停）"
  if timeout 300 "$BASE/ws_runtime_start.sh" start 2>&1; then
    "$BASE/ws_runtime_start.sh" seed 2>&1
    timeout 300 "$BASE/ws_runtime_start.sh" check 2>&1
    "$BASE/ws_runtime_start.sh" stop 2>&1
  else
    echo "起实例失败（端口占用？），跳过 WS 运行时验收"
  fi

  if [ "$RUN_PYTEST" = "1" ]; then
    hdr "7. I 全量 pytest（测试库）"
    timeout 900 "$BASE/run_tests.sh" 2>&1 | tail -6
  fi

  hdr "收尾核对：生产仍未被动过"
  echo "生产分支: $(git -C "$SRC" rev-parse --abbrev-ref HEAD) @ $(git -C "$SRC" rev-parse --short HEAD)"
  echo "生产 /health: $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health)"
  echo "残留端口 8011=$(ss -lntp 2>/dev/null | grep -c ':8011 ') 6399=$(ss -lntp 2>/dev/null | grep -c ':6399 ')"
} 2>&1 | tee "$OUT"

echo
echo "完整输出：$OUT"
