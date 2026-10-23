# 夜间开发报告 · 2026-10-23 23:30 ~ 2026-10-24（免费窗口）

> 范围：**只做温室云端这一摊**。智农AI（141 内网 transport / judge / 语音统一链路）
> 依赖 GPU 与内网环境，不在本机、不在本任务池，本次**未接触**。
>
> 硬约束自检（逐条）：
> - 生产未动：`systemctl is-active greenhouse` = **active**，`master@a01bb33` 未移动、
>   **未 push**、未改 `.env` / nginx / EMQX 配置、未 restart/stop 任何服务、**未写生产库一行**。
> - **未向设备下发任何指令**。
> - 开发与测试只在 `/opt/greenhouse-nightly/worktree` + `greenhouse_nightly_test`
>   + 独立 redis(6399) + `MQTT_BROKER_HOST=127.0.0.1 PORT=1`（不可达守卫，日志确认为 `Connection refused`）。
> - 004 在生产**一行都没写**（只跑 `--dry-run`）。
> - 测试实例与独立 redis 用完即停，`ss` 复核 8011/8012/6399 均已释放。
>
> 状态词（每项只能选一个）：`未完成` / `代码完成` / `自动测试通过` / `模拟联调通过` /
> `真实环境验证通过`。**「真实环境验证通过」本报告只用于只读生产核对**（查库、读日志、
> 只读订阅、`ss` 读 TCP 计数、只读跑脚本），不含任何写操作或部署。

---

## 0. 总览

| 任务 | 状态 | 一句话 | 提交 |
|---|---|---|---|
| **T1** 断流证据链 + 基线 | **真实环境验证通过**（只读） | 08:55 停传证据链完整；`keepalive=0` 导致 `connected=true` 不能当存活证据；倾向**供电中断** | `48b67a6` |
| **T2** WS 候选运行时验收 + 部署演练 | **模拟联调通过** | 协议级 **5/5** 通过；专项 28 / 全量 95 passed；演练 `merge --no-ff`→`/health 200`→`reset --hard` 全通 | `aecb744` |
| **T3** 004 归属回填脚本化 + 护栏 | **自动测试通过** | 4 可信 / 7047 推定 / 2 pending（生产只读复现）；新增 300s 偏差护栏；回滚演练收敛 | `5454325` |
| **T4** 悬挂 job 清理工具 | **自动测试通过** | 三条口径 + dry-run 默认 + 不误伤正常 queued；**本机生产库无 job 表，7 条悬挂不在本机** | `60fb454` |
| **T5** `www.usr.cn` 解析错误分类 | **自动测试通过** | 四类分类 + 聚合表（候选，**未接线**）；历史 1753 条**无法回溯分类**（报文没留存） | `b2c7447` |
| **T6** 006 / T3 入网补缺 | **自动测试通过** | 006 已是 `IF NOT EXISTS`；补**上线前列比对**脚本（生产实测阻断问题 0 条） | `1cf2f09` |
| **T7** 运维工具与观测修正 | **自动测试通过** | `dbq.sh` 不再误伤 `created_at`；断流事件记录；`--reconcile` 补"重复"与显式 `reread` | `a9a2f01` |
| **T8** 上线清单 + 回滚手册 | **代码完成** | 7 步逐步前置/执行/验证/回滚/影响面，标注 🅐需批准 与 🅦需窗口 | `472470f` |
| **T9** T4 前端接入说明 | **未完成（阻塞）** | 阻塞：缺可构建前端工程（`fe_src` 只是反编译 chunk） | `9ade9c1` |
| **I** 集成 | **自动测试通过** | 全量 **203 passed**（上次 166，新增 37，失败 0） | 本分支全部 |

**测试**：`203 passed in 57.65s`（全量，测试库 + 独立 redis，`MQTT_BROKER_PORT=1`，真跑）。
**生产全程 active，`/health` 200。**

---

## 1. 分支与提交

分支 `night/night-20261023`（自 `night/integration` `b5b8324` 拉出），**未 push**，
`master` 仍是 `a01bb33`。

