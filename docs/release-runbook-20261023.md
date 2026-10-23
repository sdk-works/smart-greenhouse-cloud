# 上线清单 + 回滚手册（合并版）· 2026-10-23

> 状态：**全部未执行**。生产仍是 `master@a01bb33`、服务 active、`/health` 200。
> 本文把 `docs/release-checklist.md`、`docs/migration-004-runbook.md`、
> `docs/emqx-authn-enable.md` 合并成一条"照着敲"的链，逐步给出
> **前置检查 / 执行 / 验证 / 回滚 / 影响面**，并标出**需要谁批准**与**要不要停机窗口**。
> 每一步之间的顺序不能反：迁移必须先于代码，authn 必须最后。

图例：`🅐` = 需要用户批准 · `🅦` = 需要停机/低峰窗口 · `—` = 无需特别批准

---

## 步骤 0：备份与现状快照（**所有步骤的前置**）🅐

```bash
# 前置检查
systemctl is-active greenhouse          # 期望 active
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health   # 期望 200
git -C /opt/greenhouse rev-parse --short HEAD    # 期望 a01bb33

# 执行
bash /opt/greenhouse-nightly/worktree/scripts/backup_db.sh
git -C /opt/greenhouse tag -f pre-release-$(date +%F) master

# 验证：备份文件存在且非空
ls -la /opt/greenhouse/backups/ | tail -3

# 回滚：不需要（本步不改任何东西）
```

影响面：无。备份会占磁盘，先确认 `/opt` 剩余空间。

---

## 步骤 1：迁移 003 / 004 / 005 🅐 🅦

**为什么必须先跑**：004 会改写 `telemetry` 7,053 行的归属字段；
005 给 T4 状态页提供 `metric_definitions`；代码上线后若缺列会直接报错。

### 1.1 前置检查（只读，必须先做）

```bash
psql -d greenhouse -P pager=off -f migrations/004_preflight.sql
psql -d greenhouse -P pager=off -f migrations/004_preflight_sampled_time_sanity.sql
# 期望：attribution_status IS NULL = 7053；冲突窗口/缺公司/跨公司不一致 = 0/0/0
```

### 1.2 执行

```bash
cd /opt/greenhouse-nightly/worktree
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/003_robot_loop_up.sql
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/004_data_reliability_up.sql
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/005_business_up.sql
```

### 1.3 验证

```bash
psql -d greenhouse -At -c "select attribution_status, count(*) from telemetry group by 1;"
psql -d greenhouse -At -c "select count(*) from metric_definitions;"
psql -d greenhouse -At -c "select code, rows_affected from attribution_migrations;"
```

### 1.4 回滚

```bash
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/005_business_down.sql
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/004_data_reliability_down.sql
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/003_robot_loop_down.sql
```

⚠️ 004 的 down 会**连带删** `alerts` / `incidents` / `incident_events`。
若上线后已经写入业务告警数据，不要整体 down，只回退归属字段：见步骤 4.4。

### 1.5 影响面

| 项 | 说明 |
|---|---|
| 数据改写 | `telemetry` 7,053 行（写 3 个字段），其余为建表 |
| 锁 | 004 的 UPDATE 会持行锁；19k 行的表实测量级不需要长停 |
| 停机窗口 | 🅦 建议低峰执行；**不需要**整体停服 |
| 未决口径 | 7,047 行"推定"是否直接写 `resolved`（见 `docs/attribution-backfill-20261023.md` §7） |
| 与脚本的关系 | `004_data_reliability_up.sql` **尚未**含 300s 偏差护栏；建议改用步骤 4 的脚本执行回填 |

---

## 步骤 2：WS 候选 `ac133fa`（`release/ws-only`）🅐

**NO_MIGRATION，只有代码**。已通过协议级 5/5 与部署演练（见 `docs/ws-acceptance-20261023.md`）。

### 2.1 前置检查

```bash
ss -lntp | grep -E ':(8000|8011)'   # 记下生产 8000 的 PID，别杀错
# 确认前端所有 WS 都走 createWebSocket（带 ?token=）—— 需前端确认，本机无工程无法核实
```

### 2.2 执行（演练过的命令，先在临时副本验证过）

```bash
cd /opt/greenhouse            # 由人执行
git merge --no-ff release/ws-only -m "release: WS 强制鉴权 + 频道按公司隔离"
systemctl restart greenhouse
```

### 2.3 验证

```bash
systemctl is-active greenhouse
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health    # 200
journalctl -u greenhouse --since '5 min ago' | grep -E '\[WS\]' | tail -5
# 首日要盯：'[WS] 拒绝未认证连接' 与 '[WS] 丢弃未归属频道的消息' 的条数
```

