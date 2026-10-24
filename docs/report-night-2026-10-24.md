# 夜间开发报告 · 2026-10-24 23:30 ~ 2026-10-25（免费窗口）

> **先说本夜的定位**（避免被读成"昨夜重做一遍"）：
> 本夜收到的任务池（T1~T9 + I）与 **2026-10-23 夜间完全一致**，而这一池在昨夜已全部交付
> （见 `report-night-2026-10-23.md`，14 个提交），白天又追加了 3 个提交做结案。
> 因此本夜**不重写已交付的东西**，做的是三件事：
> ① 把昨夜的验收结论**逐项复跑核实**（不是复读）；② 修掉复跑中暴露的**不可复跑缺陷**；
> ③ 用今天的实时数据刷新证据链与五问答案。
> 报告文件名用 2026-10-24，**没有覆盖昨夜那份**（那是唯一的原始记录，不能抹）。

---

## 0. 环境与守卫（先核对，再动手）

| 项 | 本夜实测 | 说明 |
|---|---|---|
| 生产 | `/opt/greenhouse` `master@a01bb33`，`/health=200`，8000 仅 127.0.0.1 | 全程未 merge / push / restart |
| 工作分支 | `night/night-20261024`（自 `8d923f2` 拉出） | 昨夜分支 `night/night-20261023` 保持不动 |
| WS 候选 | `/opt/greenhouse-nightly/release-ws` @ `ac133fa`，工作区干净 | 未部署 |
| 测试库 | `greenhouse_nightly_test` | 全量测试与 `--apply` 演练都在这里 |
| 实例端口 | 起前 `8011=0 8012=0 6399=0`，收尾 `8011=0 6399=0` | 起实例前必 `ss` 查看，停实例按 PID 校验 |
| MQTT 守卫 | `MQTT_BROKER_HOST=127.0.0.1 PORT=1` | 日志实证 `[Errno 111] Connection refused` |
| 生产写操作 | **0**（本夜未改一行生产数据、未执行任何迁移） | 004 只跑 `--dry-run` |

---

## 1. 总览

| 任务 | 本夜动作 | 状态 |
|---|---|---|
| T1 断流证据链 | 用今日实时数据刷新；**首次**实际运行 `outage_watch.py` / `verify_recovery.py` 于生产只读口径 | **真实环境验证通过**（只读生产核对） |
| T2 WS 候选 `ac133fa` | 复跑协议级 5 项 + 部署演练；**修掉一个导致复跑误报的缺陷** | **模拟联调通过**（浏览器仍未验证） |
| T3 004 归属回填 | 生产只读预演复核（分级数字未变）；专项 6 条测试 | **自动测试通过** + 生产只读核对 |
| T4 悬挂 job 清理 | 复跑 6 条测试；生产仍无 job 表 | **代码完成 / 自动测试通过**（数据不在本机） |
| T5 解析错误分类 | 复跑 10 条测试；**未接线**（待批准） | **代码完成 / 自动测试通过**（未接线） |
| T6 006 列比对 | 生产只读复跑 | **真实环境验证通过**（只读列比对） |
| T7 运维工具 | `dbq.sh` 动词版复核；断流三态与恢复验收脚本首次实跑 | **自动测试通过** |
| T8 上线清单 | 已交付，本夜补一步：今日分支合入 `master` 的冲突预检 | **代码完成** |
| T9 前端接入 | 仍缺可构建工程 | **未完成（阻塞）** |
| I 集成 | 全量 pytest **238 passed**（连跑两次一致） | **自动测试通过** |

---

## 2. 本夜唯一的取舍：发现「验收本身不可复跑」

这是本夜最有价值的产出，不是新功能，是**把昨夜的结论真正立住**。

### 2.1 复现：照昨夜的命令跑，WS 验收误报 3 项失败

```
$ /opt/greenhouse-nightly/ws_runtime_start.sh start
候选 HEAD: ac133fa    /health=200
$ /opt/greenhouse-nightly/ws_runtime_start.sh check
[PASS] 1. 无效 token -> 4401
[FAIL] 2. 账号无公司 -> 4403
      close_code=4401 elapsed=0.00s（期望 4403）
Traceback ... case_isolation ... websockets.exceptions.ConnectionClosedError: received 4401
```

