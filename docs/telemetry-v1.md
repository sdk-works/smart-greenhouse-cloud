# 遥测数据契约 v1（telemetry-v1）

> 本文档是 2026-10-21 夜间任务固化的**入库契约**。凡是写入 `telemetry` 表的路径
> （MQTT 上行、批量入库）都必须遵守；读取侧据此判读。

## 1. 时间字段

| 字段 | 含义 | 必填 | 说明 |
|---|---|---|---|
| `timestamp` | 服务器接收时间 | 是 | 入库时刻，永远有值，**不受设备时钟影响** |
| `sampled_at` | 设备采样时间 | 否 | 设备时钟不可信时为 `NULL` |
| `time_quality` | 时间可信度 | 是 | `1` = 时间可信；`0` = 无可信时间（如未校时） |

规则：
- `time_quality=1` 时才允许把 `sampled_at` 当作真实采样时间使用。
- 新鲜度判定（见 `docs/state-machines.md` / M3）**先取 `sampled_at`，
  `time_quality=0` 时退回 `created_at`**，并在结果里标注 `basis`。
- **绝不**用接收时间去覆写 `sampled_at`，也**不**用 `sampled_at` 反推 `timestamp`。

## 2. 归属字段（第三批引入）

| 字段 | 含义 |
|---|---|
| `greenhouse_id` | 归属大棚 |
| `company_id` | 归属公司 |
| `attribution_status` | `resolved`（已按绑定窗口确认） / `pending`（未确认） |
| `attribution_reason` | `binding_window` / `no_binding_history` / `outside_binding_window` / `ambiguous_binding_window` / `legacy_before_attribution` |
| `binding_id` | 判定所依据的 `device_bindings.id`（`pending` 时为 `NULL`） |
| `binding_version` | 判定所依据的绑定版本 |

判定规则详见 `app/core/attribution.py`：
- 事件时刻落在**唯一一条**绑定窗口内 → `resolved`
- 无绑定历史 / 落在所有窗口之外 / 同版本多条同时生效 → `pending`

**重要**：`attribution_status=pending` 时 `greenhouse_id` 可能是设备当前所在大棚的
兜底值，**不代表归属已被确认**。读取方必须看 `attribution_status`，不能只看 `greenhouse_id`。

## 3. 位置与来源字段

| 字段 | 含义 |
|---|---|
| `position_quality` | 位置质量（保留，当前由点位映射解析结果决定） |
| `chassis_point` | 底盘点位编号 |
| `greenhouse_point` | 大棚点位名称 |
| `mapping_id` | 命中的点位映射 |
| `map_version_id` | 关联的已发布地图版本 |
| `raw_message_id` | 关联的原始报文 |
| `raw_data` | 原始报文全文（便于追溯/重放） |

## 4. 去重

- `msg_id` + `device_id` 唯一（写入 `device_receipts`）。
- `msg_id` 复用但内容摘要（`digest`）不同 → 判为冲突，拒绝入库。
- 同一 `msg_id` 重复到达 → 幂等返回，不重复入库。

## 5. 数据质量维度（沿用架构基线 v2）

| 维度 | 存放 | 说明 |
|---|---|---|
| 测量有效性 | `data_type` / 指标定义区间 | 越界值入库但打标，不静默丢弃 |
| 时间质量 | `time_quality` | 见 §1 |
| 位置质量 | `position_quality` | 见 §3 |
| 新鲜度 | **查询时计算** | 不入库写死，见 `app/core/freshness.py` |
| 归属 | `attribution_status` | 见 §2 |

## 6. 契约测试位置

`tests/test_time_quality.py`、`tests/test_ingest_regression.py`、
`tests/test_m3_data_reliability.py::test_attribution_*`。
