# `www.usr.cn` 垃圾上行与解析错误分类 · 2026-10-23

> 交付形态：**候选**。没有改 `app/mqtt/mqtt_service.py` 一行 —— 现有"解析失败即丢"的行为
> 原样保留。接线是独立的 3 行 diff（§4），需要人批准后才做。

## 1. 现状（生产只读实测）

```
$ psql -d greenhouse -f scripts/parse_error_stats.sql

===== 1. 007 是否已上线 =====
 stats_table | kind_column
-------------+-------------
             |           0          <- 表和列都不存在

===== 5. raw_messages 的 parse_status 分布 =====
 parse_status | rows  | first_at                      | last_at
--------------+-------+-------------------------------+-------------------------------
 stored       | 11994 | 2026-10-21 09:58:02.435013+08 | 2026-10-23 08:54:18.977221+08
```

**`raw_messages` 里一条 `rejected` 都没有** —— 解析失败的报文从未落库，
服务端只在日志里打一行 WARNING 就丢。所以"解析错误"永远只有一个总数，无法解释。

服务日志里的解析失败计数（只读 `journalctl`）：

| 口径 | 条数 |
|---|---|
| journal 全量（自 2026-10-17 11:55:22 首次出现起） | **6078** |
| 2026-10-22 17:00:38（DTU 改配置后）起 | 1903 |
| 2026-10-23 当天（到 08:55 设备断流为止） | 1066 |
| 断流后（08:55:19 起） | **0**（跟着设备一起停了，见 `docs/outage-triage-20261023.md`） |

## 2. 直接回答"1753 条分类后各是多少"

**回答不了。**

缺的东西：**原始报文没有留存**。`raw_messages` 只有 `stored` 11994 行，
被拒的一条都没写；服务日志只记"JSON解析失败 + topic"，**不记 payload**。
历史报文既不在库里也不在日志里，**没有任何办法回溯分类那 1753 条**。

能确定的只有这些（不越界）：

| 事实 | 证据 | 置信度 |
|---|---|---|
| 至少在 10-22 晚间的抓包窗口内，垃圾上行原文是 ASCII `www.usr.cn`（10 字节，非 JSON） | 上一夜用 ACL 放行的只读观测 clientid 订阅抓到的原文 | 抓包窗口内确定 |
| 它与遥测同窗口交替出现，相位 30s 且**相对遥测缓慢漂移**（00 点 `:19/:49` → 08 点 `:18/:48`，8 小时漂 −1s） | 服务日志时间戳统计 | 确定 |
| 漂移说明它和遥测**不共用一个时钟** → 更可能是 DTU 自己产生的，不是 MCU | 两个独立振荡器才会漂移 | **推断**，需现场确认 |
| 全部 6078 条都是同一签名 | **没有证据**，不能这么断言 | — |

**要拿到分类数字，必须先上线留存**（§3），然后从上线那一刻开始积累。

## 3. 交付物

| 文件 | 作用 |
|---|---|
| `app/core/parse_error_classifier.py` | 四类分类 + 报文指纹 + 落库（**可独立运行**） |
| `migrations/007_parse_error_kind_{up,down}.sql` | `raw_messages.parse_error_kind` 列 + 聚合表 `parse_error_stats`（全部 `IF NOT EXISTS`） |
| `scripts/parse_error_stats.sql` | 只读统计口径（生产可直接跑） |
| `tests/test_parse_error_classifier.py` | 10 条测试 |

### 3.1 四类

| 分类码 | 判据 | 生产的对应物 |
|---|---|---|
| `duplicate_registration` | 非 JSON，且命中已知厂家注册包/心跳包签名（`www.usr.cn` 等） | 那 1066 条/天 |
| `non_json` | 非 JSON（含"能解析但顶层不是对象"） | 乱码、截断报文 |
| `device_mismatch` | 报文自称 `device_id` 与 topic 里的设备不一致 | 冒用/配错 |
| `missing_field` | JSON 合法但缺必需字段（device 通道要 `msg_id`） | 固件改版漏字段 |

CLI 实跑（四种各一条）：