**不是 WS 候选坏了。** 根因：验收脚本假定 redis 6399 里已经有
`auth:token:tok-a / tok-b / tok-noco` 三条 key（指向测试库里的 `wstest-a` / `wstest-b` /
`wstest-noco`），而 redis 是**每次临时起**的，这一步只存在于昨夜当时敲过的命令里，
**没有任何脚本记录它**。于是：

- 「没种上 token」被读成「token 无效」→ 返回 4401 → 断言失败；
- 第 3 项连异常都没机会正常判定。

**这说明了一条**：昨夜那 5/5 的结果是真实的，但**当时不可复跑**。
这种"这次绿过不等于下次能绿"的验收，拿去做上线依据是有风险的。

### 2.2 处置：把漏掉的一步固化成子命令

新增 `scripts/ws_runtime_start.sh seed`（并写入 `scripts/ws_runtime_start.sh` 头部注释）：

```
$ ./ws_runtime_start.sh seed
tok-a   -> aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa
tok-b   -> bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb
tok-noco -> cccccccc-3333-4333-8333-cccccccccccc
```

补齐后重跑，**5/5 全通**（原始输出见 §4）。

> 教训（写给以后）：**验收脚本的依赖状态必须和脚本一起落盘**，
> 依赖"当时环境里碰巧有"的东西，等于把结论寄托在一次手气上。

---

## 3. T1：今日实时刷新（只读）

### 3.1 取证 SQL（不写死日期，可每晚复跑）

```
$ sudo -n -u postgres psql -d greenhouse -P pager=off -f /opt/greenhouse-nightly/t1_refresh_20261024.sql
A.  last_msg_id=JY001/31/922  last_created_at=2026-10-23 08:54:18.978728+08  silent=140191s  viewed_at=2026-10-24 23:50
B.  10-21: 8520 条 / 10-22: 6520 条 / 10-23: 535 条（08:54 止） / 10-24: 0 条
C.  devices: JY001 status=ONLINE last_seen=2026-10-23 08:54:18.980433+08 stale=140191s
D.  boot 31：rows=922 seq 1..922 expected=922 delta=0（无跳号、无重复）
G.  device_receipts 末条 = telemetry 末条 = 2026-10-23 08:54:18.978728（同一时刻，见下）
I.  attribution_status: resolved=11996 / NULL=7053
```

**本夜新确认并值得单独点出来的一条**（昨夜已提到，本夜在多口径下再次印证）：

- **`device_receipts` 与 `telemetry` 的最后一条时间戳完全相同**（`08:54:18.978728`）。
  ACK 回收与遥测入库是同一动作，所以"ACK 是否在 08:54 前后同步停止"这个问题有了明确答案：
  **是，同步停止，且没有"入库了但 ACK 没回"的残留**（未回收上行 = 0）。
- 由此可以排除一类怀疑：不是"云端入库失败导致我们看不到数据"，是**设备侧根本没再上行**。
- `devices.status=ONLINE` 仍是过期值（见 §9 风险 1）。

### 3.2 `outage_watch.py` —— 首次在生产只读口径实跑

```
$ /opt/greenhouse/venv/bin/python scripts/outage_watch.py --db-url "$DATABASE_SYNC_URL" --device JY001 --period 60
设备=JY001  窗口=48h  样本=553 条  周期=60.0s  阈值=120.0s  连续阈值=3 条
最后一条：2026-10-23 08:54:18.978728+08:00
当前状态：计划停机   设备离线=True  数据过期=True  需通知=False
  计划停机：用户 / 主动断电（计划停机，非故障）  2026-10-23T08:54:18+08:00 → 2026-10-26T18:00:00+08:00
  [计划停机] 自 2026-10-23T08:54:18.978728+08:00 起静默 139635.1s（阈值 120.0s）
本次新记 1 条 → /opt/greenhouse-nightly/logs/outage-events.jsonl
```

三态口径按预期工作：**计划停机不计入断流次数、不发通知**，但页面仍应显示"离线"。

> ⚠ **必须让人知道的一条**：窗口 `end=2026-10-26 18:00` 是白天登记时的**临时估计值**，
> 不是约定停机时长。在这之前，**任何真实故障都会被归类成"计划停机"而静默**。
> 设备一旦上电，请按 `docs/recovery-acceptance-20261024.md` §3 把窗口 `end` 改成实际恢复时刻。