### 2.4 回滚

```bash
cd /opt/greenhouse && git reset --hard a01bb33 && systemctl restart greenhouse
```

（演练过：临时副本 `merge --no-ff` → `/health 200` → `reset --hard a01bb33` → 工作区 0 改动。）

### 2.5 影响面

| 项 | 说明 |
|---|---|
| 掉线风险 | 任何**不带 `?token=`** 的 WS 连接会被 4401 拒绝；无公司归属的账号 4403 |
| 平台运维 | WS 上只能看本公司数据（跨公司看板继续走 REST） |
| 未验证 | **浏览器未验证**（本机无 chromium/playwright），上线首日必须人工看一次 |
| 停机窗口 | — 重启一次即可（秒级） |

---

## 步骤 3：迁移 006 + T3 入网闭环 🅐

### 3.1 前置检查（列比对，本次新交付）

```bash
python3 scripts/check_mqtt_credentials_schema.py --db-url "$DATABASE_SYNC_URL"
# 实测：阻断问题 0 条（缺的 8 列 + 2 索引都是 006 要加的）；
#       警告：device_id 在生产是 NULL-able（IF NOT EXISTS 不会修）
```

### 3.2 执行

```bash
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/006_device_onboarding_up.sql
# 可选（见 docs/device-onboarding-gap-20261023.md §3，需批准）：
#   ALTER TABLE mqtt_credentials ALTER COLUMN device_id SET NOT NULL;
#   UPDATE mqtt_credentials SET compat_identity = device_id WHERE compat_identity IS NULL;
cd /opt/greenhouse && git merge --no-ff night/mqtt-onboarding && systemctl restart greenhouse
```

### 3.3 验证

```bash
python3 scripts/check_mqtt_credentials_schema.py --db-url "$DATABASE_SYNC_URL"
#   期望：006 待补列 = 无、缺索引 = 无、退出码 0
curl -s http://127.0.0.1:8000/openapi.json | grep -o '/api/v1/devices/{[^}]*}/credential' | head -3
```

### 3.4 回滚

```bash
cd /opt/greenhouse && git reset --hard <上个发布点> && systemctl restart greenhouse
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/006_device_onboarding_down.sql
```

（`006_..._down.sql` **只删自己加的列，不 DROP 表** —— 生产这张表在 006 之前就存在。）

### 3.5 影响面

| 项 | 说明 |
|---|---|
| 数据 | 只加列 + 索引，不改既有 1 行凭据 |
| 新增接口 | 挂在现有设备详情下，不另开菜单 |
| **不含** | **不启用 EMQX authn**（见步骤 5） |
| 停机窗口 | — |

---

## 步骤 4：004 数据回填（用脚本，不用迁移 SQL）🅐 🅦

**建议用 `scripts/backfill_attribution.py`**：它内置 300s 偏差护栏、
`--limit` 小批量、`--only-trusted` 只写可信，并自动生成回滚 SQL。

### 4.1 前置检查

```bash
python3 scripts/backfill_attribution.py --db-url "$DATABASE_SYNC_URL" --dry-run --sample 5
# 期望：resolved/backfill_receive_time_estimated 7047
#       resolved/backfill_sample_time                4
#       pending/sample_time_implausible              2
```

### 4.2 执行（先小批量，再放开）

```bash
python3 scripts/backfill_attribution.py --apply --only-trusted      # 只写可信 4 条
python3 scripts/backfill_attribution.py --apply --limit 100         # 小批量试推定档
python3 scripts/backfill_attribution.py --apply                     # 全量（确认前两步没问题后）
```

⚠️ 脚本的 `--apply` **只允许库名含 `greenhouse_nightly_test`**。
要在生产执行，必须由人显式调整这条校验并确认后再跑 —— **本次没有放宽**。

### 4.3 验证

```bash
psql -d greenhouse -At -c "select attribution_status, attribution_reason, count(*)
                           from telemetry group by 1,2 order by 3 desc;"
```

### 4.4 回滚

```bash
psql -d greenhouse -v ON_ERROR_STOP=1 \
  -f /opt/greenhouse-nightly/logs/backfill-attribution-<ts>.rollback.sql
```

回滚原理：被处理的行改动前 `attribution_status` 一律是 NULL，所以回滚 =
把 manifest 里的 id 恢复成 NULL（连 `attribution_reason` / `binding_id` / `binding_version` 一起清）。
**不要**用 `WHERE attribution_reason IN (...)` 这类条件回滚 —— 会误清之后正常写入的行。

### 4.5 影响面

