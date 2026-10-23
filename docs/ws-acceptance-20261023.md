# WS 候选 `ac133fa` 运行时验收 · 2026-10-23

> **验证层级：协议级（Protocol-level）**。用 `websockets` 客户端直连实测，**未做浏览器验证**
> （本机无 chromium / playwright，2 核 3GB 环境装不了）。报告里凡涉及浏览器的地方一律写"未验证"。
> 未部署生产、未重启生产服务、未改生产库与 EMQX。

## 1. 被测对象

`/opt/greenhouse-nightly/release-ws` @ `ac133fa`（= 分支 `release/ws-only`），三个提交：

| commit | 内容 |
|---|---|
| `bf0b9c3` | WS 连接强制鉴权 + 实时频道按公司隔离 |
| `145d5fc` | 去掉 WS 路由级 `Depends`（握手前断开浏览器只读到 1006，拿不到关闭码） |
| `ac133fa` | 发布通道公司作用域 + 独立 30s 权限复查（A 方案，**无迁移**） |

## 2. 测试环境

| 项 | 值 |
|---|---|
| 数据库 | `greenhouse_nightly_test`（测试库） |
| Redis | **独立实例 `localhost:6399`**（生产用 `6379/0`，完全隔离；可随时停掉来制造异常） |
| MQTT | `127.0.0.1:1`（不可达守卫，实例日志确认 `Connection refused` 循环，未连生产 EMQX） |
| 端口 | `8011`（起前 `ss -lntp` 确认空闲，生产 8000 未受影响） |
| 复查周期 | `WS_AUTH_RECHECK_SECONDS=2`（生产默认 30，调小只为便于观测） |
| 账号 | 测试库既有 `wstest-a`（公司 A）/ `wstest-b`（公司 B）/ `wstest-noco`（无公司） |

启动要点：`NIGHTLY_READONLY` 必须**显式置 0**，否则 `main.py` 的只读守卫会跳过
`redis_listener()`，第 3 项（频道隔离）根本测不到。这一点已踩到并记录在脚本里。

## 3. 实测结果（5/5 通过）

```
$ /opt/greenhouse/venv/bin/python /opt/greenhouse-nightly/ws_runtime_check.py
WS 运行时验收  base=ws://127.0.0.1:8011  WS_AUTH_RECHECK_SECONDS=2.0
----------------------------------------------------------------------
[PASS] 1. 无效 token -> 4401
      close_code=4401 elapsed=0.00s（期望 4401）
[PASS] 2. 账号无公司 -> 4403
      close_code=4403 elapsed=0.00s（期望 4403）
[PASS] 3. A 公司遥测只推 A，B 收不到
      A 收到='{"type": "telemetry", "device_id": "JY001", "temperature": 25.7, "tag": "isolation-probe"}'
      B 收到=None（B 必须为 None）
      发布频道=telemetry_updates:00eb15e8-487c-4cc3-bb92-be7ce3a795c3
[PASS] 4. 吊销后空闲连接被 4401 断开
      ping/pong='pong' -> 吊销 -> close_code=4401 elapsed=2.00s（复查周期=2.0s，允许上限=5.0s）
[PASS] 5. 复查查询异常 -> fail-closed 4401
      停掉 redis(6399) 制造复查异常 -> close_code=4401 elapsed=2.00s（期望 4401，
      即异常时按失效处理而非保持连接）
----------------------------------------------------------------------
合计 5 项，通过 5，失败 0
```

服务端侧日志（同一时间点，互为佐证）：

```
23:42:35,350 [INFO]    [WS] 拒绝未认证连接
23:42:35,367 [WARNING] [WS] 账号无公司归属，拒绝 user=wstest-noco
23:42:35,371 [INFO]    [WS] 客户端连接 company=00eb15e8-…（A）当前连接数: 1
23:42:35,375 [INFO]    [WS] 客户端连接 company=ceea68f0-…（B）当前连接数: 2
23:42:41,407 [INFO]    [WS] 会话已失效，断开 user=aaaaaaaa-…（吊销后 2.00s）
23:42:43,418 [ERROR]   [WS] 权限复查失败，按失效断开 user=aaaaaaaa-…: Connection closed by server.
23:42:43,419 [INFO]    [WS] 会话已失效，断开 user=aaaaaaaa-…
```

