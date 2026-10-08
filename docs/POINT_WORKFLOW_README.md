# 导航点位发现、分配与采集协议（V1.0）

## 工作流程

1. 云端发布“发现模式”。工控机收到任意 `arrNN|` 后只上报导航编号，不采集传感器、不拍照。
2. 管理员在“采集点配置”中把编号分配到具体大棚和采集点，设置采集时长及环境、土壤、拍照开关。
3. 云端发布“正式采集模式”。工控机校验机器人编号、协议版本、配置版本、SHA-256、点位唯一性及参数范围后原子保存。
4. 正式模式只执行已分配、已启用点位。未知点仍会上报供后台发现，但不会误采集。

## 上行消息

点位发现使用 `type=point_discovered`，关键字段为 `robot_id`、`nav_point_code`、`timestamp`、`config_version`、`mode`、`msg_id`。消息先写入工控机 SQLite，再通过 DTU 重试；收到云端数据库提交后的 `upload_ack` 才标记完成。

配置应用结果使用 `type=config_ack`，`point_mapping_sync_status` 为 `synced` 或 `failed`，并携带版本、摘要和详细原因。

## 下行配置

配置使用 `type=point_config`、`schema_version=1.0`，包括 `robot_id`、单调递增的 `config_version`、`mode`、完整 `points` 数组、`issued_at` 和 `config_sha256`。MQTT 主题为 `agri/robot/{robot_id}/config`，QoS 1、retain=true。

每个点包含：`nav_point_code`、`greenhouse_id`、`point_name`、`sample_seconds(1..120)`、`collect_soil`、`collect_environment`、`take_photo`、`enabled`、`mapping_id` 和备注。

## 安全与容错

- 云端配置是全量快照；没有发布操作，后台编辑不会改变车端行为。
- 工控机拒绝错误机器人、错误摘要、旧版本、同版本不同内容、重复编号和越界采集时长。
- 新文件先写临时文件并 `fsync`，再原子替换；旧配置保存在配置目录的 `backups` 子目录。
- DTU 断线时发现事件、配置确认和采集结果都保存在 SQLite，恢复后自动补传。
- 导航当前是单向 RS485，工控机不会向导航总线发送回复。

## 现场使用顺序

先在云端点“发布发现模式”，再让导航厂家打点并让车辆依次触发。后台确认编号完整后逐项分配，最后点“发布正式采集模式”，等待状态显示 `applied` 后再开始正式巡检。
