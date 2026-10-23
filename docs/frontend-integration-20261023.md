# T4 前端接入说明 · 2026-10-23

> ## 状态：**阻塞 —— 缺可构建前端工程**
>
> 云服务器上的 `fe_src` 是**反编译出来的 webpack chunk**（`0574_services_api.js`、
> `1605_GreenhouseWorkbench.js` 等），有 `src/` 目录、`package.json`、构建脚本的**工程本身不在这台机器上**。
> 因此本次**无法接入、无法构建、无法做浏览器验证**；按任务书要求也不去拼一个工程出来。
> 本文只交付：拿到源码后**一次接对**所需的接入点清单、props 契约、数据依赖。

---

## 1. 交付物位置

| 文件 | 说明 |
|---|---|
| `frontend/GreenhouseStatusPanel.js` | React 组件（181 行，commit `d5f5f84`，分支 `night/status-page`） |
| `app/api/greenhouse_status.py` | 后端接口 `GET /api/v1/greenhouses/{id}/status` |
| `app/core/status_view.py` | 视图构造（字段契约在这里） |
| `static/greenhouse_status.html` | 单文件静态页（**不需要前端工程**即可独立验证，挂载在 `/pages`） |

## 2. 接入点清单（照着改 3 处）

从反编译的 `fe_src/1605_GreenhouseWorkbench.js` 定位到的实际结构：

```jsx
// 现有代码（反编译还原，格式已整理）
const { greenhouseId } = useParams();                 // ← greenhouseId 的来源
const [tab, setTab] = useState('overview');

<Tabs activeKey={tab}
      onChange={key => { setTab(key); if (key !== 'config') load(); }}
      destroyInactiveTabPane
      items={[
        { key: 'overview', label: '布局与数据总览', children: ... },
        { key: 'config',   label: '配置布局与采集点', children: ... },
        { key: 'records',  label: '点位记录入口',     children: ... },
        { key: 'profile',  label: '大棚档案',         children: ... },
      ]} />
```

| # | 改什么 | 怎么改 |
|---|---|---|
| 1 | 放文件 | 把 `GreenhouseStatusPanel.js` 落到前端工程 `src/pages/GreenhouseStatusPanel.js`（与 `GreenhouseWorkbench` 同级） |
| 2 | 引入 | 在 `GreenhouseWorkbench` 顶部 `import GreenhouseStatusPanel from './GreenhouseStatusPanel';`（若放 `pages/` 下用 `./GreenhouseStatusPanel`，放别处按实际路径） |
| 3 | 加 Tab | 在 `items` 数组里插入一项，建议放在 `records` 之后、`profile` 之前：<br>`{ key: 'status', label: '设备与数据状态', children: <GreenhouseStatusPanel greenhouseId={greenhouseId} /> }` |

**两个坑（从反编译代码里看出来的）**：

1. `onChange` 里是 `if (key !== 'config') load();` —— 切到新 tab **会**触发一次 `load()`
   （那是 workbench 自己的数据加载，`PlanDrawing`/`doc` 相关），与新组件无关但会多发一次请求；
   新组件自带 15 秒轮询，**不要再在外面套一层定时器**。
2. 组件用了 antd 的 `Tabs items` 之外的这些：`Alert / Card / Empty / Select / Space / Table / Tag / Tooltip / Typography`。
   父组件 chunk 里已 import 了 `Tabs, Tag, Typography, message` from 'antd'，说明 antd 版本较新，
   但 **`Empty.PRESENTED_IMAGE_SIMPLE`、`Table` 的 `rowKey` 函数形式**要确认版本支持（antd 4.x 起都支持）。

## 3. props 契约

```jsx
<GreenhouseStatusPanel
  greenhouseId={greenhouseId}   // string，必填。来自 useParams()，不要传 devices.id 或大棚 UUID
  maxAgeSeconds={900}           // number，可选，默认 900。对应接口的 max_age_seconds
/>
```

| prop | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `greenhouseId` | string | ✅ | — | 大棚 **UUID**。传错/越权 → 接口 404 → 组件显示"这个大棚不存在，或不属于你所在公司" |
| `maxAgeSeconds` | number | ❌ | 900 | 只作为**初始值**；组件内部有 `Select`（5 分钟 / 15 分钟 / 1 小时）可改，改了会重新拉 |