| 项 | 说明 |
|---|---|
| 数据改写 | 最多 7,053 行；`--only-trusted` 时只有 4 行 |
| 展示语义 | 7047 行从"未判定 NULL"变成"推定 resolved"，页面须显示"推定"角标（T4 已实现，未上线） |
| 停机窗口 | 🅦 低峰执行；纯 UPDATE 不需要停服 |
| 未决 | 推定档是否允许写 `resolved`（见 `docs/attribution-backfill-20261023.md` §7） |

---

## 步骤 5：EMQX authn 启用（**最高风险，必须最后**）🅐 🅦

### 5.1 前置检查（三条缺一不可）

```bash
# 1) 设备固件里的 MQTT 凭据已改成登记的用户名/口令，并已在一台设备上试点成功
# 2) /api/v1/emqx/authn 不暴露公网（它只吃共享密钥 EMQX_AUTHN_TOKEN）
# 3) 已确认没有未登记设备在线
sudo -n emqx ctl clients list
```

### 5.2 执行

见 `docs/emqx-authn-enable.md`（本次未执行）。要点：改 `/etc/emqx/emqx.conf` 加
`authentication`，ACL 从 `%c`（clientid）换成 `%u`（username），备份 `acl.conf` 后 reload。

### 5.3 验证

```bash
sudo -n emqx ctl clients list        # 真设备应仍在线
journalctl -u greenhouse --since '5 min ago' | grep -E 'DEV-ACK|MQTT-V9' | tail -5
```

### 5.4 回滚

```bash
# 恢复 emqx.conf 与 acl.conf 备份后 reload（authn 关闭 = 回到匿名可连的旧状态）
```

### 5.5 影响面

| 项 | 说明 |
|---|---|
| **最大风险** | 设备侧没同步改凭据 → **全部设备立刻离线且无法自行恢复** |
| 现状 | authn **未启用**，任何人可匿名连接、可自称任意 clientid（ACL 挡不住主动冒充） |
| 停机窗口 | 🅦 必须挑设备侧配合的时间窗，且**先一台试点** |
| 本次 | **未执行、未改配置** |

---

## 步骤 6（可选）：007 解析错误分类 🅐

```bash
psql -d greenhouse -v ON_ERROR_STOP=1 -f migrations/007_parse_error_kind_up.sql
# 接线 diff 见 docs/parse-error-classification-20261023.md §4（**未接线**）
psql -d greenhouse -P pager=off -f scripts/parse_error_stats.sql
```

回滚：`migrations/007_parse_error_kind_down.sql`（删列 + 删表，都是 007 自己加的）。
影响面：只加列 + 一张聚合表；**不改解析行为**（接线才改，且需单独批准）。
不接线的代价：`www.usr.cn` 那几千条解析错误永远是黑盒总数（**历史报文没留存，无法回溯分类**）。

---

## 汇总：谁批准 / 要不要窗口

| 步骤 | 批准 | 窗口 | 数据改写 | 回滚难度 |
|---|---|---|---|---|
| 0 备份 | 🅐 | — | 无 | — |
| 1 迁移 003/004/005 | 🅐 | 🅦 低峰 | 7,053 行归属 + 建表 | 中（004 down 会删告警表，注意） |
| 2 WS 候选 | 🅐 | 重启一次 | 无 | 低（`reset --hard` 已演练） |
| 3 迁移 006 + 入网 | 🅐 | — | 只加列 | 低（down 只删自己加的列） |
| 4 004 回填（脚本） | 🅐 | 🅦 低峰 | 最多 7,053 行 | 低（manifest + 回滚 SQL 自动生成） |
| 5 EMQX authn | 🅐 | 🅦 设备侧配合 | 无（但会断连） | **高**（设备可能集体离线） |
| 6 007 解析分类 | 🅐 | — | 加列 + 聚合表 | 低 |

## 上线当天要盯的数字

```
systemctl is-active greenhouse                       # active
curl -s -o /dev/null -w '%{http_code}\n' .../health  # 200
journalctl -u greenhouse --since '10 min ago' | grep -c 'DEV-ACK'            # 应持续增长
journalctl -u greenhouse --since '10 min ago' | grep -c '\[WS\] 丢弃未归属频道的消息'  # 应为 0
python3 scripts/outage_watch.py --db-url "$DATABASE_SYNC_URL" --device JY001 --no-write
#   断流事件应为空（当前会因为 08:55 那次报一条"进行中"，属已知）
python3 /opt/greenhouse-nightly/observer.py --reconcile
#   应有/缺失/重复：缺 0、重复 0、主游标重读 0
```