```
48b67a6 docs(T1): JY001 08:55 断流证据链 + 三种可能判据对照表（只读取证）
aecb744 test(T2): WS 候选 ac133fa 运行时验收（协议级 5/5）+ 部署演练脚本
5454325 feat(T3): 004 归属回填可执行脚本 + 300s 偏差护栏 + 回滚演练
60fb454 feat(T4): 通用悬挂 job 清理工具（三条口径 + dry-run 默认 + 不误伤正常 queued）
b2c7447 feat(T5): 上行报文解析错误四类分类（候选，未接线）+ 007 迁移 + 只读统计
1cf2f09 feat(T6): 006 上线前列比对脚本（mqtt_credentials）+ 自测
a9a2f01 fix(T7): dbq.sh 改按语句动词判断 + 断流事件记录 + 对账补重复/显式 reread
472470f docs(T8): 上线清单 + 回滚手册（合并版）
9ade9c1 docs(T9): T4 前端接入说明（阻塞：缺可构建前端工程）
```

配套（在 `/opt/greenhouse-nightly`，不进 git，均已留 `.bak`）：
`dbq.sh`（新版，源码已同步进 git）、`observer.py.bak-20261023-0210`、
`t1_outage_20261023.sql`、`ws_runtime_check.py`、`deploy_rehearsal.sh`。

---

## 2. T1 断流证据链（真实环境只读）

详见 `docs/outage-triage-20261023.md`。核心事实：

| 项 | 实测 |
|---|---|
| 最后一条遥测 | `2026-10-23 08:54:18.978728`，`JY001/31/922`，温 25.7 / 湿 40.8 / CO₂ 709 / 光 1120，`uptime_ms=55320456`，`valid=[1]*8` |
| 最后一条 DTU 注册包 `www.usr.cn` | `08:55:18.736`（**比遥测晚 60 秒**） |
| 之后 | 遥测 0 条、ACK 0 条、垃圾包 0 条 —— **静默 14 小时 40 分**（截至 23:31 为 877 分钟） |
| 今日节奏 | 00–07 点各 60 条/小时；08 点 55 条（08:54 止） |
| seq 连续性 | boot 31 今日 388–922 共 535 条，**缺口 0、重复 0** |
| 设备自报 `uptime_ms` | 最后 15 条**逐条恰好 +60000ms**（墙钟却有 55.2s / 64.8s 抖动）→ MCU 时钟正常、没卡、没变慢 |
| ACK 回收 | 与遥测逐条对应，08 点 55 条，末条 08:54:18.978728；**未回收上行 = 0** |
| TCP 层（`ss -tni`） | `Send-Q=0`、`bytes_sent=3703 = bytes_acked=3703`；`lastsnd=08:54:19`、`lastrcv=08:55:18`；`rtt=209.9ms rto=472ms` |
| EMQX | `connected=true, keepalive=0, delivered_msgs=41, connected_at=08:15:05` |
| `devices.last_seen` | `08:54:18.98`，`status=ONLINE` —— **已过期 14.6 小时，页面会显示"在线"** |

### 2.1 `keepalive=0` 为什么造出"假在线"

1. MQTT keepalive 是**客户端声明**的心跳周期；填 `0` = 不启用心跳。EMQX 不发 `PINGREQ`、
   也不期望收到，**永远不会**因空闲判定超时。
2. 于是只剩 TCP 层。而 TCP **只有在有数据要发时**才会发现对端消失。08:54:19 云端发完
   最后一条 ACK 且被对端 ACK（`bytes_acked == bytes_sent`），此后**没有任何要发的东西**
   （ACK 只在收到上行时才产生），也就没有写操作会失败。
3. 结果：`connected=true` 只代表"EMQX 没收到过 FIN/RST"。设备掉电时 TCP 来不及发 FIN，
   连接就会像这样一直挂着。**它不是存活证据。**

### 2.2 三种可能 × 判据

