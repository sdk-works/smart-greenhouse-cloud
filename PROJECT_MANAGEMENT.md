# 智慧大棚云端平台：版本管理

公司内部项目，GitHub仓库必须保持Private；依赖及第三方源码的原许可证继续适用。

> ⚠️ **2026-10-17 核查发现：远端 `sdk-works/smart-greenhouse-cloud` 当前为 Public。**
> 文档要求私有，实际公开，两者不一致。请立即在 GitHub → Settings → Danger Zone
> 将仓库改为 Private。目前仓库内已确认**不含真实密码**（凭据已替换为
> `SET_LOCALLY_DO_NOT_COMMIT`），但仍含内网地址、设备编号、MQTT 主题结构和全部
> 业务逻辑，建议按私有处理。

## 来源与状态

- 迁移日期：2026-10-08。
- 核验来源：`/home/user/projects/smart-greenhouse-cloud`。
- 状态：正式源码工作区快照，包含迁移前未提交修改。原7次提交已脱敏保留，原始历史另存本地。运行后端另存runtime-reference-20261008分支，尚未合并判定。
- 托管副本：`D:/CompanyProjects/smart-greenhouse-cloud`。
- 远端：https://github.com/sdk-works/smart-greenhouse-cloud

## 工作方式

本次仅建立版本管理，不启动运动、不烧录、不发布服务，不覆盖原目录中的运行配置。
后续先更新仓库，在功能分支修改，记录检查结果后合并；发布或实车测试另行记录具体代码SHA。
原目录仍保留，后续修改若在那里进行，需要审查差异再同步，不能假设原目录已自动和GitHub同步。

## 配置与依赖

密码和连接凭据已在上传副本中替换为SET_LOCALLY_DO_NOT_COMMIT；这是占位符，不能当作生产密码使用。
真实配置通过环境变量或独立配置文件提供，禁止提交数据库、令牌、采集记录和运行数据。
模型、厂商二进制库、编译器和SDK不保证由本仓库完全提供，使用前按项目原说明配置。
已有第三方许可文件保留，不将第三方代码宣称为公司自有。

## 检查范围

`python tools/check_repository.py`检查自有Python语法、JSON、Keil工程XML及受限文件类型，不导入业务代码。
它不等同于ROS构建、固件编译、依赖安装、接口联调或实车验收；本次未执行这些硬件/业务操作。

---

# 2026-10-17 本次变更记录

## 一、版本管理建立（可回退）

线上 `/opt/greenhouse` 此前不是 git 仓库，代码无法回溯。现已建立：

```bash
cd /opt/greenhouse
git log --oneline        # 提交历史
git tag -l               # 版本标签
git status               # 当前改动
```

| 标签 | 说明 |
|---|---|
| `v1.0.0-baseline` | 2026-10-17 线上运行基线（改动前的原始快照） |
| `v1.1.0-security` | 安全加固 + 前端部署 + 备份 |

**回退方法**（示例：回退到基线）：

```bash
cd /opt/greenhouse
git checkout v1.0.0-baseline -- app/     # 只回退后端代码
sudo systemctl restart greenhouse
# 或整体回退
git reset --hard v1.0.0-baseline && sudo systemctl restart greenhouse
```

数据库回退：

```bash
ls /opt/greenhouse/backups/              # 每日 03:00 自动备份，保留 14 天
gunzip -c greenhouse-YYYYMMDD-HHMMSS.sql.gz | sudo -u postgres psql greenhouse
```

## 二、本次改动内容

| 文件 | 改动 |
|---|---|
| `backend/app/api/__init__.py` | 统一路由注册；14 个业务路由模块强制登录鉴权 |
| `backend/app/api/auth.py` | `/auth/users`(GET/POST) 补鉴权（此前可匿名拉取全部用户） |
| `backend/app/mqtt/mqtt_service.py` | `dtu_receipts` → `device_receipts`（表不存在导致机器人数据静默丢失） |
| `frontend/src/services/api.js` | WebSocket 带 `?token=`（后端 WS 已要求鉴权） |
| `deploy/nginx/greenhouse.conf` | 前端静态托管 + API 反代 + WS 反代；`/docs`、`/health` 仅本机 |
| `deploy/emqx/acl.conf` | 撤销 `{allow, all}`；改为默认拒绝，设备仅能发布自己上行、订阅自己下行 |
| `deploy/emqx/emqx.conf` | 启用 file ACL 授权源（`no_match = deny`） |
| `deploy/scripts/backup_db.sh` | 数据库备份（含完整性校验，保留 14 天） |
| `.gitignore` | **新增**。此前仓库无 .gitignore，存在误提交 .env 的风险 |

## 三、部署状态（42.193.104.148）

| 项 | 状态 |
|---|---|
| 后端 `greenhouse.service` | active，监听 127.0.0.1:8000（经 nginx 反代，不直接暴露） |
| 前端 | `/var/www/greenhouse`，nginx 80 端口托管，公网可访问 |
| nginx | 已安装并开机自启 |
| EMQX | ACL 已收紧，本机云端服务放行 |
| 数据库备份 | 每日 03:00，输出至 `/opt/greenhouse/backups/` |

## 四、管理员账号

初始化管理员已创建（`SUPER_ADMIN`）。`/auth/init-admin` 接口在已有用户后自动关闭。
**请首次登录后立即修改密码。**

## 五、已验证项

- 无 token 访问 7 个业务接口 → 全部 401
- 带有效 token → 200；错误 token → 401
- 公网 `/docs`、`/health` → 403（仅本机可访问）
- JY001 MQTT 上报 → 落库 → `upload_ack` 正常
- 前端首页、静态资源 → 200

## 六、待办（本次未做）

- [ ] 将 GitHub 仓库改为 Private
- [ ] Alembic 接管数据库迁移（当前 `create_all` 无法加字段/改约束）
- [ ] HTTPS（当前为 HTTP 明文）
- [ ] 固件侧 3 个 P0（见评估报告）：`FS_Recycle` 丢数据、`FS_FIXED_DIV1` 光照溢出、无效值未输出 null
- [ ] 契约 §13 自动化回归测试