### 3.3 `verify_recovery.py` —— 首次在生产只读口径实跑

```
$ /opt/greenhouse/venv/bin/python scripts/verify_recovery.py --db-url "$DATABASE_SYNC_URL" \
    --device JY001 --outage-start '2026-10-23T08:54:18+08:00' --period 60
断电前：boot 30 seq 4  最后一条 2026-10-22T17:29:59.148453+08:00
恢复后：boot 31 seq 1  首条 2026-10-22T17:33:17.892539+08:00  共 922 条
结论：当前未在线（曾恢复过，但现在没有数据）

  [✓] 新 boot_id：boot 30 → 31
  [✓] seq 重新开始：恢复首条 seq=1（正常重启）
  [✓] 恢复后 seq 连续：922 条无跳号、无重复
  [✓] 上传节律：样本 921 段，中位 60.0s（期望 60.0s），落在 45~75s 的占 99.8%
  [✗] 当前在线：最新一条停在 2026-10-23T08:54:18.978728+08:00，至今静默 139626s（阈值 120s）

空白口径：设备断电期间没有采样，这段空白不计入丢包、不推算应达条数、不补数据
  空白时长 139626.1s（38.8h）（断流仍在进行） → 应达 0 条，丢包 0 条，计入完整率=False
```

顺带**证实了脚本对上一次重启（10-22 17:33 boot 30→31）的判定是对的**：
新 boot、seq 归 1、连续无跳号、节律中位 60.0s —— 说明这套判据在真实数据上是可用的，
不是只在造的数据上通过。

### 3.4 EMQX（只读）

```
$ sudo emqx ctl clients list | grep 02501026081300004386
Client(02501026081300004386, username=dev-jy-dtu-000001, peername=36.113.69.129:53542,
       clean_start=true, keepalive=0, ..., delivered_msgs=41, connected=true,
       connected_at=1792714505258)     # = 2026-10-23 08:15:05
```

`connected_at` 仍停留在断流前那次重连时刻 → **39 小时没有新的会话**，佐证"DTU 一次都没重连"。
`keepalive=0` 使 MQTT 层永不判定超时 ⇒ `connected=true` 依然不是存活证据（详见
`docs/outage-triage-20261023.md` §3）。

### 3.5 服务端日志（只读 `journalctl -u greenhouse`）

```
Oct 23 08:54:18 ... [DEV-ACK] ✅ 遥测入库并回 stored device=JY001 msg=JY001/31/922
Oct 23 08:54:48 ... [WARNING] JSON解析失败 topic=agri/device/JY001/up
Oct 23 08:55:18 ... [WARNING] JSON解析失败 topic=agri/device/JY001/up    ← 最后一条上行
Oct 23 12:15:27 ... GET /api/vendor/phpunit/... 404（互联网扫描，与设备无关）
```

断流前后的完整事实链（含 08:14~08:15 那次 47 秒中断与自愈）见
`docs/outage-triage-20261023.md` §1，本夜未改动。

> **`journalctl -u emqx` 查不到任何条目** —— 本夜实测确认该 unit 没有日志输出，
> EMQX 的连接记录只能从 `sudo emqx ctl clients list` 与 `/var/log/emqx/emqx.log.1`
> （已轮转，含 10-17~10-22；10-23 仅 8 条 authz 拒绝记录）取。别再对着 journald 找 EMQX。

---

## 4. T2 WS 候选 `ac133fa`：复跑 + 部署演练

### 4.1 协议级 5 项（`seed` 补齐后）

```
$ ./ws_runtime_start.sh start && ./ws_runtime_start.sh seed && ./ws_runtime_start.sh check
候选 HEAD: ac133fa   redis PID=…   uvicorn PID=…   /health=200
WS 运行时验收  base=ws://127.0.0.1:8011  WS_AUTH_RECHECK_SECONDS=2.0
[PASS] 1. 无效 token -> 4401                      close_code=4401 elapsed=0.00s
[PASS] 2. 账号无公司 -> 4403                      close_code=4403 elapsed=0.00s
[PASS] 3. A 公司遥测只推 A，B 收不到              A 收到='{...tag: isolation-probe}'  B 收到=None
[PASS] 4. 吊销后空闲连接被 4401 断开              ping/pong='pong' -> close_code=4401 elapsed=2.00s
[PASS] 5. 复查查询异常 -> fail-closed 停掉 redis  -> close_code=4401 elapsed=2.00s
合计 5 项，通过 5，失败 0
```