| 假设 | 支持 | 反对 | 现场判据 | 倾向 |
|---|---|---|---|---|
| **A 供电中断** | MCU 与 DTU 在 **08:55:19 前后同时消失**（垃圾包 08:55:18.7，遥测 08:55:19 未到，窗口 0.3s）；掉电不发 FIN；**14.6h 零重连** | 无直接反证 | DTU **PWR 灯灭**；恢复后 `boot_id` 变 32、seq 从 1 开始 | **最符合** |
| **B 网络侧中断** | 08:15 那次就是运营商重拨（IP `117.61.232.100`→`36.113.69.129`）；RTT 210ms 链路一般 | **08:14 那次 47 秒中断是 DTU 自己恢复的**（重连 + 补传 seq 882）；若是纯链路问题，14.6h 里**至少该重连一次**，实际 0 次 | PWR 亮、NET 灯灭；云端出现新 `connected_at` 但连不上 | 可能，但不能单独解释 0 重连 |
| **C 固件侧 MCU 挂死** | `uptime_ms` 严格 +60000ms，符合"正常发完一条后突然不再发" | **解释不了 DTU 注册包也停了**：MCU 死了 DTU 若还通电，30s 的 `www.usr.cn` 应持续 | MCU 灯仍闪、DTU 在线灯仍亮；恢复后 **boot 仍 31、seq 从 923 续** | 单独不成立 |

**一锤定音的判据**（设备恢复后看第一条 `msg_id`）：`JY001/32/1` → 重启过 → A；
`JY001/31/923` → MCU 没重启 → B 或 C。命令：
`psql -d greenhouse -At -c "select msg_id, created_at from telemetry order by created_at desc limit 3;"`

---

## 3. T2 WS 候选 `ac133fa`（模拟联调通过）

详见 `docs/ws-acceptance-20261023.md`。

```
$ /opt/greenhouse/venv/bin/python /opt/greenhouse-nightly/ws_runtime_check.py
WS 运行时验收  base=ws://127.0.0.1:8011  WS_AUTH_RECHECK_SECONDS=2.0
[PASS] 1. 无效 token -> 4401        close_code=4401 elapsed=0.00s
[PASS] 2. 账号无公司 -> 4403        close_code=4403 elapsed=0.00s
[PASS] 3. A 公司遥测只推 A，B 收不到  A 收到='{...tag: isolation-probe}'  B 收到=None
[PASS] 4. 吊销后空闲连接被 4401 断开  ping/pong='pong' -> 吊销 -> close_code=4401 elapsed=2.00s
[PASS] 5. 复查查询异常 -> fail-closed 停掉 redis(6399) -> close_code=4401 elapsed=2.00s
合计 5 项，通过 5，失败 0
```

服务端日志同时间点互为佐证（"拒绝未认证连接" / "账号无公司归属，拒绝 user=wstest-noco" /
"会话已失效，断开" / "权限复查失败，按失效断开 … Connection closed by server"）。

- 测试：专项 **28 passed**、全量 **95 passed**（`release-ws @ ac133fa`）。
- 部署演练（临时副本 `/tmp/deploy-rehearsal`，**未碰 `/opt/greenhouse`**）：
  `merge --no-ff` → 起实例 → `/health 200` → `reset --hard a01bb33`（工作区 0 改动）→ 生产 HEAD 仍 `a01bb33`。
  附带预检：`night/integration` 合入 `master` **不冲突**。
- **边界：协议级验证，浏览器未验证**（本机无 chromium / playwright）。上线首日必须人工看一次
  F12 → Network → WS 的关闭码。
- **判断：可以上线**，前提是确认前端所有 WS 都带 `?token=`（本机无前端工程，无法核实）。

> 踩到的坑：起实例必须显式 `NIGHTLY_READONLY=0`，否则 `main.py` 的只读守卫跳过
> `redis_listener()`，第 3 项根本测不到。已写进脚本注释。

---

## 4. T3 004 归属回填（自动测试通过）

详见 `docs/attribution-backfill-20261023.md`。

生产**只读**预演（一行没写）：

```
$ python3 scripts/backfill_attribution.py --db-url "$DATABASE_SYNC_URL" --dry-run --sample 3
库=greenhouse  最大允许偏差=300s  范围内行数 = 7053
resolved   backfill_receive_time_estimated        7047
resolved   backfill_sample_time                      4
pending    sample_time_implausible                   2
```

护栏命中的 2 条：`JY001/99/1`、`JY001/99/2`，`sampled_at=2025-10-17 11:00:00`，
**偏差 −365 天**，`time_quality=1`。可信的 4 条偏差 **−0.6s**。
⇒ `time_quality=1` 只说明设备自称有时钟，**不能证明时钟正确**。