组件**不接收**数据 prop（不搞受控数据），自己 `api.get` 拉取 + 15 秒轮询；
`greenhouseId` 变化时会用 ref 丢弃过期响应（避免快速切棚时旧响应覆盖新数据）。

## 4. 数据依赖

### 4.1 接口

```
GET /api/v1/greenhouses/{greenhouse_id}/status?max_age_seconds=900
```

- **鉴权**：走 axios 拦截器带的 token，**前端不用额外传**。
- **隔离**：接口按公司隔离，跨公司返回 **404**（不是 403，不泄漏存在性）。
- **后端前置**：依赖迁移 **005** 的 `metric_definitions`（指标名称/单位/精度/合理区间都出自这里）。
  生产 005 **未执行** —— 接口会返回空 `metrics`，不是前端的问题。

### 4.2 响应字段（组件实际用到的，逐字段对齐过 `status_view.py` / `greenhouse_status.py`）

```
{
  greenhouse: { id, name, company_id },
  as_of,                       // ISO 时间
  max_age_seconds,
  summary: {
    metrics_total, metrics_ok, metrics_expired, metrics_missing, metrics_out_of_range,
    devices_total, devices_mqtt_online, devices_data_fresh,
    attribution: { trusted, inferred, pending, unknown },
    anomalies_total, errors
  },
  devices: [{
    id, device_id, name,
    mqtt:   { connected,           // boolean，只代表链路在
              source,              // 'redis_online_cache' | 'db_status' 等
              db_status, last_seen },
    data:   { has_data, last_valid_at, basis, age_seconds, freshness, within_max_age, expired },
    backfilled_rows,               // number
    attribution: { grade, label, detail },   // grade ∈ trusted/inferred/pending/unknown
    note,                          // 仅"本棚内无任何遥测"时出现
    credential: { registered, enabled, username, asset_no }   // 由 API 层并入
  }],
  metrics: [{
    code, name, unit, precision, device_id,
    value, display_value,         // 过期时 display_value=null，value 保留备查
    state, message,               // ok / expired / missing / out_of_range
    last_updated_at, last_updated_source, age_seconds, freshness,
    attribution: { grade, label, detail },
    device_reports_channel_invalid,   // boolean
    history: [{ basis_at, created_at, msg_id, is_backfill }]
  }],
  anomalies: [{ code, level, message, ref }]   // level ∈ error/warning/info
}
```

`anomalies.code` 的取值（前端若要分色/跳转，按这个枚举）：
`sensor_invalid` / `data_stalled` / `mqtt_online_without_data` / `identity_conflict` /
`credential_missing` / `credential_disabled` / `value_out_of_range` / `attribution_pending`。

### 4.3 组件**刻意遵守**的四条（改动时不要破坏）

1. 「MQTT 已连接」与「最近有效数据」**分两栏**，不合并成一个"在线/离线"；
2. 过期数据只渲染「数据已过期」，`display_value=null`，**不把旧数值当实时值**；
3. 补传数据只在 `history` 里出现（带「补传」标签），**不进当前值**；
4. 归属分**可信 / 推定 / 待定 / 未判定**四档，标签不同；拿不到依据一律显示"未知"，**不猜**。

## 5. 不改前端也能验收的办法

```
# 起夜间的只读测试实例（测试库），浏览器打开：
#   http://127.0.0.1:8011/pages/greenhouse_status.html
# 填棚 ID + 接口基址 http://127.0.0.1:8011/api/v1 + 登录后拿到的 token
/opt/greenhouse-nightly/smoke_t4.sh
```

单文件静态页与 React 组件取的是**同一份接口数据**，字段一致。

## 6. 待决策

1. **前端仓库路径是什么？** 拿到了才能真接。本次没有。
2. **Tab 放哪个位置 / 叫什么名？** 建议 `'status'` + 「设备与数据状态」，放在「点位记录入口」之后。
3. **`metric_definitions` 只有 8 条种子**：设备上报的 `soil_nitrogen` 等若无定义**不会展示**，
   上线前要按实际设备补齐（是数据问题，不是前端问题）。