服务端日志同点佐证（`/opt/greenhouse-nightly/logs/ws-uvicorn-8011.log`）：

```
23:44:41,805 [INFO]  [WS] 会话已失效，断开 user=aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa company=00eb15e8-…
23:44:43,815 [ERROR] [WS] 权限复查失败，按失效断开 user=aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa: Connection closed by server.
```

### 4.2 部署演练（临时副本，**未碰 `/opt/greenhouse`**）

```
$ ./deploy_rehearsal.sh
0. 生产 HEAD: a01bb33  分支: master    生产服务: 200   端口 8012 占用: 0
1. 副本 HEAD: a01bb33
2. merge --no-ff release/ws-only(ac133fa) → c02e80e   7 files changed
3. 起实例 PID=… /health = 200
4. 停实例 → 端口 8012 仍占用: 0
5. reset --hard a01bb33 → 回滚后 HEAD: a01bb33，工作区 0 处改动
6. night/integration 合入 master 预检：退出码=0（Automatic merge went well）
7. 生产 HEAD: a01bb33   生产 /health: 200
```

**本夜新增的一步**：把**今天的分支**也做了合入预检（昨夜只预检了 `night/integration`）：

```
$ git merge --no-commit --no-ff origin/night/night-20261023
exit=0
Automatic merge went well; stopped before committing as requested
改动文件数: 109
（随后 merge --abort + reset --hard a01bb33，副本回到干净 a01bb33）
```

> 注：演练里 `curl /openapi.json | grep ws/realtime` **查不到 WS 路由是正常现象** ——
> FastAPI 不把 WebSocket 路由导出到 OpenAPI。**不能用 openapi 判断 WS 是否上线**，
> 判据只能是 §4.1 的运行时实测。

---

## 5. T3 004 归属回填：生产只读复演

```
$ /opt/greenhouse/venv/bin/python scripts/backfill_attribution.py --db-url "$DATABASE_SYNC_URL" --dry-run --sample 3
004 归属回填（DRY-RUN，未写入）
  库=greenhouse  最大允许偏差=300s  limit=不限  only-trusted=否
  范围内行数 = 7053
status     reason                               行数
resolved   backfill_receive_time_estimated        7047
resolved   backfill_sample_time                      4
pending    sample_time_implausible                   2

抽样（前 3 条）：
  'JY001/88/1'  recv=2026-10-17 10:28:52.557026+08:00 sampled=2026-10-17 10:28:52+08:00 tq=1 skew=-0.6s -> resolved/backfill_sample_time
  'JY001/88/2'  recv=2026-10-17 10:28:56.561531+08:00 sampled=2026-10-17 10:28:56+08:00 tq=1 skew=-0.6s -> resolved/backfill_sample_time
  'JY001/88/3'  recv=2026-10-17 10:29:00.566014+08:00 sampled=None tq=0 -> resolved/backfill_receive_time_estimated
DRY-RUN：未写入任何数据。
```

- 与昨夜**完全一致**：4 可信 / 7047 推定 / 2 pending；300s 偏差护栏仍把 2 条 `sampled_at` 错一年的挡住。
- 生产侧交叉核对：`attribution_status` 为 NULL 的行数正是 **7053** = 待回填范围内行数，口径对齐。
- 专项测试 **6 passed**（含 `--apply` 后回滚恢复成 NULL、`--only-trusted` 时推定档一行不动）。

---

## 6. T6 006 迁移前置：生产只读列比对

```
$ /opt/greenhouse/venv/bin/python scripts/check_mqtt_credentials_schema.py --db-url "$DATABASE_SYNC_URL"
库=greenhouse
表存在        : True
现有列        : clientid, created_at, device_id, enabled, note, password_hash, username
缺列          : 无
006 待补列    : ['asset_no', 'compat_identity', 'updated_at', 'disabled_at', 'last_rotated_at',
                 'rotation_count', 'created_by', 'updated_by']
缺索引        : ['ux_mqtt_credentials_device', 'ix_mqtt_credentials_enabled']
凭据行数      : 1   username=dev-jy-dtu-000001 clientid=02501026081300004386 device_id=JY001 enabled=True
阻断问题 0 条：（无，006 可以安全执行）
警告 1 条：device_id 期望 character varying/NOT NULL，实际 character varying/NULL（IF NOT EXISTS 不会修，需单独 ALTER）
```

