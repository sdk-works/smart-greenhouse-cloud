# 版本盘点（2026-08-24）

## 唯一主线

`/home/user/projects/smart-greenhouse-cloud`

该目录由 V10 完整源码作为骨架，并合入 2026-08-24 正在运行的 `/opt/greenhouse` 后端代码。以后所有开发从这里进行。

## 当前线上环境

- 后端：`/opt/greenhouse`
- 前端：`/var/www/greenhouse`
- 服务：FastAPI、Nginx、PostgreSQL、Redis、EMQX、TCP 图片服务
- 快照：`/home/user/projects/archives/smart-greenhouse-online-20260824`

快照包含线上后端、已部署前端和部署配置。配置目录权限受限，其中的环境变量不得外发或提交 Git。

## 历史目录

- `/home/user/greenhouse-website`：早期 Node.js 网站。
- `/home/user/all_smart_greenhouse/greenhouse-website`：另一条网站开发分支。
- `/home/user/all_smart_greenhouse/smart-greenhouse_v3`：历史 V3。
- `/home/user/all_smart_greenhouse/smart-greenhouse-v5`：历史 V5。
- `/home/user/all_smart_greenhouse/smart-greenhouse-v7`：历史 V7。
- `/home/user/all_smart_greenhouse/smart-greenhouse-v8`：历史 V8。
- `/home/user/all_smart_greenhouse/smart-greenhouse-v9`：历史 V9。
- `/home/user/smart-greenhouse-v10`：合并前的完整源码来源。
- `/home/user/robot_ws`：ROS/机器人端工程，不属于云端工程。

以上目录暂不删除、不覆盖、不继续开发。

## 下一步修改顺序

1. 输出云端与新工控机 MQTT 报文对照表。
2. 固定机器人、导航点、采集任务和完成确认的状态机。
3. 扩展土壤水分、温度、EC、pH、氮、磷、钾字段。
4. 编写数据库迁移与兼容旧数据方案。
5. 使用模拟报文完成后端联调。
6. 再修改工控机端程序并进行端到端测试。

## 禁止事项

- 不直接用 V10 覆盖 `/opt/greenhouse`。
- 不在部署目录临时改完而不同步回 Git。
- 不把 `.env`、密码、Token 或数据库文件提交到仓库。
- 未验证前不删除任何历史目录。