### 3.1 每条的判据与边界

| # | 语义 | 判据 | 边界说明 |
|---|---|---|---|
| 1 | 无效/缺失 token | **先 accept 再 `close(4401)`**，客户端能读到 4401 | 若改为 accept 前关闭，浏览器只会看到 1006，无法区分"token 过期" |
| 2 | 账号无 `company_id` | `close(4403)` | 平台运维账号若没有 `company_id` 也会被 4403 拒，见 §5 风险 2 |
| 3 | 频道隔离 | 向 `telemetry_updates:<A>` 发布，A 收到、**B 4 秒内一条都没收到** | 只证明了"B 收不到 A"；反向（B 收不到 A 的数据）同理由同一份代码对称保证，本次未再单独发一次 B→A |
| 4 | 空闲连接复查 | 吊销后 **2.00s**（= 复查周期）断开，**不依赖客户端发消息** | 生产默认 30s，即最坏 30s 才踢掉；改小会增加 Redis `GET` 频率（每连接每周期 1 次 + 1 次 `EXPIRE` + 1 次 DB 查询） |
| 5 | fail-closed | 复查查询**抛异常**时按失效断开（4401），而不是保持旧权限 | 实测手段是把独立 redis 停掉；生产若 Redis 抖动，所有 WS 连接会被批量踢下线并要求重新登录 |

## 4. 测试（真跑）

```
# 专项（WS 三个文件）
$ pytest -q tests/test_ws_reject_semantics.py tests/test_ws_isolation.py tests/test_ws_session_revocation.py
28 passed in 1.31s

# 全量（release-ws @ ac133fa）
$ pytest -q
95 passed in 2.96s
```

环境：测试库 + 独立 redis 6399 + `MQTT_BROKER_PORT=1` + `NIGHTLY_READONLY=1`。**未放宽/删除任何既有断言。**

## 5. 部署演练（临时副本，未碰 `/opt/greenhouse`）

脚本：`/opt/greenhouse-nightly/deploy_rehearsal.sh`（可复现）。

```
===== 0. 前置核对 =====
生产 HEAD: a01bb33  分支: master        生产服务 /health: 200        端口 8012 空闲
===== 1. 临时副本 =====
git clone --no-local /opt/greenhouse /tmp/deploy-rehearsal → 副本 HEAD: a01bb33
===== 2. merge --no-ff =====
git merge --no-ff origin/release/ws-only -m "rehearsal: 合并 WS 候选"
 7 files changed, 684 insertions(+), 72 deletions(-)   合并后 HEAD: 8ccc7b1
===== 3. 起实例 + /health =====
实例 PID=2371906 ；/health = 200 ；日志含 "[WS] Redis Pub/Sub 监听器已启动（按公司频道分流）"
（注：/openapi.json 里查不到 /api/v1/ws/realtime —— FastAPI 不为 WebSocket 路由生成 OpenAPI schema，属预期）
===== 4. 停实例 =====
端口 8012 占用: 0
===== 5. 回滚 =====
git reset --hard a01bb33 → HEAD: a01bb33，工作区 0 处改动
===== 6. 附加：night/integration → master 冲突预检（不落盘）=====
git merge --no-commit --no-ff origin/night/integration → 退出码 0，"Automatic merge went well"
git merge --abort → HEAD 回到 a01bb33，0 处改动
===== 7. 收尾核对 =====
生产 HEAD: a01bb33，生产服务 /health: 200
```

**结论：命令可用。** `merge --no-ff` 不冲突、`reset --hard a01bb33` 可干净回退、
`night/integration` 合入 `master` 也**不冲突**（这只是 git 层面，不代表迁移与数据兼容）。