专项测试 **3 passed**。

---

## 7. I 集成：全量测试真跑

```
$ /opt/greenhouse-nightly/run_tests.sh
238 passed in 57.46s
$ /opt/greenhouse-nightly/run_tests.sh     # 第二次，查稳定性/状态泄漏
238 passed in 57.32s
$ ./run_tests.sh tests/test_backfill_attribution.py tests/test_check_mqtt_credentials_schema.py \
      tests/test_verify_recovery.py tests/test_outage_watch.py tests/test_stuck_job_sweeper.py \
      tests/test_parse_error_classifier.py -v
68 passed in 2.73s
```

| 对照 | 数字 |
|---|---|
| 2026-10-22 夜间 | 166 passed |
| 2026-10-23 夜间 | 203 passed |
| **本夜（含白天 3 个提交）** | **238 passed**，**失败 0、跳过 0，连跑两次一致** |

环境：测试库 `greenhouse_nightly_test` + redis 6379/15 + `MQTT_BROKER_PORT=1` + `NIGHTLY_READONLY=1`。
**没有删过任何测试、没有放宽过任何既有断言。**

---

## 8. 提交号

| 分支 | commit | 内容 |
|---|---|---|
| `night/night-20261024`（本夜） | `9fe9385` | 验收可复跑化：`scripts/ws_runtime_start.sh`（+seed）、`scripts/night_verify_all.sh`、`scripts/t1_refresh_20261024.sql`、`docs/recovery-acceptance-20261024.md` |
| `night/night-20261023`（昨夜继承，未改动） | `8d923f2` / `64f8ee8` / `7aadc9a`（白天） | 恢复验收 / 断流三态 / T1 结案 |
| 昨夜 T1~T9+I | `48b67a6` `aecb744` `5454325` `60fb454` `b2c7447` `1cf2f09` `a9a2f01` `472470f` `9ade9c1` `14d37c0` | 见昨夜报告 |

**未 push**。

本夜新增/修改的文件（同时放在 `/opt/greenhouse-nightly/` 便于直接执行）：

```
/opt/greenhouse-nightly/ws_runtime_start.sh       # 起停 + seed（新增 seed）
/opt/greenhouse-nightly/night_verify_all.sh       # 一条龙验收（新增）
/opt/greenhouse-nightly/t1_refresh_20261024.sql   # 不写死日期的取证 SQL（新增）
/opt/greenhouse-nightly/logs/night-verify-2026-10-24.log        # 全套原始输出
/opt/greenhouse-nightly/logs/ws-runtime-check-20261024-night.txt
/opt/greenhouse-nightly/logs/deploy-rehearsal-20261024-night.txt
/opt/greenhouse-nightly/logs/verify-recovery-20261024-night.txt
/opt/greenhouse-nightly/logs/outage-watch-20261024-night.txt
/opt/greenhouse-nightly/logs/backfill-attribution-20261024-night.txt
/opt/greenhouse-nightly/logs/check-mqtt-credentials-20261024-night.txt
```

---

## 9. 失败项 / 跳过项 / 未覆盖风险

### 9.1 失败项

**全量 0 失败。** 本夜出现的失败全部是**验收脚本自身的环境依赖缺陷**（§2），已修并复跑通过；
没有任何一处是"改断言让它变绿"。

### 9.2 跳过项

无 skip（238 条全跑）。

### 9.3 未覆盖风险（如实）

1. **`devices.status=ONLINE` 仍是过期值**：已 39 小时无数据，页面仍会显示"在线"。
   候选实现（按 `last_seen` 判定 + 「MQTT 已连接 / 有数据」分两栏）在夜间分支的 T4 状态接口里，**未上生产**。
2. **WS 浏览器未验证**：本机无 chromium / playwright；5/5 是协议级。上线首日必须人工看一次
   F12 → WS 关闭码与重连行为。