- 新增护栏：`|sampled_at − timestamp| > 300s → pending/sample_time_implausible`，**不得进可信**。
- 脚本：`--dry-run`（默认）/ `--limit` / `--only-trusted` / `--apply` / `--json` / `--max-skew`；
  `--apply` 对非测试库**拒绝**（退出码 3）；写库落 manifest + 自动生成回滚 SQL。
- 测试 **6 passed**：七场景对齐、护栏可调（阈值 400 天时"错一年"放行，证明是护栏在起作用）、
  `--only-trusted` 只剩可信、`--apply` 后收敛到 0 + 回滚恢复成 NULL、`--only-trusted` 时推定档一行不动。
- **差异**：现行 `004_data_reliability_up.sql` **还没有这条护栏**，文档里给了补法（§6），
  本次**未改**该文件（口径变更需人拍板）。建议走脚本。

---

## 5. T4 悬挂 job 清理工具（自动测试通过）

详见 `docs/stuck-job-sweeper-20261023.md`。

**先说结论：本机生产库里没有这套数据。**

```
$ python3 scripts/stuck_job_sweeper.py --db-url "$DATABASE_SYNC_URL" \
    --table job_judges --ref-table job_turns --dry-run --json
{"error": "表 job_judges 不存在（库=greenhouse）——无法盘点，请确认表名与所在库"}   exit=4
```

证据：生产库 28 张表（只读列出）无任何 `job`/`turn`/`judge`/`queue` 表；
`grep -rn "judge" app/` 与 `grep -rn "\bturn\b" app/` **零命中**；
Redis 生产库 `dbsize=0`（db0~5、15 全空）；另一套 `jingyang_web_db.translation_jobs` 有表但 **0 行**。
⇒ 那 7 条（5 条终态矛盾 + 2 条孤儿）属于 **141 内网智农AI**，不在本机/本任务池，**无法盘点、无法清理**。

交付（通用，表/列全部可配）：三条口径 `orphan` / `terminal_conflict`（上游已终态、下游仍 queued）/
`timeout`；三条都额外要求 job 自身未进终态，且终态矛盾只在"上游已终态"时成立 ——
**上游还在跑/还没开始的 queued 是正常排队，一条都不碰**。

测试 **6 passed**：5 条命中 / 3 条正常的不碰、`--dry-run` 不写、`--apply` 后收敛到 0、
`--limit` 限量、表不存在明确报错（**不**静默返回 0）、非测试库拒绝。

---

## 6. T5 `www.usr.cn` 解析错误分类（自动测试通过）

详见 `docs/parse-error-classification-20261023.md`。

**现状（生产只读）**：`raw_messages` 只有 `stored` **11994 行，一条 `rejected` 都没有** ——
解析失败的报文从未落库。服务日志只记"JSON解析失败 + topic"，**不记 payload**。

| 口径 | 条数 |
|---|---|
| journal 全量（自 2026-10-17 11:55:22） | **6078** |
| 2026-10-22 17:00:38 起 | 1903 |
| 2026-10-23 当天（到 08:55 断流为止） | 1066 |
| 断流后 | **0** |

交付：四类分类（`duplicate_registration` / `non_json` / `device_mismatch` / `missing_field`）、
报文指纹、`parse_error_stats` 聚合表（避免每 30s 一条垃圾撑表）、`007` 迁移（全 `IF NOT EXISTS`）、
只读统计 SQL。测试 **10 passed**。**未改 `mqtt_service.py`，生产解析行为不变**；接线 diff 写在文档里，需人批准。

---

## 7. T6 006 / T3 入网补缺（自动测试通过）

详见 `docs/device-onboarding-gap-20261023.md`。

核对结果：`006_device_onboarding_up.sql` 已是 `CREATE TABLE IF NOT EXISTS` +
全 `ADD COLUMN IF NOT EXISTS` + `CREATE UNIQUE INDEX IF NOT EXISTS`，down 只删自己加的列不 DROP 表
—— **主体无需重写**。缺的是"上线前的列比对"，本次补上 `scripts/check_mqtt_credentials_schema.py`。

生产实测（只读）：

