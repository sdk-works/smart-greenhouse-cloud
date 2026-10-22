# EMQX 设备认证启用步骤（**候选，未执行**）

> 2026-10-22 夜间状态：`/etc/emqx/emqx.conf` **没有任何 `authentication` 块**，
> 客户端可以匿名连接、也可以自称任意 clientid。ACL 已按 clientid 收紧，
> 能挡住"顺手试"和通配旁听，**挡不住主动冒充**。
> 本文件是启用步骤草稿，**没有执行、没有 restart 任何服务**。

## 1. 现状与差距

| 环节 | 现状 | 差距 |
|---|---|---|
| ACL | `/etc/emqx/acl.conf` 按 clientid 显式登记，末尾 `{deny, all}` | 只能约束已登记 clientid 的 publish/subscribe |
| 认证 | 无 `authentication` 配置，匿名可连 | 任何人可自称 `02501026081300004386` 顶掉真设备 |
| 凭据表 | `mqtt_credentials`（username PK / clientid UNIQUE / password_hash / enabled） | 生产已有 1 行（`dev-jy-dtu-000001`），authn 未启用前是惰性的 |

## 2. 本仓库提供的认证判定（已实现、已测）

`app/services/mqtt_credential_service.authenticate(username, password, clientid)`：

| 输入 | 结果 | 原因码 |
|---|---|---|
| 用户名 + 正确口令 + 登记的 clientid | allow | ok |
| clientid 与登记值不一致 | deny | `clientid_mismatch` |
| `enabled=false` | deny | `credential_disabled` |
| 口令错误 | deny | `bad_password` |
| 用户名不存在 / 空 | deny | `no_such_username` |

HTTP 钩子：`POST /api/v1/emqx/authn`，请求体 `{"username","password","clientid"}`，
响应 `{"result":"allow"|"deny"}`。**Fail-closed**：未设置 `EMQX_AUTHN_TOKEN` 时端点直接 503。

测试覆盖：`tests/test_device_onboarding.py::test_emqx_authn_hook_fail_closed`
（未配置→503 / 正确→allow / 错口令→deny / 错 token→deny / 停用→deny）。

⚠️ 注意例外：业务 API 统一强制登录，但 `/emqx/authn` **不在鉴权白名单内**
（broker 拿不到业务 token），改由共享密钥保护。上线前需要确认
nginx 不要把 `/api/v1/emqx` 暴露到公网。

## 3. 启用步骤（需要人工执行，且必须在设备侧同步改固件凭据之后）

前置：**先改设备固件里的 MQTT 用户名/口令**，否则一启用认证，现有设备全部离线。

1. 为每个设备登记凭据：
   `POST /api/v1/devices/{device_id}/credential  {asset_no, clientid}`
   → 返回的 `password` 只在这一次出现，打印安装清单交给现场。
2. 在 `.env` 里设置 `EMQX_AUTHN_TOKEN=<随机长串>`。
3. `/etc/emqx/emqx.conf` 增加（示意，按 EMQX 5.x 语法，实际以现场版本为准）：
   ```
   authentication {
     mechanism = password_based
     backend = http
     enable = true
     method { type = post; url = "http://127.0.0.1:8000/api/v1/emqx/authn"; content_type = json }
     body { username = "${username}"; password = "${password}"; clientid = "${clientid}" }
     headers { X-Authn-Token = "<上面那个 token>" }
   }
   ```
4. ACL 从 `%c` 换成 `%u`（可选，按用户名约束，便于后续换 clientid 不改 ACL）。
5. 重启 EMQX：**这一步属于生产变更，本次夜间未做**。
6. 验证：用 `mqttx` 以正确/错误/停用三种凭据连一次，确认只有正确的能连上；
   再看服务日志里遥测是否仍在按 60s 节奏入库、ACK 正常。

## 4. 回滚

删掉 `emqx.conf` 里新增的 `authentication` 块并重启 EMQX，即回到匿名 + ACL 现状
（`acl.conf.bak-20261022-094030` 是 ACL 收紧前的备份，与本次认证改动无关）。
凭据表里的行保留即可，不影响回填与展示。
