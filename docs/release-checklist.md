# 发布清单（候选 → 生产）

> 状态：**未部署**。生产仍运行 `master@a01bb33`，2026-10-22 夜间未重启、未 push、未改 `.env`、
> 未改 nginx/EMQX 运行配置。本文是"照着做"的清单，不是已完成的记录。

## 1. 分支与提交（夜间产物）

| 分支 | commit | 内容 |
|---|---|---|
| `night/integration` | `413156c` | 004 采集时刻可信性校验 + 执行/回滚手册（继承既有 M1~M4 + WS 候选） |
| `night/mqtt-onboarding` | `fb1fff2` | T3 设备入网与凭据闭环 |
| `night/status-page` | `60bfd87` | T4 大棚设备与数据状态（接口 + 页面） |
| `night/sim-tool` | `6dfe5ea` | T5 模拟器 + 自动联调工具 |
| `night/integration` | （本次） | I 集成联调 + 台账/发布清单 |

三条任务分支是线性依赖（T3 ← T4 ← T5），所以 `night/integration` 直接快进到
`night/sim-tool` 的提交，集成提交只加跨模块联调，不重复开发。

## 2. 上线前的硬性前置（按顺序）

1. **`attribution_status` 为 NULL 的 7,053 行怎么定性**必须拍板（见 `docs/migration-004-runbook.md` §7）。
2. **004 的偏差护栏**是否接受（`|sampled_at − 接收时刻| > 300s` 降级为 pending）。
   生产实测有 2 行 `sampled_at` 差整整一年（`2025-10-17 11:00:00`）。
3. **`devices` 表没有 `company_id` 列**：T3/T4 的公司隔离都是经 `greenhouse_id` 推导的。
   若后续要按设备直接授权，需要新增列 + 回填（本次没做）。
4. **EMQX authn 启用必须先改设备固件凭据**（否则一开认证，现有设备全部离线）。
   步骤见 `docs/emqx-authn-enable.md`，本次未执行、未重启。
5. **`/api/v1/emqx/authn` 不能暴露到公网**（它不吃业务 token，只靠共享密钥；
   未配置 `EMQX_AUTHN_TOKEN` 时端点直接 503）。上线前确认 nginx 规则。

## 3. 数据库迁移（生产必须先跑 up 再切代码，顺序不能反）

```
003_robot_loop_up.sql
004_data_reliability_up.sql      ← 含 7,053 行归属回填，先跑 004_preflight.sql 核对数量
005_business_up.sql              ← metric_definitions 种子（T4 的指标单位靠它）
006_device_onboarding_up.sql     ← 只补字段，不 DROP 表（生产 mqtt_credentials 已存在）
```

回滚：`003/004/005/006` 各有 `_down.sql`，**只在测试库执行过**。生产回滚前先备份；
004 的 down 会连带删 `alerts/incidents/incident_events`，若已写入业务数据则只回退归因字段。

## 4. 建议的分批上线顺序

| 批次 | 内容 | 风险 | 建议 |
|---|---|---|---|
| **第 1 批** | 迁移 003/004/005 + 既有 M1~M4 与 WS 候选（`cb3433f`+`1aa4b48`） | 中：004 改写 7,043 行归属 | 先跑 preflight，上线后第一天盯 `/health` 与 WS 的「丢弃未归属频道的消息」 |
| **第 2 批** | 迁移 006 + T3 入网闭环（不含启用 authn） | 低：纯增量表 + 新接口，不动既有读路径 | 可独立上线；上线后先给 1~2 台设备试登记 |
| **第 3 批** | T4 状态页（接口 + `static/greenhouse_status.html`） | 低：只读新接口 | 依赖 005 的 `metric_definitions`；页面是新增静态页，不改动现有页面 |
| **第 4 批** | EMQX authn 启用 | 高：设备侧没同步改凭据就会集体离线 | **必须**先完成设备固件凭据改造，并挑一台设备试点 |
| **第 5 批** | T5 模拟器 | 无（只跑测试库，不进生产镜像也可） | 随代码带上无副作用；不要在生产库上跑（脚本自检会拒绝） |

## 5. 上线当天要盯的数字

- `/health` 200；`systemctl is-active greenhouse` = active。
- 遥测节奏：应为 **60 秒/条**（2026-10-22 17:17 固件烧录后已稳定在这个节奏）。
- ACK：每台设备每条 `up` 都应有 `device_receipts` 一行；重复入库应为 0。
- 日志里的 `[MQTT-V9] JSON解析失败`：正常应为 0。
  当前生产自 `2026-10-22 17:00:38` 起每 **30 秒**一条（累计 924+），
  抓包确认原文是 ASCII `www.usr.cn`——USR DTU 的注册包被透传进了上行主题，
  需要在 DTU 侧关掉，或服务端在解析前先落一份原始留存（当前解析失败即丢，查不到内容）。
- WS：`丢弃未归属频道的消息` 应为 0。

## 6. 未覆盖风险（上线前必须补）

1. T4 界面**没有合入前端工程**，只交付了两份可直接用的东西：
   单文件静态页 `static/greenhouse_status.html`（挂 `/pages`，可独立打开验证），
   和 React 组件 `frontend/GreenhouseStatusPanel.js`（与项目现有写法一致，
   在 `GreenhouseWorkbench` 加一个 Tab 引入即可）。
   组件语法已用 `@babel/parser(jsx)` 解析通过，**未做浏览器视觉验证**，
   合入前端工程需要前端同学确认依赖（antd / `../services/api`）。
2. `metric_definitions` 目前只有 8 条种子指标；设备上报的 `soil_nitrogen` 等若无定义就不会展示。
3. 设备自报的 `valid` 位序与指标的对应关系**契约里没定义**，页面只报"几路未通过自检"，不指明是哪一路。
4. T4 的"MQTT 已连接"依赖 Redis 在线缓存；若 MQTT 心跳没写缓存，会一直显示离线（只读实例上就是这样）。
5. 模拟器只在测试库跑过，没有对真实 EMQX 与真实设备跑过。