```
表存在: True   缺列: 无
006 待补列: asset_no, compat_identity, updated_at, disabled_at, last_rotated_at,
            rotation_count, created_by, updated_by
缺索引: ux_mqtt_credentials_device, ix_mqtt_credentials_enabled
凭据行数: 1  (dev-jy-dtu-000001 / 02501026081300004386 / JY001 / enabled=True / 设备对得上=False)
阻断问题 0 条：（无，006 可以安全执行）
警告 1 条：device_id 生产为 NULL-able（IF NOT EXISTS 不会修，需单独 ALTER）
```

端到端：`tests/test_device_onboarding.py` + `tests/test_integration_t3_t4_t5.py` **7 passed**；
`scripts/sim_device_check.py` **14/14**（进程内 broker，**不连生产 EMQX**）。
自测 **3 passed**。

---

## 8. T7 运维工具与观测修正（自动测试通过）

详见 `docs/ops-tools-20261023.md`。

1. **`dbq.sh` 误伤已修**：子串黑名单 → **语句动词**判断（首句或 `;` 之后，动词后跟空白）。
   ```
   旧: dbq.sh "select created_at from telemetry ..."     -> REJECTED: mutating keyword detected
   新: dbq.sh "select created_at from telemetry ..."     -> 正常返回
   新: dbq.sh "update telemetry set id='x'"              -> REJECTED
   新: dbq.sh "select 1; drop table telemetry"           -> REJECTED（多语句注入）
   ```
   测试 **4 passed**（两头都钉住：含 `created_at`/`updated_at` 必须放行；写操作与注入必须拒）。
2. **断流事件记录** `scripts/outage_watch.py`：连续 N 条间隔 > 2×周期 → 记一次事件；
   另判"进行中"（`now − 最后一条 > 2×周期`，不必等够 N 条 —— 正是本次 08:55 的情况）。
   **只落本地 JSONL，不告警到人**。生产只读实测：
   ```
   设备=JY001 窗口=48h 样本=6989 条 周期=60.0s 阈值=120.0s 连续阈值=3 条
   最后一条：2026-10-23 08:54:18.978728+08:00
     [进行中] 自 2026-10-23T08:54:18.978728+08:00 起静默 54999.2s（阈值 120.0s）
   ```
   测试 **8 passed**。**未挂 cron**（改生产 crontab 需人批准）。
3. **`--reconcile` 补稳定指标**：JSON 增加 `duplicate` 与**显式** `reread`，
   查不出时 `duplicate=-1`（`-1 ≠ 0`，避免把"没查"当"没有重复"）：
   ```
   组数 30 / 应有 19049 条 / 缺 0 条 / 重复 0 条 / 主游标重读 0（应为 0）
   {"ts":1792771950,"groups":30,"expected":19049,"missing":0,"duplicate":0,"reread":0,
    "self_check":{"reread_is_zero":true,"duplicate_resolved":true}}
   ```
   （`observer.py` 已备份 `observer.py.bak-20261023-0210`。）

---

## 9. T8 / T9

- **T8 上线清单 + 回滚手册**：`docs/release-runbook-20261023.md`，7 步
  （备份 → 003/004/005 → WS → 006/T3 → 004 回填 → 007 可选 → **authn 最高风险**），
  每步给出前置检查 / 执行 / 验证 / 回滚 / 影响面，并标 🅐需用户批准 与 🅦需停机-低峰窗口。
  状态**代码完成**（文档类交付，无可执行测试）。
- **T9 前端接入说明**：**未完成（阻塞：缺可构建前端工程）**。`fe_src` 只是反编译 chunk，
  本次**不接入、不构建、不做浏览器验证**。已交付接入点（`GreenhouseWorkbench` 的 `Tabs items`，
  现有 key `overview/config/records/profile`，`greenhouseId` 来自 `useParams()`）、
  props 契约、逐字段的响应契约与 `anomalies.code` 枚举。

---

## 10. 全量测试（真跑）

```
$ /opt/greenhouse-nightly/run_tests.sh
203 passed in 57.65s
```

| 对照 | 数字 |
|---|---|
| 上次（2026-10-22 夜间） | 166 passed |
| 本次 | **203 passed**，**失败 0、跳过 0** |
| 新增 37 条 | T3 回填 6 + T4 清理 6 + T5 分类 10 + T6 列比对 3 + T7 dbq 守卫 4 + T7 断流 8 |

