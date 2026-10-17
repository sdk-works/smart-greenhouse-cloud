# 智慧大棚云端平台

当前仓库可见性为 **Public**（按 PROJECT_MANAGEMENT.md 要求应设为 Private，待操作）。版本来源、配置和迁移边界见 [PROJECT_MANAGEMENT.md](PROJECT_MANAGEMENT.md)。

---

# 智慧农业巡检机器人云端平台

本目录是项目后续唯一开发主线，由 2026-08-24 线上盘点后建立。

## 目录职责

- `backend/`：FastAPI 后端、MQTT、数据库模型和图片服务。
- `frontend/`：可编辑的前端源码。
- `deploy/`：Nginx 与 systemd 部署模板，不包含密码。
- `docs/`：架构、协议、部署和版本说明。
- `simulator/`：机器人或报文模拟程序。

## 基线来源

- 完整工程骨架和前端源码：`/home/user/smart-greenhouse-v10`
- 当前线上后端增量：`/opt/greenhouse`
- 当前线上前端仅保存在快照中：`/var/www/greenhouse`

## 使用规则

1. 后续代码只在本目录修改，不再复制 V11、V12 等目录。
2. `/opt/greenhouse` 是部署目录，不直接作为开发目录。
3. `.env`、数据库备份、上传图片和运行日志不得提交 Git。
4. 部署前必须提交代码并打标签，部署后执行健康检查。
5. 历史 V3～V10 目录只读保留，确认新主线稳定前不删除。

## 当前状态

- 已完成全站 JWT 鉴权：14 个业务路由强制 `Depends(get_current_user)`，WebSocket 改用 `?token=` 鉴权；
- 修复 `/auth/users`(GET/POST) 可匿名拉取全量用户的后门；
- 表名 `dtu_receipts` 已修正为 `device_receipts`（对齐库里实际表名）；
- 已部署前端（React 18 + antd + echarts）并经 Nginx 反代托管，公网已禁 `/docs`、`/health`；
- EMQX ACL 由 `{allow, all}` 收紧为默认拒绝 + file ACL；
- 新增每日数据库备份脚本 `scripts/backup_db.sh`（保留 14 天，crontab 03:00）；
- 已建立 Git 版本管理（见下方「版本标签」），支持一键回退。
- 待完成：新工控机 MQTT 协议对接、新七参数土壤数据字段、巡检状态机统一（修改前需先完成协议对照与数据库备份验证）。

## 版本标签

| 标签 | 含义 | 提交 |
|---|---|---|
| `v1.0.0-baseline` | 优化前的线上基线（服务端 `/opt/greenhouse`） | — |
| `v1.1.0-security` | 全站鉴权 + 前端部署 + EMQX ACL 收紧 + 备份 + 版本管理 | `9063b26` |

配套固件仓库 `JY_EnvMon` 对应标签：`v1.1.0-fix-p0`（修复 Flash 数据覆盖 / 光照溢出 / 无效值误报）。

> 回退方法、部署目录与配置边界详见 [PROJECT_MANAGEMENT.md](PROJECT_MANAGEMENT.md)。
