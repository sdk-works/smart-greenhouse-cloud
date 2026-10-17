import React, { useState, useEffect } from 'react';
import {
  Tabs, Form, Input, InputNumber, Button, message,
  Alert, Row, Col, Divider, Switch,
} from 'antd';
import { SaveOutlined, WifiOutlined, SettingOutlined, WarningOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { settingsApi } from '../services/api';

const C = {
  teal:'#00D9A5', tealDk:'#00A87A', yellow:'#F5E663',
  border:'rgba(0,217,165,0.18)', muted:'#8aab9e', text:'#2d4a3e',
};

function GlassCard({ children, style = {} }) {
  return (
    <div style={{
      background:'rgba(255,255,255,0.68)', backdropFilter:'blur(14px)',
      border:`1px solid ${C.border}`, borderRadius:12,
      position:'relative', overflow:'hidden',
      boxShadow:'0 4px 20px rgba(0,100,70,0.06)',
      padding:20, ...style,
    }}>
      <div style={{ position:'absolute', top:0, left:'10%', right:'10%', height:1, background:`linear-gradient(90deg,transparent,${C.teal}55,transparent)` }} />
      {children}
    </div>
  );
}

function SectionTitle({ icon, children, color }) {
  return (
    <div style={{ fontSize:13, color: color || C.tealDk, fontWeight:700, marginBottom:14, display:'flex', alignItems:'center', gap:6 }}>
      {icon}{children}
    </div>
  );
}

// ── MQTT 配置 ─────────────────────────────────────────────────
function MqttSettings() {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    settingsApi.getMqttConfig()
      .then(r => form.setFieldsValue({ ...r.data, password:'' }))
      .catch(() => {});
  }, []);

  const save = async (v) => {
    setSaving(true);
    try {
      const res = await settingsApi.updateMqttConfig(v);
      message.success(res.data.message || 'MQTT 配置已保存');
      message.info('请执行：sudo systemctl restart greenhouse', 5);
    } catch (e) {
      message.error(e.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  return (
    <GlassCard>
      <SectionTitle icon={<WifiOutlined />}>MQTT Broker 配置</SectionTitle>

      <Alert type="warning" showIcon style={{ marginBottom:16, borderRadius:8 }}
        message="修改后需重启后端服务：sudo systemctl restart greenhouse"
      />

      {/* V8 Topic 说明 */}
      <div style={{ marginBottom:16, padding:'12px 14px', background:'rgba(0,217,165,0.04)', borderRadius:8, border:`1px solid rgba(0,217,165,0.12)` }}>
        <div style={{ fontSize:12, color:C.tealDk, fontWeight:700, marginBottom:8 }}>📡 V8 Topic 规范</div>
        <div style={{ fontSize:11, color:C.muted, lineHeight:2 }}>
          <div>上行（遥测）：<code style={{ color:C.tealDk }}>agri/{'{greenhouse_id}'}/{'{device_id}'}/telemetry</code></div>
          <div>下行（指令）：<code style={{ color:C.tealDk }}>agri/{'{greenhouse_id}'}/{'{device_id}'}/command</code></div>
          <div>心跳走 telemetry（type=heartbeat），60秒一次，已删除 status topic</div>
        </div>
      </div>

      <Form form={form} layout="vertical" onFinish={save} style={{ maxWidth:500 }}>
        <Row gutter={16}>
          <Col span={16}>
            <Form.Item name="broker_host" label="Broker 地址" rules={[{ required:true }]}>
              <Input placeholder="localhost 或 服务器IP" />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="broker_port" label="端口">
              <InputNumber style={{ width:'100%' }} min={1} max={65535} />
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="username" label="用户名"><Input placeholder="admin" /></Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="password" label="密码（留空保持不变）">
              <Input.Password placeholder="••••••" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="topic_prefix" label="Topic 前缀">
          <Input placeholder="agri" addonAfter="/{greenhouse_id}/{device_id}/telemetry" />
        </Form.Item>
        <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
          保存 MQTT 配置
        </Button>
      </Form>
    </GlassCard>
  );
}

// ── 系统告警阈值 ──────────────────────────────────────────────
function SystemParams() {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    settingsApi.getSystemConfig()
      .then(r => form.setFieldsValue(r.data))
      .catch(() => {});
  }, []);

  const save = async (v) => {
    setSaving(true);
    try { await settingsApi.updateSystemConfig(v); message.success('✅ 系统参数已保存'); }
    catch { message.error('保存失败'); }
    finally { setSaving(false); }
  };

  return (
    <GlassCard>
      <SectionTitle icon={<WarningOutlined />} color="#FF7043">告警阈值配置</SectionTitle>
      <Alert type="info" showIcon style={{ marginBottom:16, borderRadius:8 }}
        message="当环境数据超出阈值时，顶部导航栏会出现告警提示（🔔红色数字）"
      />
      <Form form={form} layout="vertical" onFinish={save} style={{ maxWidth:520 }}>
        <div style={{ background:'rgba(255,107,67,0.04)', border:'1px solid rgba(255,107,67,0.12)', borderRadius:8, padding:'14px 14px 2px', marginBottom:14 }}>
          <div style={{ fontSize:12, color:'#FF7043', fontWeight:700, marginBottom:10 }}>🌡️ 温度告警</div>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="alert_temperature_max" label="最高温度（°C）">
                <InputNumber style={{ width:'100%' }} addonAfter="°C" min={20} max={60} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="alert_temperature_min" label="最低温度（°C）">
                <InputNumber style={{ width:'100%' }} addonAfter="°C" min={-10} max={20} />
              </Form.Item>
            </Col>
          </Row>
        </div>

        <div style={{ background:'rgba(79,195,247,0.04)', border:'1px solid rgba(79,195,247,0.15)', borderRadius:8, padding:'14px 14px 2px', marginBottom:14 }}>
          <div style={{ fontSize:12, color:'#4FC3F7', fontWeight:700, marginBottom:10 }}>💧 湿度 & CO₂ 告警</div>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="alert_humidity_max" label="最高湿度（%）">
                <InputNumber style={{ width:'100%' }} addonAfter="%" min={50} max={100} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="alert_co2_max" label="CO₂ 超标（ppm）">
                <InputNumber style={{ width:'100%' }} addonAfter="ppm" min={500} max={5000} />
              </Form.Item>
            </Col>
          </Row>
        </div>

        <Divider style={{ borderColor:'rgba(0,217,165,0.12)' }} />

        <SectionTitle icon={<SettingOutlined />}>系统基础参数</SectionTitle>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="data_retention_days" label="数据保留天数">
              <InputNumber style={{ width:'100%' }} addonAfter="天" min={7} max={365} />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="upload_max_size_mb" label="图片上传限制（MB）">
              <InputNumber style={{ width:'100%' }} addonAfter="MB" min={1} max={50} />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="platform_name" label="平台名称">
          <Input placeholder="智慧温室巡检机器人平台" />
        </Form.Item>

        <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
          保存系统参数
        </Button>
      </Form>
    </GlassCard>
  );
}