环境：测试库 `greenhouse_nightly_test` + 独立 redis 6399 + `MQTT_BROKER_PORT=1` +
`NIGHTLY_READONLY=1`。**没有删过测试、没有放宽过任何既有断言**。

---

## 11. 失败项 / 跳过项 / 未覆盖风险

### 11.1 失败项

**无**（最终全量 0 失败）。过程中出现过 6 次失败，全部是新代码自身的缺陷，已逐条修掉，
**没有靠改断言绕过去**：

| 失败 | 根因 | 处置 |
|---|---|---|
| 回填测试 `AttributeError: 'tuple' object has no attribute 'company'` | `iso` fixture 返回 `(fx, db)` 元组 | 改为 `fx, db = iso` |
| `set_session cannot be used inside a transaction` | dry-run 已开事务再切只读 | 只在 dry-run 开只读会话，`--apply` 走普通会话 |
| 回滚断言 `dict()` 解 3 列报 length 3; 2 is required | 三列结果不能直接 `dict()` | 显式按 `r[0]: (r[1], r[2])` 组装 |
| `psql` 以 ubuntu 身份连库 `role "ubuntu" does not exist` | 测试里直接调 psql | 改用 psycopg2 执行回滚 SQL |
| 清理后仍剩 2 条悬挂 | orphan 口径没有限制 job 自身状态，`failed` 的历史行也被算成悬挂 | 三条口径统一加 `job.status ∈ (pending + running)` |
| 断流测试看不到缺口 | 测试造数据只设了 `timestamp`，`created_at` 是 `server_default now()` | 显式给 `created_at` |

### 11.2 跳过项

**无 skip**（203 条全跑）。`paho` 与 `websockets` 本机都有，相关链路未被跳过。

### 11.3 未覆盖风险

1. **WS 浏览器未验证**（本机无 chromium/playwright）——协议级 5/5 不等于浏览器行为一致；
   首日必须人工看一次关闭码与重连逻辑。
2. **T9 前端未接入**（缺可构建工程），T4 组件目前只能在 `/pages/greenhouse_status.html` 看。
3. **004 未执行**，且现行迁移 SQL 与脚本口径不一致（缺护栏）；7,047 行"推定"定性未决。
4. **EMQX authn 未启用**：任何人可匿名连接、可自称任意 clientid（ACL 挡不住主动冒充）。
5. **`www.usr.cn` 未根治**：分类是候选且未接线，历史报文没留存，6078 条无法回溯分类。
6. **7 条悬挂 job 无法在本机处置**：数据属 141 内网智农AI，工具虽备好但没有表结构可跑。
7. **`devices.status=ONLINE` 是过期值**：`last_seen=08:54:18` 却已 14.6h 无数据，页面会显示"在线"。
8. **断流没有告警**：`outage_watch.py` 只落本地 JSONL，**未挂 cron、不告警到人**。
9. **`metric_definitions` 只有 8 条种子**：设备上报的 `soil_nitrogen` 等若无定义不会展示。
10. **`devices` 表没有 `company_id`**：设备归属只能经大棚推导，设备挪棚时无独立依据。

---

## 12. 待决策问题

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | 7,047 行"推定"是否直接写 `resolved`？ | 写后页面会当可信展示 | 写 `resolved` 但保留 reason，展示层显示"推定"角标（T4 已实现，未上线） |
| 2 | 004 走脚本还是走迁移 SQL？ | 迁移 SQL 缺 300s 护栏 | 走脚本（有护栏 + manifest + 回滚）；走迁移须先补文档 §6 那段 |
| 3 | WS 能否上线？ | 不带 token 的 WS 会被 4401 拒 | 可以上线；**先确认前端所有 WS 都带 `?token=`**（本机无法核实） |
| 4 | 007 解析分类要不要接线？ | 不接则解析错误永远只是个总数 | 建议接，且先 `persist_raw=False` 只聚合；根治仍在 DTU 侧关注册包转发 |
| 5 | 断流要不要告警、发给谁？ | 本次断流是人工发现的 | 建议先挂 cron 每 10 分钟跑 `outage_watch.py`，告警通道另议 |
| 6 | EMQX authn 何时启用？ | 不启用：任意人可冒充设备；启用：设备未改凭据会集体离线 | 必须先完成设备固件凭据改造并**先一台试点**，最后一步做 |
| 7 | `devices` 要不要加 `company_id`？ | 现在设备归属只能经大棚推导 | 列入下一轮 |
| 8 | 前端仓库路径 / Tab 位置？ | 不接则 T4 只在 `/pages` 能看 | 拿到路径后按 `docs/frontend-integration-20261023.md` 一次接对 |