3. **前端是否所有 WS 都带 `?token=` 无法核实**：本机无前端工程（T9 同因）。
4. **004 仍一行未写**：7,047 条"推定"是否写 `resolved` 未拍板；迁移 SQL 缺 300s 护栏，建议走脚本。
5. **`www.usr.cn` 未根治**：分类（007）是候选且**未接线**；历史报文没留存，无法回溯分类。
6. **EMQX authn 未启用**：任意人可匿名连接、可自称任意 clientid。
7. **7 个悬挂 job 无法在本机处置**：数据属 141 内网智农AI，本机无对应表。
8. **没有断流告警**：`outage_watch.py` 只落本地 JSONL，**未挂 cron、不告警到人**。
9. **维护窗口掩盖真实故障**：`mw-20261024142159-JY001` 的 `end=2026-10-26 18:00` 是临时值，
   到期之前任何故障都会被记成"计划停机"。
10. **`metric_definitions` 只有 8 条种子**；**`devices` 表没有 `company_id`**（设备挪棚无独立依据）。

---

## 10. 待决策问题

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | 7,047 行"推定"是否写 `resolved`？ | 写后页面当可信展示 | 写 `resolved` + 保留 reason，展示层标"推定"（T4 已实现，未上线） |
| 2 | 004 走脚本还是迁移 SQL？ | 迁移 SQL 缺 300s 护栏 | 走脚本（有护栏 + manifest + 回滚） |
| 3 | WS 能否上线？ | 不带 token 的 WS 会被 4401 拒 | 可以；**先确认前端所有 WS 都带 `?token=`**（本机无法核实） |
| 4 | 007 解析分类要不要接线？ | 不接则解析错误永远只有总数 | 建议接，先 `persist_raw=False`；根治仍在 DTU 侧 |
| 5 | 断流要不要告警、发给谁？ | 本次是人工发现（虽然是计划停机） | 建议 `outage_watch.py` 挂 cron 每 10 分钟；通道另议 |
| 6 | EMQX authn 何时启用？ | 不启用可冒充；启用则未改凭据的设备集体离线 | 先固件改造 + 一台试点，放到最后 |
| 7 | **维护窗口 `end` 要不要现在就改成合理值？** | 现在是 10-26 18:00 的估计值，期间真实故障静默 | 设备一上电就按实际关窗；若预计长时间停机，宁可把窗口改短，让它自然升级为"超期未恢复"提醒 |
| 8 | `devices` 加不加 `company_id`？ | 设备归属只能经大棚推导 | 列入下一轮 |
| 9 | 前端仓库路径 / Tab 位置？ | 不接则 T4 只在 `/pages` 能看 | 拿到路径后按 `docs/frontend-integration-20261023.md` 一次接对 |

---

## 11. 明早必须能回答的五个问题

### Q1 设备为什么在 08:54 停？网络侧 / 固件侧 / 供电侧分别有什么证据？

**性质已由用户白天确认：主动断电（计划停机），不是设备故障。** 取证只能给出观测边界：

| 侧 | 证据 | 结论 |
|---|---|---|
| 云端(共同) | 末条遥测 `08:54:18.989`；末条 `www.usr.cn` 注册包 `08:55:18.736`；此后两个本该到达的时刻（`08:55:19` 遥测、`08:55:48` 注册包）**都没到**；`device_receipts` 末条与遥测同为 `08:54:18.978728`（ACK 同步停止、未回收上行 = 0） | 「没有数据」不是入库失败，是**设备没上行** |
| 网络侧 | 08:15:05 有一次运营商重拨 / IP 变更，且 08:14:18~08:15:05 的 47 秒中断是**DTU 自己恢复并补传 seq 882**；本次 39 小时**零重连**（EMQX `connected_at` 停留在 08:15:05） | 单纯链路问题应至少重连一次 → 不支持"只是网络闪断" |
| 固件侧 | 最后 15 条 `uptime_ms` 严格 +60000ms（MCU 没卡）；但 MCU 挂死**解释不了 DTU 注册包为何同时停** | 单独不成立 |
| 供电侧 | MCU 与 DTU 在 ~0.3s 窗口内同时消失；掉电不发 FIN → EMQX 一直 `connected` | 与"断电"吻合 |
| **结论** | **云端永远观测不到"人为断电"这个原因**。上述哪一种可能的排序表**今后不再使用**（`docs/outage-triage-20261023.md` §0.2 已作废原 §4 的排序），改为：恢复后按 `verify_recovery.py` 验 boot / seq / 节律 | 恢复后首条 `JY001/32/1`（新 boot、seq 归 1）= 设备重启过；若仍是 `JY001/31/923` = MCU 没重启，需复查 |