// ── 关于系统 ──────────────────────────────────────────────────
function AboutSystem() {
  const infos = [
    { label:'平台版本',    value:'v8.0.0 · 农业清新版' },
    { label:'技术栈',      value:'FastAPI + PostgreSQL + Redis + EMQX + React' },
    { label:'MQTT 库',     value:'aiomqtt 1.2.1 / Python 3.10' },
    { label:'Topic 格式',  value:'agri/{gh_id}/{dev_id}/telemetry' },
    { label:'时间标准',    value:'全链路 UTC (Z 后缀) → 前端转本地显示' },
    { label:'图片上传',    value:'HTTP POST（非MQTT），支持断点续传/批量' },
    { label:'心跳机制',    value:'telemetry type=heartbeat，60秒间隔' },
    { label:'动态订阅',    value:'注册设备后自动订阅，无需重启' },
  ];

  return (
    <GlassCard>
      <SectionTitle icon={<CheckCircleOutlined />} color={C.tealDk}>关于系统</SectionTitle>
      <div style={{ display:'grid', gap:8 }}>
        {infos.map(i => (
          <div key={i.label} style={{ display:'flex', alignItems:'flex-start', padding:'8px 12px', background:'rgba(0,217,165,0.03)', borderRadius:8, border:`1px solid rgba(0,217,165,0.08)` }}>
            <span style={{ fontSize:12, color:C.muted, width:100, flexShrink:0 }}>{i.label}</span>
            <span style={{ fontSize:12, color:C.text, fontWeight:600 }}>{i.value}</span>
          </div>
        ))}
      </div>
      <div style={{ marginTop:14, padding:'10px 12px', background:'rgba(245,230,99,0.08)', borderRadius:8, border:'1px solid rgba(245,230,99,0.25)', fontSize:11, color:'#8a7a30' }}>
        💡 快速查看 API 文档：<code style={{ color:C.tealDk }}>http://服务器IP:8000/docs</code>
      </div>
    </GlassCard>
  );
}

// ── 主页面 ────────────────────────────────────────────────────
export default function SystemSettings() {
  return (
    <div className="fade-in">
      <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:16 }}>
        <div style={{ width:3, height:18, background:C.teal, borderRadius:2 }} />
        <span style={{ color:C.text, fontSize:15, fontWeight:700 }}>系统配置</span>
      </div>
      <Tabs
        type="card"
        items={[
          { key:'mqtt',  label:<span style={{ display:'flex', alignItems:'center', gap:5 }}><WifiOutlined />MQTT 配置</span>,  children:<MqttSettings /> },
          { key:'sys',   label:<span style={{ display:'flex', alignItems:'center', gap:5 }}><WarningOutlined />告警 & 参数</span>, children:<SystemParams /> },
          { key:'about', label:<span style={{ display:'flex', alignItems:'center', gap:5 }}><CheckCircleOutlined />关于系统</span>, children:<AboutSystem /> },
        ]}
      />
    </div>
  );
}
