import React, { useState, useEffect, useRef } from 'react';
import { Row, Col, Card, Select, Badge, Space, Typography, Progress, Tag, Alert } from 'antd';
import { RobotOutlined, ReloadOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { greenhouseApi, deviceApi, createWebSocket } from '../services/api';
import { formatUTCtoLocal } from '../services/api';

const { Title, Text } = Typography;
const { Option } = Select;

export default function GreenhouseMap() {
  const [greenhouses, setGreenhouses]   = useState([]);
  const [devices, setDevices]           = useState([]);
  const [selectedGh, setSelectedGh]     = useState(null);
  const [selectedDevice, setSelectedDevice] = useState(null);
  const [robotPath, setRobotPath]       = useState([]);
  const [liveData, setLiveData]         = useState(null);
  const [lastUpdateTime, setLastUpdateTime] = useState(null);
  const [mapConfig, setMapConfig]       = useState({ width: 100, height: 60, waypoints: [] });
  const wsRef = useRef(null);

  useEffect(() => {
    greenhouseApi.list().then(r => {
      setGreenhouses(r.data);
      if (r.data.length > 0) selectGreenhouse(r.data[0].id);
    });
    wsRef.current = createWebSocket((data) => {
      if (data.robot_status?.position) {
        const { x, y } = data.robot_status.position;
        setRobotPath(prev => [...prev.slice(-100), [x, y]]);
        setLiveData(data);
        // 使用 payload 中的 timestamp（UTC）转为本地时间显示
        setLastUpdateTime(data.timestamp ? formatUTCtoLocal(data.timestamp) : null);
      }
    });
    return () => wsRef.current?.close();
  }, []);

  const selectGreenhouse = async (id) => {
    setSelectedGh(id);
    setRobotPath([]);
    const [devRes, ghRes] = await Promise.all([
      deviceApi.list({ greenhouse_id: id }),
      greenhouseApi.get(id),
    ]);
    setDevices(devRes.data);
    if (ghRes.data.map_data) {
      setMapConfig({
        width: ghRes.data.map_data.width || 100,
        height: ghRes.data.map_data.height || 60,
        waypoints: ghRes.data.map_data.waypoints || [],
      });
    }
    const robots = devRes.data.filter(d => d.device_type === 'robot');
    if (robots.length > 0) setSelectedDevice(robots[0]);
  };

  const robotPos = liveData?.robot_status?.position;
  const mapOption = {
    backgroundColor: '#f6ffed',
    tooltip: { formatter: p => `(${p.data[0]?.toFixed(1)}, ${p.data[1]?.toFixed(1)})` },
    xAxis: {
      type: 'value', min: 0, max: mapConfig.width, name: '米',
      splitLine: { lineStyle: { color: '#d9f7be', type: 'dashed' } },
    },
    yAxis: {
      type: 'value', min: 0, max: mapConfig.height, name: '米',
      splitLine: { lineStyle: { color: '#d9f7be', type: 'dashed' } },
    },
    legend: { data: ['巡检路径','机器人','传感器','巡检点'], bottom: 0 },
    series: [
      {
        name: '巡检路径', type: 'line', symbol: 'none',
        data: robotPath.length > 0 ? robotPath : [],
        lineStyle: { color: '#52c41a', width: 2, type: 'dashed' },
      },
      {
        name: '机器人', type: 'scatter', symbolSize: 18,
        data: [[robotPos?.x ?? 30, robotPos?.y ?? 30]],
        itemStyle: { color: '#52c41a' },
        label: { show: true, formatter: '🤖', fontSize: 16, offset: [0, -22] },
      },
      {
        name: '传感器', type: 'scatter', symbolSize: 12,
        data: mapConfig.waypoints
          .filter(w => !w.actions || w.actions.includes('measure_env'))
          .map(w => [w.x, w.y]),
        itemStyle: { color: '#1890ff' },
        label: { show: true, formatter: '📡', fontSize: 12, offset: [0, -18] },
      },
      {
        name: '巡检点', type: 'scatter', symbolSize: 8,
        data: mapConfig.waypoints.map(w => [w.x, w.y]),
        itemStyle: { color: '#faad14', opacity: 0.7 },
      },
    ],
  };

  const envData = liveData?.data || {};
  const robotStatus = liveData?.robot_status || {};
  const envItems = [
    { label: '温度',   value: envData.temperature  != null ? `${envData.temperature.toFixed(1)}°C`  : '-', color: '#ff7875' },
    { label: '湿度',   value: envData.humidity      != null ? `${envData.humidity.toFixed(1)}%`      : '-', color: '#40a9ff' },
    { label: '土壤湿度', value: envData.soil_moisture != null ? `${envData.soil_moisture.toFixed(1)}%` : '-', color: '#36cfc9' },
    { label: 'CO₂',  value: envData.co2            != null ? `${envData.co2.toFixed(0)} ppm`        : '-', color: '#9254de' },
    { label: '光照',  value: envData.light          != null ? `${envData.light.toFixed(0)} lux`      : '-', color: '#faad14' },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Row align="middle" justify="space-between" style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>🗺️ 机器人巡检地图</Title>
        <Space>
          <Select placeholder="选择大棚" style={{ width: 160 }} value={selectedGh} onChange={selectGreenhouse}>
            {greenhouses.map(g => <Option key={g.id} value={g.id}>{g.name}</Option>)}
          </Select>
          <Select placeholder="选择机器人" style={{ width: 150 }} value={selectedDevice?.id}
            onChange={id => setSelectedDevice(devices.find(d => d.id === id))}>
            {devices.filter(d => d.device_type === 'robot').map(d => (
              <Option key={d.id} value={d.id}>
                <Badge status={d.is_online ? 'success' : 'default'} /> {d.name}
              </Option>
            ))}
          </Select>
        </Space>
      </Row>

      {lastUpdateTime && (
        <Alert
          type="success" showIcon closable style={{ marginBottom: 12, borderRadius: 8 }}
          message={`最后更新：${lastUpdateTime}（本地时间）`}
        />
      )}

      <Row gutter={16}>
        <Col xs={24} lg={17}>
          <Card
            title={<Space><RobotOutlined />大棚俯视地图</Space>}
            extra={<Badge status="processing" text="实时追踪" />}
            style={{ borderRadius: 10 }}
          >
            {mapConfig.waypoints.length === 0 && (
              <Alert type="info" message="大棚暂无点位配置，请前往【系统配置】→【大棚点位】添加巡检点" style={{ marginBottom: 8, borderRadius: 6 }} />
            )}
            <ReactECharts option={mapOption} style={{ height: 460 }} />
          </Card>
        </Col>

        <Col xs={24} lg={7}>
          <Card title="机器人状态" style={{ marginBottom: 16, borderRadius: 10 }}>
            {selectedDevice ? (
              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                {[
                  { label: '设备ID', value: <Text code style={{ fontSize: 11 }}>{selectedDevice.device_id}</Text> },
                  { label: '在线', value: <Badge status={selectedDevice.is_online ? 'success' : 'default'} text={selectedDevice.is_online ? '在线' : '离线'} /> },
                  { label: '位置', value: <Text>X:{robotPos?.x?.toFixed(1) ?? '-'} Y:{robotPos?.y?.toFixed(1) ?? '-'}</Text> },
                  { label: '速度', value: <Tag color="green">{robotStatus.speed ?? 0} m/s</Tag> },
                  { label: '状态', value: <Tag color={robotStatus.status === 'charging' ? 'orange' : 'green'}>{robotStatus.status ?? '-'}</Tag> },
                ].map(item => (
                  <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Text type="secondary">{item.label}</Text>
                    {item.value}
                  </div>
                ))}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Text type="secondary">电量</Text>
                    <Text>{robotStatus.battery ?? 0}%</Text>
                  </div>
                  <Progress
                    percent={robotStatus.battery ?? 0}
                    strokeColor={{ '0%': '#faad14', '100%': '#52c41a' }} size="small"
                  />
                </div>
              </Space>
            ) : <Text type="secondary">请选择机器人</Text>}
          </Card>

          <Card title="实时环境数据" style={{ borderRadius: 10 }}>
            <Space direction="vertical" style={{ width: '100%' }} size={8}>
              {envItems.map(item => (
                <div key={item.label} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '6px 10px', background: '#fafafa', borderRadius: 6,
                }}>
                  <Text type="secondary" style={{ fontSize: 13 }}>{item.label}</Text>
                  <Text strong style={{ color: item.value !== '-' ? item.color : '#ccc' }}>{item.value}</Text>
                </div>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
