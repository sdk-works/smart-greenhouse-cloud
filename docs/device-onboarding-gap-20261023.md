# 006 / T3 入网：现状核对与补缺 · 2026-10-23

> 原则：**只补缺，不重写**。核对下来的结论是主体已完备，缺的是"上线前的列比对"。

## 1. 现状核对

| 项 | 现状 | 结论 |
|---|---|---|
| `migrations/006_device_onboarding_up.sql` | `CREATE TABLE IF NOT EXISTS` + 全部 `ADD COLUMN IF NOT EXISTS` + `CREATE UNIQUE INDEX IF NOT EXISTS`；down 脚本**只删自己加的列，不 DROP TABLE** | ✅ 已满足"生产已有表"的要求，不用改 |
| `app/api/device_onboarding.py` | 登记 / 换密 / 停用 / 安装清单四个端点都在 | ✅ |
| `app/services/mqtt_credential_service.py` | 认证判定（生产钩子与测试同一份代码） | ✅ |
| `tests/mqtt_test_broker.py` | 进程内最小 MQTT broker（只给测试用） | ✅ |
| `tests/test_device_onboarding.py` + `tests/test_integration_t3_t4_t5.py` | 7 条，本轮真跑全过 | ✅ |
| **上线前的列比对** | **没有** —— 006 全是 `IF NOT EXISTS`，直接跑不会报错，但也不会告诉你生产这张表缺什么 | ❌ **本次补上** |

## 2. 补的东西：`scripts/check_mqtt_credentials_schema.py`

只读，四个检查：表是否存在 / 006 需要的列是否齐全 / 类型与 NOT NULL 是否与声明一致 /
**现有凭据的 `device_id` 能不能在 `devices.device_id` 里对上**（对不上入网接口会查不到）。

### 2.1 生产实测（只读，未改任何东西）

```
$ python3 scripts/check_mqtt_credentials_schema.py --db-url "$DATABASE_SYNC_URL"
库=greenhouse
表存在        : True
现有列        : clientid, created_at, device_id, enabled, note, password_hash, username
缺列          : 无
006 待补列    : ['asset_no', 'compat_identity', 'updated_at', 'disabled_at',
                'last_rotated_at', 'rotation_count', 'created_by', 'updated_by']
缺索引        : ['ux_mqtt_credentials_device', 'ix_mqtt_credentials_enabled']
凭据行数      : 1
  - username=dev-jy-dtu-000001 clientid=02501026081300004386 device_id=JY001 enabled=True 设备对不上=False

阻断问题 0 条：
  （无，006 可以安全执行）

警告 1 条：
  - 列 device_id 与 006 声明不一致：期望 character varying/NOT NULL，实际 character varying/NULL
    （IF NOT EXISTS 不会修，需单独 ALTER）
exit=0
```

**结论：006 在生产上可以安全执行**（缺的 8 列 + 2 个索引都是 006 自己要加的）。
唯一警告是生产那张表 `device_id` 允许 NULL，而 006 的 `CREATE TABLE` 声明是 NOT NULL ——
`IF NOT EXISTS` 不会去改它，所以执行完仍然是 NULL-able。影响面很小
（`ux_mqtt_credentials_device` 唯一索引允许多个 NULL），但建议一并补掉（§3）。

### 2.2 测试库（006 已执行）

```
库=greenhouse_nightly_test
缺列: 无    006 待补列: 无    缺索引: 无    凭据行数: 0
```

自测：`tests/test_check_mqtt_credentials_schema.py` **3 passed**
（迁移后的库退出码 0 且无缺列 / `--json` 结构正确 / 缺 DSN 时快速失败退出码 2）。

## 3. 候选补救 SQL（**未执行**，生产要跑需人批准）

```sql
-- 1) 先确认没有 NULL（生产只有 1 行，device_id='JY001'，安全）
SELECT count(*) FROM mqtt_credentials WHERE device_id IS NULL;   -- 必须为 0

-- 2) 把 device_id 补成 NOT NULL
ALTER TABLE mqtt_credentials ALTER COLUMN device_id SET NOT NULL;

-- 3) 给 006 之前就存在的那一行补初值（compat_identity = 老的报文身份 JY001）
UPDATE mqtt_credentials
   SET compat_identity = device_id,
       updated_at      = now()
 WHERE compat_identity IS NULL;
```

## 4. 端到端验证（登记 → 凭据 → 连接 → 上行 → ACK → 停用拒绝）

用 `tests/mqtt_test_broker.py`（进程内 broker，**不连生产 EMQX**）：

```
$ /opt/greenhouse-nightly/run_tests.sh tests/test_device_onboarding.py \
      tests/test_integration_t3_t4_t5.py -q
.......                                                                  [100%]
7 passed
```

```
$ /opt/greenhouse/venv/bin/python scripts/sim_device_check.py
...
[PASS] 负向｜非 JSON 报文（www.usr.cn）
        非 JSON 报文被丢弃、不入库、服务不崩
        raw_messages 新增 0 条（应为 0）
[PASS] 负向｜凭据停用后再连接
        停用后 broker 拒绝连接（CONNACK rc=5），消息一条也发不出去
        connect rc=5（5=被拒），收到 ACK 0 条
==================================================================
合计 14 项：通过 14 / 失败 0
```

14 项里与入网闭环直接相关的：正常遥测 + ACK 回收、重复发送、ACK 丢失后重发、
重启换 boot、旧 boot 补传、乱序、**凭据停用后连接被拒（rc=5）**。

## 5. 边界

- 本次**没有**改 `006_device_onboarding_up.sql`，也没有动生产库那一行凭据。
- EMQX authn **仍未启用**（生产 `username=dev-jy-dtu-000001` 目前只是 DTU 侧配置，
  broker 不做校验）。启用步骤见 `docs/emqx-authn-enable.md`，**需要人批准且必须先改设备固件**。
