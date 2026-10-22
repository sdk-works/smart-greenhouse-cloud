/**
 * GreenhouseStatusPanel.js —— 大棚「设备与数据状态」面板（T4）
 *
 * 用法（放进现有大棚详情页，不重做全站仪表盘）：
 *   1. 落到前端工程的 `src/pages/GreenhouseStatusPanel.js`
 *   2. 在 GreenhouseWorkbench 里加一个 Tab：
 *        import GreenhouseStatusPanel from './GreenhouseStatusPanel';
 *        <Tabs.TabPane tab="设备与数据状态" key="status">
 *          <GreenhouseStatusPanel greenhouseId={greenhouseId}/>
 *        </Tabs.TabPane>
 *   3. 数据来自 GET /api/v1/greenhouses/{id}/status?max_age_seconds=N
 *      （接口已经按公司隔离：越权返回 404，页面直接提示，不做兜底猜测）
 *
 * 刻意遵守的几条：
 *   - 「MQTT 已连接」与「最近收到有效数据」分两栏显示，不合并成"在线/离线"
 *   - 过期数据只显示「数据已过期」，不再把旧数值当实时值渲染（用 display_value）
 *   - 补传数据只在"历史"里出现，不进当前值
 *   - 归属分可信/推定/待定/未判定四种，标签不同，不混成一种正常状态
 *   - 拿不到依据的一律显示"未知"，不猜
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Card, Empty, Select, Space, Table, Tag, Tooltip, Typography } from 'antd';
import api, { formatUTCtoLocal as time } from '../services/api';

const STATE = {
  ok: ['green', '正常'],
  expired: ['orange', '数据已过期'],
  missing: ['default', '本次未上报'],
  out_of_range: ['red', '超出合理区间'],
};
const ATTR = {
  trusted: ['green', '可信'],
  inferred: ['orange', '推定'],
  pending: ['blue', '待定'],
  unknown: ['default', '未判定'],
};
const LEVEL = { error: ['error', '错误'], warning: ['warning', '警告'], info: ['info', '提示'] };
const AGE_OPTIONS = [
  { value: 300, label: '5 分钟' },
  { value: 900, label: '15 分钟' },
  { value: 3600, label: '1 小时' },
];

export default function GreenhouseStatusPanel({ greenhouseId, maxAgeSeconds = 900 }) {
  const [age, setAge] = useState(maxAgeSeconds);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const current = useRef(greenhouseId);
  current.current = greenhouseId;

  const load = useCallback(async () => {
    try {
      const r = await api.get(`/greenhouses/${greenhouseId}/status`, { params: { max_age_seconds: age } });
      if (current.current !== greenhouseId) return;
      setData(r.data);
      setError('');
    } catch (e) {
      const status = e?.response?.status;
      setData(null);
      setError(status === 404
        ? '这个大棚不存在，或不属于你所在公司（接口按公司隔离）'
        : '加载失败，请检查连接后重新加载');
    }
  }, [greenhouseId, age]);

  useEffect(() => { setData(null); load(); }, [load]);
  useEffect(() => {
    if (!greenhouseId) return undefined;
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [greenhouseId, load]);

  if (error) return <Alert type="error" showIcon message={error} />;
  if (!data) return <Card loading />;

  const s = data.summary || {}, a = s.attribution || {};

  const deviceCols = [
    { title: '设备', dataIndex: 'device_id', render: (v, r) => <>{v}<br /><Typography.Text type="secondary">{r.name}</Typography.Text></> },
    { title: 'MQTT 已连接', dataIndex: ['mqtt', 'connected'], render: v => <Tag color={v ? 'green' : 'red'}>{v ? '在线' : '离线'}</Tag> },
    { title: '连接判定来源', dataIndex: ['mqtt', 'source'], render: (v, r) => <Typography.Text type="secondary">{v}<br />最后上线 {time(r.mqtt.last_seen)}</Typography.Text> },
    {
      title: '最近有效数据', dataIndex: ['data', 'last_valid_at'],
      render: (v, r) => r.data.has_data
        ? <>{time(v)}<br /><Typography.Text type="secondary">基准 {r.data.basis}</Typography.Text></>
        : <Tag>无数据</Tag>,
    },
    {
      title: '数据是否新鲜', dataIndex: ['data', 'within_max_age'],
      render: (v, r) => !r.data.has_data
        ? <Tag>无数据</Tag>
        : (v ? <Tag color="green">新鲜 {r.data.age_seconds}s</Tag> : <Tag color="orange">{r.data.freshness?.state || '已过期'} {r.data.age_seconds}s</Tag>),
    },
    { title: '补传条数', dataIndex: 'backfilled_rows', render: v => v || 0 },
    {
      title: '数据归属', dataIndex: ['attribution', 'grade'],
      render: (v, r) => {
        const [color, label] = ATTR[v] || ATTR.unknown;
        return <Tooltip title={r.attribution?.detail}><Tag color={color}>{label}</Tag></Tooltip>;
      },
    },
    {
      title: '凭据', dataIndex: ['credential', 'registered'],
      render: (v, r) => !v ? <Tag>未登记</Tag> : (r.credential.enabled ? <Tag color="green">已登记/启用</Tag> : <Tag color="red">已停用</Tag>),
    },
  ];

  const metricCols = [
    { title: '指标', dataIndex: 'name', render: (v, r) => <>{v}<br /><Typography.Text type="secondary">{r.code} · {r.device_id}</Typography.Text></> },
    {
      title: '当前值', dataIndex: 'display_value',
      render: (v, r) => {
        if (r.state === 'expired') return <Typography.Text type="secondary" italic>{r.value}（数据已过期）</Typography.Text>;
        if (r.state === 'missing') return <Typography.Text type="secondary">—（本次未上报）</Typography.Text>;
        const digits = r.precision ?? 1;
        return <b>{typeof v === 'number' ? v.toFixed(digits) : v} {r.unit}</b>;
      },
    },
    {
      title: '数值状态', dataIndex: 'state',
      render: (v, r) => { const [c, l] = STATE[v] || STATE.ok; return <><Tag color={c}>{l}</Tag><br /><Typography.Text type="secondary">{r.message}</Typography.Text></>; },
    },
    { title: '最后更新', dataIndex: 'last_updated_at', render: (v, r) => <>{time(v)}<br /><Typography.Text type="secondary">距今 {r.age_seconds ?? '—'} 秒</Typography.Text></> },
    {
      title: '数据归属', dataIndex: ['attribution', 'grade'],
      render: v => { const [c, l] = ATTR[v] || ATTR.unknown; return <Tag color={c}>{l}</Tag>; },
    },
    {
      title: '设备自检', dataIndex: 'device_reports_channel_invalid',
      render: v => v
        ? <Tooltip title="设备自报有通道未通过自检；位序与指标的对应关系未在契约里定义，不指明是哪一路"><Tag color="orange">有通道未通过</Tag></Tooltip>
        : <Tag color="green">全部通过</Tag>,
    },
    {
      title: '历史（含补传）', dataIndex: 'history',
      render: v => (v || []).length
        ? <ul style={{ margin: 0, paddingLeft: 16 }}>{v.map((h, i) => <li key={i}>{time(h.basis_at)} <Typography.Text code>{h.msg_id}</Typography.Text>{h.is_backfill && <Tag color="blue">补传</Tag>}</li>)}</ul>
        : <Typography.Text type="secondary">无</Typography.Text>,
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Card size="small" title="大棚设备与数据状态" extra={
        <Space>
          <span>过期阈值</span>
          <Select size="small" value={age} options={AGE_OPTIONS} onChange={setAge} style={{ width: 110 }} />
        </Space>}>
        <Typography.Text type="secondary">
          快照 {time(data.as_of)} · 设备 {s.devices_total} 台（MQTT 在线 {s.devices_mqtt_online}，
          数据有效 {s.devices_data_fresh}）· 指标正常 {s.metrics_ok}/{s.metrics_total} ·
          归属 可信 {a.trusted ?? 0} / 推定 {a.inferred ?? 0} / 待定 {a.pending ?? 0} / 未判定 {a.unknown ?? 0}
        </Typography.Text>
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          「MQTT 已连接」只说明链路在，不代表数据在来；是否有效请看右侧三列。
        </Typography.Paragraph>
      </Card>

      <Card size="small" title={`异常提示（${s.anomalies_total ?? 0} 条，含 ${s.errors ?? 0} 条错误）`}>
        {(data.anomalies || []).length
          ? <ul style={{ margin: 0, paddingLeft: 18 }}>
              {data.anomalies.map((x, i) => {
                const [type, word] = LEVEL[x.level] || LEVEL.info;
                return <li key={i}><Tag color={type}>{word}</Tag><b>{x.code}</b>：{x.message}</li>;
              })}
            </ul>
          : <Empty description="没有检测到异常" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
      </Card>

      <Card size="small" title="设备：连接状态 与 数据状态">
        <Table size="small" rowKey="device_id" pagination={false}
               dataSource={data.devices || []} columns={deviceCols} />
      </Card>

      <Card size="small" title="当前指标">
        <Table size="small" rowKey={(r) => `${r.device_id}:${r.code}`} pagination={false}
               dataSource={data.metrics || []} columns={metricCols} />
      </Card>
    </Space>
  );
}