（`表Filing` 为笔误，原文应为"上述三种可能的排序表**不再使用**"。）

### Q2 WS 能不能上线？依据？浏览器验证补了吗？

**可以上线（协议级）**，依据三条：① `ac133fa` 运行时 5/5 且服务端日志同点佐证；
② 全量 238 passed，未放宽断言；③ 部署演练证明 `merge --no-ff → /health 200 → reset --hard a01bb33` 可用可回滚，
且今天把**当前整条夜间分支**也做了合入预检（109 文件，无冲突）。
**浏览器验证没补** —— 本机无 chromium / playwright，2 核 3GB 装不了；这是明确未验证项。
另有一个本夜新发现的前提：**昨夜的 5/5 当时不可复跑**（漏了 redis token 播种），已修，今天结果**重新成立**。

### Q3 004 现在能不能执行？分级数字、抽样证据、回滚演练结果？

**技术上可以，但仍建议先拍板口径。** 本夜生产只读复演：**4 可信 / 7047 推定 / 2 pending**（与昨夜一致，未变）。
抽样：可信 4 条偏差 **−0.6s**；pending 2 条为 `JY001/99/1`、`JY001/99/2`，`sampled_at` 错一年，被 300s 护栏挡下。
生产交叉核对：`attribution_status IS NULL` = **7053** = 待回填行数，口径自洽。
回滚演练：测试库 `--apply` 写 3 行 → 三档落库 → 执行自动生成的回滚 SQL → 全部恢复（含 `binding_id`），专项 6 条通过。
**生产一行没写。**

### Q4 7 个悬挂 job 什么原因，清完还会不会再有？

**回答不了 —— 数据不在本机。** 本机生产库无 `job` / `turn` / `judge` 表，那 7 条属 141 内网智农AI；
工具（`scripts/stuck_job_sweeper.py`，三条口径 + dry-run 默认 + 不误伤正常 queued）专项 6 条通过，
拿到表名列名即可跑。**会不会再生**：只清数据不修机制一定再生（终态矛盾说明"上游完成即拉起"这条路漏了；
孤儿说明上下游不同事务或有丢写）——但这条**本机无法验证**。

### Q5 `www.usr.cn` 那 1753 条解析错误，分类后各是多少？

**回答不了 —— 报文没有留存，无法回溯分类。** 被拒的一条都没落 `raw_messages`（只有 `stored`），
服务日志也不记 payload。能给的：journal 全量累计 **6078** 条（任务书里的 1753 是 10-22 当时的累计值）；
10-23 断流后为 0。四类分类能力已就绪（`scripts/` + 007 迁移 + 只读统计，10 条测试通过），
但**未接线**，接上之后才开始按类累计。

---

## 12. 上线建议（沿用昨夜分批，本夜无新增变更）

| 批次 | 内容 | 批准 | 窗口 | 风险 |
|---|---|---|---|---|
| 1 | 备份 → 迁移 003/004/005 → WS 候选 `ac133fa` | 需批准 | 迁移需窗口 | 中（004 改 7,053 行语义） |
| 2 | 迁移 006 + T3 入网闭环（**不含 authn**） | 需批准 | — | 低 |
| 3 | T4 状态页（顺带解决 `devices.status` 过期的展示误导） | 需批准 | — | 低 |
| 4 | 004 数据回填（走脚本，先 `--only-trusted` 再放量） | 需批准 | 需窗口 | 中 |
| 5 | 007 解析错误分类（接线需单独批准） | 需批准 | — | 低 |
| 6 | **EMQX authn 启用** | 需批准 | 设备侧配合 | **高** |

完整命令见 `docs/release-runbook-20261023.md`。**本夜没有任何东西上生产。**

---

## 13. 明早立刻可做的两件事

```bash
# ① 一条命令复跑本夜全部验收（生产只读 + 测试库跑测试，约 5 分钟）
/opt/greenhouse-nightly/night_verify_all.sh

# ② 设备重新上电后，按清单验收（boot / seq / 节律 / 空白口径）
#    见 docs/recovery-acceptance-20261024.md；验收完记得把维护窗口 end 改成实际恢复时刻
```