## 6. 判断：**可以上线**（协议级通过）

依据：
1. 5 条运行时语义**逐条实测通过**，且服务端日志与客户端关闭码互为佐证；
2. 专项 28 / 全量 95 通过，未放宽断言；
3. **无迁移**（NO_MIGRATION），部署面只有代码；
4. 演练证明 `merge --no-ff` → 起实例 → `/health 200` → `reset --hard a01bb33` 全链路命令可用且可回滚。

**上线前必须确认的两件事（否则会掉线）**：

1. **前端所有 WS 连接都带 `?token=`**。`fe_src/0574_services_api.js:176` 的 `createWebSocket` 已带，
   但**本项目没有可构建前端工程**（见 T9），其它页面是否存在直连 `ws://` 而不走 `createWebSocket`
   **无法在本机核实** —— 这类连接会被 4401 拒绝。上线首日盯日志里 `[WS] 拒绝未认证连接` 的条数。
2. **生产 Redis（6379/0）要扛得住复查流量**。每次成功连接每 30s 产生 1 次 `GET` + 1 次 `EXPIRE`
   + 1 次 `User` 查询。当前生产只有 1 台设备、并发浏览器连接极少，可以接受；
   若以后连接数上百，要把复查改成"按需 + 更长周期"或改成 Redis 里存 user 快照。

**还需补（不影响本次上线判定，但必须记账）**：

| 缺口 | 影响 | 建议 |
|---|---|---|
| **浏览器未验证** | 真实浏览器的关闭码处理、重连逻辑、代理/网关是否透传 WebSocket 都未测 | 上线后首日在浏览器里看一次：F12 → Network → WS → 关闭码；**本次未做** |
| 平台运维账号在 WS 上只看本公司 | 跨公司实时看板做不了 | 需要显式加白名单才放开，不默认放开（沿用 RC-2026-10-22 §3 口径） |
| `4403` 的前端表现未定义 | 无公司账号的用户 WS 被拒后，前端可能反复重连 | 前端收到 4403 应停止重连并提示"账号未关联企业" |
| 前端无 4401 的跳登录处理验证 | token 过期后前端是否跳登录页未知 | 同上，需浏览器验证 |

## 7. 复现步骤

```bash
# 1) 独立 redis（与生产 6379/0 隔离，且用于制造 fail-closed 异常）
redis-server --port 6399 --save '' --appendonly no --daemonize yes \
  --logfile /tmp/redis6399.log --dbfilename ws-test-dump.rdb --dir /tmp

# 2) 播种 token（测试库既有账号）
redis-cli -p 6399 set auth:token:tok-a      aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa
redis-cli -p 6399 set auth:token:tok-b      bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb
redis-cli -p 6399 set auth:token:tok-noco   cccccccc-3333-4333-8333-cccccccccccc

# 3) 起实例（release-ws @ ac133fa，端口 8011，先 ss 确认空闲）
#    注意 NIGHTLY_READONLY=0，否则 redis_listener 不启动
cd /opt/greenhouse-nightly/release-ws && NIGHTLY_READONLY=0 \
  DATABASE_URL=.../greenhouse_nightly_test DATABASE_SYNC_URL=.../greenhouse_nightly_test \
  REDIS_URL=redis://localhost:6399/0 MQTT_BROKER_HOST=127.0.0.1 MQTT_BROKER_PORT=1 \
  WS_AUTH_RECHECK_SECONDS=2 \
  /opt/greenhouse/venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8011 &

# 4) 跑验收
/opt/greenhouse/venv/bin/python /opt/greenhouse-nightly/ws_runtime_check.py

# 5) 部署演练
bash /opt/greenhouse-nightly/deploy_rehearsal.sh

# 6) 收尾：停实例（kill <PID>）、停独立 redis（redis-cli -p 6399 shutdown nosave）
```
