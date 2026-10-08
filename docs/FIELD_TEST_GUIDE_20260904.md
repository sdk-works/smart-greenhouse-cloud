# 下午现场测试使用说明（2026-09-04）

## 当前运行状态

- 工控机服务：`agri-robot.service`，已启用开机自启动及异常自动重启。
- 旧`robot_core.service`保持禁用，避免重复占用串口。
- COM1 `/dev/ttyS0`：DTU，RS232，9600/8N1。
- COM3 `/dev/ttyS2`：土壤地址1、环境地址2，RS485，9600/8N1。
- COM4 `/dev/ttyS3`：导航到站报文，RS485，9600/8N1；默认不回写。
- 虚拟机greenhouse、EMQX、PostgreSQL、Redis、Nginx及手机隧道均已自启动。
- 工控机和虚拟机NTP已同步；Windows交流供电时睡眠时间为“从不”。

## 手机访问

- 临时HTTPS地址：`https://dose-persistent-steal-lindsay.trycloudflare.com`
- 第一层访问账号：`field`
- 第一层访问密码：`JYfield-2026-9Qm7`
- 通过第一层认证后，再使用网站自己的管理员账号登录。
- 此地址为Cloudflare免费Quick Tunnel，只用于今天的现场测试；虚拟机或隧道重启后地址可能改变，不保证长期在线。
- 未认证访问返回401；认证后首页和遥测API均已实测返回200。

## 状态心跳

工控机空闲时约每60秒上传一次真实环境数据，不上传离土土壤数据。`robot_status`包含：

- `status`：idle、sample、upload_pending等程序状态。
- `sensor_online`：本次环境传感器读取是否成功。
- `navigation_port_open`：只表示COM4成功打开，不等于厂家导航一定在线。
- `pending_uploads`：本地已保存、尚未获得云端确认的任务数量。
- `fault_tasks`：失败任务累计数量。
- `arm_enabled`：当前为false，机械臂未接入。

## 当前点位

当前只配置测试点1：`arr01|`和`arr1|`都会规范化为点位`1`，名称`测试点01`，默认采集10秒。同一点2秒内重复报文被抑制；以后再次到达仍会产生新任务。

## 常用检查

```bash
~/bin/field_status
sudo systemctl status agri-robot.service
journalctl -u agri-robot.service -f
sudo systemctl restart agri-robot.service
sudo systemctl stop agri-robot.service
```

## 当前JSON契约

- `type`: telemetry
- `schema_version`: 1.0
- `msg_id`: 每次消息唯一，用于云端幂等去重。
- `robot_id`: ROB001
- `task_id`: 到站任务或STATUS心跳标识。
- `timestamp`: UTC ISO-8601，由NTP同步时钟产生。
- `data`: 温湿度、CO2、光照和正式采集时的土壤七参数。
- `robot_status`: 程序与队列状态。
- `data_quality`: 正式采集的数据来源、样本数和探头模式。
- DTU外层`dtu-v1`包含SHA-256，云端持久化成功后才返回ACK。

## 已知限制

1. 厂家`arrN|`报文没有CRC、唯一任务号、采集时长和确认机制；单次发送若在物理链路上丢失，工控机无法主动恢复。建议后续升级为带任务号、时长、校验及ACK重发的正式协议。
2. 当前网站能展示环境数据和心跳，但页面未必直接展示`pending_uploads/fault_tasks`所有扩展字段，API原始记录中可见。
3. 当前没有BMS电量、导航坐标、RTK质量、IMU状态和信号强度字段。
4. 海康相机当前未连接，程序启动时会跳过拍照；运行后再热插相机不会自动重新初始化，需要重启服务。
5. 土壤探头离土时的0值和pH 3.0无业务意义；进棚后必须插入介质并等待稳定。
6. 状态心跳用于在线观察，不进入本地持久化队列；正式采集任务会持久化并补传。
7. Quick Tunnel适合临时测试，不是正式生产公网方案。长期部署应使用固定域名、正式Cloudflare Tunnel或新增花生壳HTTP映射，并更换已在聊天/截图中出现过的账号密码。