```
$ python -m app.core.parse_error_classifier 'www.usr.cn'
kind    : duplicate_registration
detail  : 厂家注册包/心跳包签名 'www.usr.cn'（首次出现）

$ python -m app.core.parse_error_classifier '{"type":"telemetry"}'
kind    : missing_field
detail  : 缺少必需字段: msg_id

$ python -m app.core.parse_error_classifier '{"type":"telemetry","msg_id":"X/1/1","device_id":"JY002"}'
kind    : device_mismatch
detail  : topic 设备=JY001，报文自称 device_id=JY002

$ python -m app.core.parse_error_classifier '{"broken'
kind    : non_json
detail  : JSON 解析失败: JSONDecodeError: Unterminated string starting at: line 1 column 2 (char 1)
```

### 3.2 为什么要一张聚合表

每 30 秒一条 `www.usr.cn` = **2880 行/天**。逐条落 `raw_messages` 既撑表又看不出
"其实从头到尾都是同一条垃圾"。`parse_error_stats` 按
`(device_id, kind, signature)` 聚成一行 + 计数：

| device_id | kind | signature | cnt | first_seen | last_seen |
|---|---|---|---|---|---|
| JY001 | duplicate_registration | `www.usr.cn` | 3 | … | … |

测试已钉住：同一条垃圾连发 3 次 → 聚合表**只有 1 行**且 `cnt=3`；
同时 `raw_messages` 有 3 条 `rejected`，`device_id` 正确、原始字节长度正确。

因此**建议生产接线时先 `persist_raw=False`**（只聚合、不逐条留存），
确认噪声类型稳定后再决定是否开逐条留存，并给 `rejected` 单独设更短的保留期
（现成的 `scripts/purge_raw_messages.py` 可按 `parse_status` 清）。

### 3.3 测试

```
$ /opt/greenhouse-nightly/run_tests.sh tests/test_parse_error_classifier.py -q
..........                                                               [100%]
10 passed
```

覆盖：四类各一条、正常报文一条、"顶层不是对象"归 `non_json` 而不是 `missing_field`、
指纹去重、被拒行的字段形状、聚合计数 3 次成一行、**观测链路不能把主链路搞挂**
（DB 报错必须吞掉并返回 `None`，不抛穿到收包流程）。

## 4. 接线方式（**未做，需人批准**）

`app/mqtt/mqtt_service.py::_dispatch_payload` 现在是这样：

```python
    async def _dispatch_payload(self, message):
        topic = str(message.topic)
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            logger.warning(f"[MQTT-V9] JSON解析失败 topic={topic}"); return
```

改成（3 行 diff，`_dispatch_payload` 已经是 async，可直接 await）：

```python
    async def _dispatch_payload(self, message):
        topic = str(message.topic)
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            # ↓ 新增：解析之前先分类留痕（观测失败不影响主链路）
            from app.core.parse_error_classifier import classify, record_parse_error
            kind, detail, _ = classify(topic, message.payload)
            logger.warning(f"[MQTT-V9] 解析被拒 topic={topic} kind={kind} detail={detail}")
            await record_parse_error(AsyncSessionLocal, topic, message.payload,
                                     kind, detail, persist_raw=False)
            return
```

前置：先跑 `migrations/007_parse_error_kind_up.sql`。

## 5. 待决策

1. **要不要接线？** 不接就永远拿不到分类数字，1753/6078 始终是个黑盒总数。建议接。
2. **逐条留存还是只聚合？** 建议先只聚合（`persist_raw=False`）。
3. **根治要不要做？** 分类只解决"能解释"，不解决"还在发"。真正的根治在 DTU 侧
   关掉串口注册包转发（需要现场改 DTU 配置）。**本次没有、也不会向设备下发任何指令。**

## 6. 复现

```bash
# 分类单条报文
python -m app.core.parse_error_classifier 'www.usr.cn' agri/device/JY001/up

# 测试库建表 + 跑测试
/opt/greenhouse-nightly/testdb_sql.sh migrations/007_parse_error_kind_up.sql
/opt/greenhouse-nightly/run_tests.sh tests/test_parse_error_classifier.py -v

# 生产只读统计（007 未上线时会报"表不存在"，这正是现状）
sudo -n -u postgres psql -d greenhouse -P pager=off -f scripts/parse_error_stats.sql
```