---

## 13. 明早必须能回答的五个问题

### Q1 设备为什么在 08:54 停？网络侧 / 固件侧 / 供电侧分别有什么证据？

- **遥测**末条 `08:54:18.989`（`JY001/31/922`）；**DTU 注册包**末条 `08:55:18.736`；
  下一条遥测（08:55:19）与下一条注册包（08:55:48）**都没到**，此后 14h40m 全静默。
- **网络侧**：08:15:05 有过一次运营商重拨（IP 从 `117.61.232.100` 变 `36.113.69.129`），
  且 08:14:18~08:15:05 的 47 秒中断是**DTU 自己恢复的**并重传了 seq 882 —— 说明它具备
  分钟级自愈能力。**反对纯网络闪断**：14.6 小时一次都没重连。
- **固件侧**：最后 15 条 `uptime_ms` 严格 +60000ms，MCU 没卡没变慢；但**单独 MCU 挂死解释不了
  DTU 注册包也停了**。
- **供电侧**：MCU 与 DTU 在 **08:55:19 前后同时消失**（0.3s 窗口），掉电不发 FIN 所以 EMQX
  一直 `connected`，**没电自然不会重连** —— 三条都对得上。
- **结论：倾向 A 供电中断**，B 不能完全排除。**一锤定音**：恢复后第一条是 `JY001/32/1`（重启过→A）
  还是 `JY001/31/923`（没重启→B/C）。

### Q2 WS 能不能上线？依据？浏览器验证补了吗？

**可以上线（协议级）**。依据：① 运行时 5 条语义逐条实测通过且服务端日志佐证；② 专项 28 /
全量 95 passed，未放宽断言；③ **无迁移**，部署面只有代码；④ 演练证明
`merge --no-ff` → 起实例 → `/health 200` → `reset --hard a01bb33` 全链路可用可回滚。
**浏览器验证没补** —— 本机无 chromium/playwright，2 核 3GB 装不了。这是明确的未验证项，
上线首日必须人工看一次。

### Q3 004 现在能不能执行？分级数字、抽样证据、回滚演练结果？

**技术上可以，但建议先拍板口径再执行**。分级（生产只读预演）：**4 可信 / 7047 推定 / 2 pending**。
抽样：可信 4 条偏差 −0.6s；pending 2 条是 `JY001/99/1`、`JY001/99/2`，
`sampled_at=2025-10-17 11:00:00`，**偏差 −365 天**且 `time_quality=1`（说明 `time_quality` 不可信）。
回滚演练：测试库 `--apply` 写 3 行 → 断言三档落库 → 执行自动生成的回滚 SQL →
断言全部恢复成 NULL（含 `binding_id`），**收敛**；`--only-trusted` 时推定档一行不动。
**未决**：7047 行推定是否写 `resolved`；现行迁移 SQL 缺护栏（建议走脚本）。

### Q4 7 个悬挂 job 什么原因，清完还会不会有？

**回答不了 —— 数据不在本机。** 本机生产库 28 张表无任何 `job`/`turn`/`judge` 表，
代码零命中，Redis 全空；那 7 条属 141 内网智农AI。工具已按通用口径写好（三条口径 + dry-run
默认 + 不误伤正常 queued，6 条测试），拿到表名列名即可跑。
**会不会再生**：只清数据不修机制一定再生 —— 终态矛盾说明下游靠"上游完成时拉起"这一路漏了
（建议改用消息 + 兜底补扫）；孤儿说明上下游不是同一事务或有丢写（建议加外键约束，现在没有）。
这两条**本次无法验证**。

### Q5 `www.usr.cn` 那 1753 条解析错误，分类后各是多少？

**回答不了 —— 报文没有留存，无法回溯分类。** `raw_messages` 只有 `stored` 11994 行，
被拒的一条都没写；服务日志不记 payload。能给的只有总数：journal 全量 **6078** 条
（任务书里的 1753 是 10-22 当时的累计），10-22 17:00 起 1903 条，10-23 当天 1066 条，断流后 0 条。
能确定的：抓包窗口内原文是 ASCII `www.usr.cn`（10 字节），相位 30s 且相对遥测**缓慢漂移**
（8 小时 −1s）→ 与 MCU **不共用一个时钟**，更可能是 DTU 自己产生的（**推断**，需现场确认）。
**要拿到分类数字，必须先上线 `007` 并接线**，从那一刻开始积累。

---

## 14. 上线建议（分批）

| 批次 | 内容 | 批准 | 窗口 | 风险 |
|---|---|---|---|---|
| 1 | 备份 → 迁移 003/004/005 → WS 候选 `ac133fa` | 🅐 | 🅦（迁移） | 中（004 改 7,053 行） |
| 2 | 迁移 006 + T3 入网闭环（**不含 authn**） | 🅐 | — | 低（只加列 + 新接口） |
| 3 | T4 状态页（接口 + 静态页） | 🅐 | — | 低（依赖 005 的 `metric_definitions`） |
| 4 | 004 数据回填（用脚本，先 `--only-trusted` 再放量） | 🅐 | 🅦 | 中（改写 7,053 行语义） |
| 5 | 007 解析错误分类（可选，需单独批准接线） | 🅐 | — | 低（只加列 + 聚合表） |
| 6 | **EMQX authn 启用** | 🅐 | 🅦 设备侧配合 | **高**（设备未改凭据会集体离线） |

完整命令见 `docs/release-runbook-20261023.md`。

---

## 15. 本夜产出文件

```
worktree/
  docs/outage-triage-20261023.md            T1 断流证据链 + 三假设判据 + 现场排查步骤
  scripts/t1_outage_20261023.sql            T1 只读取证 SQL
  docs/ws-acceptance-20261023.md            T2 验收报告 + 判断
  scripts/ws_runtime_check.py               T2 协议级 5 项实测
  scripts/deploy_rehearsal.sh               T2 部署演练（临时副本）
  scripts/backfill_attribution.py           T3 回填脚本（含护栏/manifest/回滚）
  tests/test_backfill_attribution.py        T3 6 条
  docs/attribution-backfill-20261023.md     T3 口径 + 生产数字 + 回滚
  scripts/stuck_job_sweeper.py              T4 通用悬挂清理
  tests/test_stuck_job_sweeper.py           T4 6 条
  docs/stuck-job-sweeper-20261023.md        T4 口径 + 本机盘点结论
  app/core/parse_error_classifier.py        T5 四类分类（候选，未接线）
  migrations/007_parse_error_kind_{up,down}.sql
  scripts/parse_error_stats.sql             T5 只读统计口径
  tests/test_parse_error_classifier.py      T5 10 条
  docs/parse-error-classification-20261023.md
  scripts/check_mqtt_credentials_schema.py  T6 列比对
  tests/test_check_mqtt_credentials_schema.py  T6 3 条
  docs/device-onboarding-gap-20261023.md    T6 现状核对 + 补救候选
  scripts/dbq.sh                            T7 只读守卫（已安装到 /opt/greenhouse-nightly/dbq.sh）
  scripts/outage_watch.py                   T7 断流事件记录
  tests/test_dbq_guard.py                   T7 4 条
  tests/test_outage_watch.py                T7 8 条
  docs/ops-tools-20261023.md                T7 三项修正
  docs/release-runbook-20261023.md          T8 上线清单 + 回滚手册
  docs/frontend-integration-20261023.md     T9 接入说明（阻塞）
/opt/greenhouse-nightly/
  t1_outage_20261023.sql                    同上的取证 SQL
  ws_runtime_check.py / deploy_rehearsal.sh T2
  dbq.sh（新版）/ dbq.sh.bak-20261023-0150   T7
  observer.py / observer.py.bak-20261023-0210  T7
  logs/ws-uvicorn-8011.log、logs/sim-check-*.json 等
```
