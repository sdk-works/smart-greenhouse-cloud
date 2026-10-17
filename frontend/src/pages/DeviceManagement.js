import React, { useState, useEffect } from 'react';
import {
  Table, Button, Modal, Form, Input, Select, Switch, InputNumber,
  Space, message, Popconfirm, Badge, Tag, Alert, Descriptions,
  Drawer, Tabs, List, Avatar,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, ReloadOutlined, WifiOutlined,
  CopyOutlined, RobotOutlined, EnvironmentOutlined, SettingOutlined,
} from '@ant-design/icons';
import { deviceApi, greenhouseApi } from '../services/api';
import { formatUTCtoLocal } from '../services/api';
import api from '../services/api';

const C = { teal:'#00D9A5', tealDk:'#00A87A', border:'rgba(0,217,165,0.18)', muted:'#8aab9e', text:'#2d4a3e' };

function GlassCard({ children }) {
  return (
    <div style={{ background:'rgba(255,255,255,0.72)', backdropFilter:'blur(12px)', border:`1px solid ${C.border}`, borderRadius:12, position:'relative', overflow:'hidden', boxShadow:'0 4px 20px rgba(0,100,70,0.06)' }}>
      <div style={{ position:'absolute', top:0, left:'15%', right:'15%', height:1, background:`linear-gradient(90deg,transparent,${C.teal}55,transparent)` }} />
      {children}
    </div>
  );
}

function TopicLine({ label, value }) {
  return (
    <div style={{ marginBottom:8 }}>
      <div style={{ fontSize:10, color:C.muted, marginBottom:2 }}>{label}</div>
      <div style={{ display:'flex', gap:6, alignItems:'center' }}>
        <code style={{ flex:1, fontSize:11, color:C.tealDk, background:'rgba(0,217,165,0.06)', padding:'4px 8px', borderRadius:6, border:`1px solid rgba(0,217,165,0.15)`, wordBreak:'break-all' }}>{value}</code>
        <CopyOutlined style={{ color:C.muted, cursor:'pointer', flexShrink:0 }} onClick={() => { navigator.clipboard?.writeText(value); message.success('已复制'); }} />
      </div>
    </div>
  );
}

// ── 多大棚巡逻配置子组件 ──────────────────────────────────────
function PatrolConfig({ device }) {
  const [config, setConfig]         = useState(null);
  const [greenhouses, setGreenhouses] = useState([]);
  const [loading, setLoading]       = useState(false);
  const [addModal, setAddModal]     = useState(false);
  const [paramModal, setParamModal] = useState(false);
  const [addForm] = Form.useForm();
  const [paramForm] = Form.useForm();

  useEffect(() => {
    if (device) { loadConfig(); loadGreenhouses(); }
  }, [device]);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const r = await api.get(`/devices/${device.id}/patrol-config`);
      setConfig(r.data);
      paramForm.setFieldsValue({
        patrol_speed: r.data.patrol_speed,
        capture_interval: r.data.capture_interval,
        return_battery_threshold: r.data.return_battery_threshold,
        auto_patrol: r.data.auto_patrol,
      });
    } catch { setConfig(null); }
    finally { setLoading(false); }
  };

  const loadGreenhouses = async () => {
    const r = await greenhouseApi.list();
    setGreenhouses(r.data);
  };

  const handleAddGreenhouse = async (values) => {
    try {
      const res = await api.post(`/devices/${device.id}/patrol-greenhouses`, values);
      message.success(res.data.message);
      addForm.resetFields();
      setAddModal(false);
      loadConfig();
    } catch (e) { message.error(e.response?.data?.detail || '添加失败'); }
  };

  const handleRemoveGreenhouse = async (ghId) => {
    try {
      await api.delete(`/devices/${device.id}/patrol-greenhouses/${ghId}`);
      message.success('已移除');
      loadConfig();
    } catch { message.error('移除失败'); }
  };

  const handleSaveParams = async (values) => {
    try {
      await api.put(`/devices/${device.id}/patrol-params`, null, { params: values });
      message.success('巡逻参数已保存');
      setParamModal(false);
      loadConfig();
    } catch { message.error('保存失败'); }
  };

  const alreadyAdded = (config?.patrol_greenhouses || []).map(pg => pg.greenhouse_id);
  const available = greenhouses.filter(g => !alreadyAdded.includes(g.id));

  return (
    <div>
      <Alert type="info" showIcon style={{ marginBottom:12, borderRadius:8 }}
        message="一个机器人可以配置多个大棚的巡逻任务，按优先级依次执行"
      />

      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
        <span style={{ fontSize:13, color:C.text, fontWeight:600 }}>已配置 {config?.patrol_greenhouses?.length || 0} 个巡逻大棚</span>
        <Space>
          <Button icon={<SettingOutlined />} onClick={() => setParamModal(true)} style={{ borderColor:C.border, color:C.muted }}>巡逻参数</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddModal(true)} disabled={available.length === 0}>
            添加大棚
          </Button>
        </Space>
      </div>

      {available.length === 0 && (config?.patrol_greenhouses?.length || 0) > 0 && (
        <Alert type="success" message="已将所有大棚加入巡逻列表" style={{ marginBottom:8, borderRadius:8 }} />
      )}

      <List
        loading={loading}
        dataSource={config?.patrol_greenhouses || []}
        locale={{ emptyText:<div style={{ padding:20, color:C.muted, textAlign:'center' }}>暂未配置巡逻大棚，点击"添加大棚"</div> }}
        renderItem={(pg, i) => (
          <div style={{
            display:'flex', justifyContent:'space-between', alignItems:'center',
            padding:'10px 14px', marginBottom:8,
            background: pg.enabled ? 'rgba(0,217,165,0.04)' : 'rgba(0,0,0,0.02)',
            borderRadius:10, border:`1px solid ${pg.enabled ? 'rgba(0,217,165,0.15)' : 'rgba(0,0,0,0.06)'}`,
          }}>
            <div style={{ display:'flex', alignItems:'center', gap:12 }}>
              <div style={{ width:28, height:28, borderRadius:8, background:`rgba(0,217,165,0.1)`, display:'flex', alignItems:'center', justifyContent:'center', fontSize:14, color:C.tealDk, fontWeight:700 }}>
                {pg.priority}
              </div>
              <div>
                <div style={{ fontSize:13, color:C.text, fontWeight:600 }}>{pg.greenhouse_name}</div>
                <div style={{ fontSize:11, color:C.muted }}>
                  {pg.greenhouse_crop || '—'} · {pg.waypoint_count} 个点位 · 每 {pg.repeat_interval_min} 分钟
                </div>
              </div>
            </div>
            <Space>
              <Tag style={{ borderRadius:20, fontSize:11, background: pg.enabled ? 'rgba(0,217,165,0.08)' : 'rgba(0,0,0,0.04)', border:`1px solid ${pg.enabled ? 'rgba(0,217,165,0.2)' : 'rgba(0,0,0,0.08)'}`, color: pg.enabled ? C.tealDk : '#bbb' }}>
                {pg.enabled ? '✅ 启用' : '⏸ 暂停'}
              </Tag>
              <Popconfirm title="移除此大棚的巡逻配置？" onConfirm={() => handleRemoveGreenhouse(pg.greenhouse_id)} okText="移除" cancelText="取消">
                <Button size="small" type="text" danger>移除</Button>
              </Popconfirm>
            </Space>
          </div>
        )}
      />

      {/* 巡逻参数 */}
      {config && (
        <div style={{ marginTop:12, padding:'10px 14px', background:'rgba(0,217,165,0.03)', borderRadius:8, border:`1px solid rgba(0,217,165,0.1)`, display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:8 }}>
          {[
            { label:'巡逻速度', value:`${config.patrol_speed} m/s` },
            { label:'拍照间隔', value:`每${config.capture_interval}个点位` },
            { label:'低电返回', value:`<${config.return_battery_threshold}%` },
            { label:'自动巡逻', value: config.auto_patrol ? '✅ 开启' : '⏸ 关闭' },
          ].map(s => (
            <div key={s.label} style={{ textAlign:'center' }}>
              <div style={{ fontSize:10, color:C.muted }}>{s.label}</div>
              <div style={{ fontSize:12, color:C.text, fontWeight:600 }}>{s.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* 添加大棚弹窗 */}
      <Modal title={<span style={{ color:C.tealDk }}>🏠 添加巡逻大棚</span>} open={addModal}
        onCancel={() => setAddModal(false)} onOk={() => addForm.submit()} okText="添加" width={400}>
        <Form form={addForm} layout="vertical" onFinish={handleAddGreenhouse} style={{ marginTop:12 }}>
          <Form.Item name="greenhouse_id" label="选择大棚" rules={[{ required:true }]}>
            <Select placeholder="选择要添加的大棚">
              {available.map(g => <Select.Option key={g.id} value={g.id}>{g.name}{g.crop_type ? ` · ${g.crop_type}` : ''}</Select.Option>)}
            </Select>
          </Form.Item>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
            <Form.Item name="priority" label="巡逻优先级（1=最高）" initialValue={1}>
              <InputNumber style={{ width:'100%' }} min={1} max={10} />
            </Form.Item>
            <Form.Item name="repeat_interval_min" label="巡逻间隔（分钟）" initialValue={60}>
              <InputNumber style={{ width:'100%' }} min={5} max={1440} />
            </Form.Item>
          </div>
          <Form.Item name="enabled" label="是否启用" valuePropName="checked" initialValue={true}>
            <Switch checkedChildren="启用" unCheckedChildren="暂停" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 巡逻参数弹窗 */}
      <Modal title={<span style={{ color:C.tealDk }}>⚙️ 巡逻参数</span>} open={paramModal}
        onCancel={() => setParamModal(false)} onOk={() => paramForm.submit()} okText="保存" width={400}>
        <Form form={paramForm} layout="vertical" onFinish={handleSaveParams} style={{ marginTop:12 }}>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
            <Form.Item name="patrol_speed" label="巡逻速度（m/s）">
              <InputNumber style={{ width:'100%' }} min={0.1} max={3.0} step={0.1} />
            </Form.Item>
            <Form.Item name="capture_interval" label="拍照间隔（点位数）">
              <InputNumber style={{ width:'100%' }} min={1} max={20} />
            </Form.Item>
            <Form.Item name="return_battery_threshold" label="低电返回阈值（%）">
              <InputNumber style={{ width:'100%' }} min={5} max={50} />
            </Form.Item>
          </div>
          <Form.Item name="auto_patrol" label="启用自动巡逻" valuePropName="checked">
            <Switch checkedChildren="开启" unCheckedChildren="关闭" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ── 主页面 ────────────────────────────────────────────────────
export default function DeviceManagement() {
  const [devices, setDevices]         = useState([]);
  const [greenhouses, setGreenhouses] = useState([]);
  const [loading, setLoading]         = useState(false);
  const [modalOpen, setModalOpen]     = useState(false);
  const [submitting, setSubmitting]   = useState(false);
  const [drawerOpen, setDrawerOpen]   = useState(false);
  const [selected, setSelected]       = useState(null);
  const [mqttTopics, setMqttTopics]   = useState(null);
  const [drawerTab, setDrawerTab]     = useState('mqtt');
  const [form] = Form.useForm();

  useEffect(() => { loadAll(); }, []);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [dr, gr] = await Promise.all([deviceApi.list({ device_type:'robot' }), greenhouseApi.list()]);
      setDevices(dr.data); setGreenhouses(gr.data);
    } finally { setLoading(false); }
  };

  const handleRegister = async (values) => {
    setSubmitting(true);
    try {
      const res = await deviceApi.register({ ...values, device_type:'robot' });
      message.success(`✅ 机器人 "${values.name}" 注册成功，MQTT 订阅已启动`);
      if (res.data.mqtt_topics) {
        setSelected(res.data); setMqttTopics(res.data.mqtt_topics); setDrawerTab('mqtt'); setDrawerOpen(true);
      }
      setModalOpen(false); form.resetFields(); loadAll();
    } catch (e) { message.error(e.response?.data?.detail || '注册失败'); }
    finally { setSubmitting(false); }
  };

  const handleDelete = async (id) => {
    await deviceApi.delete(id); message.success('已删除'); loadAll();
  };

  const openDetail = async (device) => {
    setSelected(device); setMqttTopics(null); setDrawerTab('mqtt'); setDrawerOpen(true);
    try { const r = await deviceApi.getMqttTopics(device.id); setMqttTopics(r.data.topics); } catch {}
  };

  const columns = [
    { title:'机器人ID', dataIndex:'device_id', render:v => <code style={{ fontSize:11, color:C.tealDk, background:'rgba(0,217,165,0.06)', padding:'2px 6px', borderRadius:4 }}>{v}</code> },
    { title:'名称', dataIndex:'name', render:v => <span style={{ color:C.text, fontWeight:600 }}>{v}</span> },
    { title:'所属大棚', dataIndex:'greenhouse_id', render:v => { const g = greenhouses.find(x => x.id === v); return g ? <Tag style={{ background:'rgba(0,217,165,0.08)', border:'1px solid rgba(0,217,165,0.2)', color:'#3d6a59', borderRadius:20 }}>{g.name}</Tag> : <span style={{ color:'#ccc' }}>未分配</span>; } },
    { title:'状态', key:'status', render:(_,r) => (
      <div style={{ display:'flex', alignItems:'center', gap:6 }}>
        <div style={{ width:7, height:7, borderRadius:'50%', background: r.is_online ? C.teal : '#ccc', boxShadow: r.is_online ? `0 0 6px ${C.teal}` : 'none', animation: r.is_online ? 'breathGreen 2s infinite' : 'none' }} />
        <span style={{ color: r.is_online ? C.tealDk : '#bbb', fontSize:12 }}>{r.is_online ? '在线' : '离线'}</span>
      </div>
    )},
    { title:'最后上线', dataIndex:'last_seen', render:v => <span style={{ fontSize:12, color:C.muted }}>{v ? formatUTCtoLocal(v,'short') : '从未'}</span> },
    { title:'固件', dataIndex:'firmware_version', render:v => <span style={{ color:C.muted, fontSize:12 }}>{v || '—'}</span> },
    { title:'操作', key:'op', render:(_,r) => (
      <Space size={4}>
        <Button size="small" type="text" icon={<WifiOutlined />} style={{ color:C.tealDk }} onClick={() => openDetail(r)}>详情</Button>
        <Popconfirm title="确认删除？" onConfirm={() => handleDelete(r.id)} okText="删除" cancelText="取消">
          <Button size="small" type="text" danger icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      </Space>
    )},
  ];

  return (
    <div className="fade-in">
      <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:16 }}>
        <div style={{ width:3, height:18, background:C.teal, borderRadius:2 }} />
        <span style={{ color:C.text, fontSize:15, fontWeight:700 }}>机器人管理</span>
      </div>

      <div style={{ display:'flex', gap:10, marginBottom:12 }}>
        {[
          { label:'机器人总数', value:devices.length, c:C.teal },
          { label:'在线', value:devices.filter(d=>d.is_online).length, c:'#00A87A' },
          { label:'离线', value:devices.filter(d=>!d.is_online).length, c:'#ccc' },
        ].map(s => (
          <div key={s.label} style={{ padding:'6px 14px', background:'rgba(255,255,255,0.6)', border:`1px solid ${C.border}`, borderRadius:8, fontSize:12, color:s.c, display:'flex', alignItems:'center', gap:8 }}>
            <span style={{ fontWeight:700, fontSize:18 }}>{s.value}</span><span style={{ color:C.muted }}>{s.label}</span>
          </div>
        ))}
        <div style={{ marginLeft:'auto', display:'flex', gap:8 }}>
          <Button icon={<ReloadOutlined />} onClick={loadAll} style={{ borderColor:C.border, color:C.muted }}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>注册机器人</Button>
        </div>
      </div>

      <GlassCard>
        <Table dataSource={devices} columns={columns} rowKey="id" loading={loading} size="middle"
          pagination={{ pageSize:10, style:{ padding:'8px 16px' } }}
          locale={{ emptyText:<div style={{ padding:30, color:C.muted, textAlign:'center' }}>暂无机器人，点击"注册机器人"</div> }}
        />
      </GlassCard>

      {/* 注册弹窗 */}
      <Modal title={<span style={{ color:C.tealDk }}>🤖 注册巡检机器人</span>}
        open={modalOpen} onCancel={() => { setModalOpen(false); form.resetFields(); }}
        onOk={() => form.submit()} okText="注册" confirmLoading={submitting} width={440}>
        <Alert type="info" showIcon style={{ marginBottom:14, borderRadius:8 }} message="注册后自动订阅 MQTT telemetry topic，无需重启服务" />
        <Form form={form} layout="vertical" onFinish={handleRegister}>
          <Form.Item name="device_id" label="机器人ID（唯一标识）" rules={[{ required:true }]} extra="建议格式：robot_001，与机器人端 MQTT Client ID 保持一致">
            <Input placeholder="robot_001" />
          </Form.Item>
          <Form.Item name="name" label="机器人名称" rules={[{ required:true }]}><Input placeholder="1号巡检机器人" /></Form.Item>
          <Form.Item name="greenhouse_id" label="默认所属大棚" extra="注册后可在详情中配置多个巡逻大棚">
            <Select allowClear placeholder="选择默认大棚（可选）">
              {greenhouses.map(g => <Select.Option key={g.id} value={g.id}>{g.name}</Select.Option>)}
            </Select>
          </Form.Item>
          <Form.Item name="firmware_version" label="固件版本"><Input placeholder="v2.1.0" /></Form.Item>
        </Form>
      </Modal>

      {/* 详情抽屉（MQTT + 多大棚巡逻）*/}
      <Drawer
        title={<span style={{ color:C.tealDk }}>🤖 {selected?.name || '机器人详情'}</span>}
        open={drawerOpen} onClose={() => setDrawerOpen(false)} width={520}
      >
        {selected && (
          <Tabs activeKey={drawerTab} onChange={setDrawerTab} items={[
            {
              key:'mqtt', label:'📡 MQTT 配置',
              children: (
                <>
                  <Descriptions size="small" column={2} style={{ marginBottom:14 }}>
                    <Descriptions.Item label="机器人ID" span={2}><code style={{ color:C.tealDk }}>{selected.device_id}</code></Descriptions.Item>
                    <Descriptions.Item label="状态"><span style={{ color: selected.is_online ? C.tealDk : '#bbb' }}>{selected.is_online ? '在线' : '离线'}</span></Descriptions.Item>
                    <Descriptions.Item label="最后上线">{selected.last_seen ? formatUTCtoLocal(selected.last_seen,'short') : '—'}</Descriptions.Item>
                  </Descriptions>
                  {mqttTopics ? (
                    <div style={{ padding:12, background:'rgba(0,217,165,0.04)', border:`1px solid rgba(0,217,165,0.12)`, borderRadius:10 }}>
                      <div style={{ fontSize:12, color:C.tealDk, marginBottom:10, fontWeight:700 }}>机器人端 MQTT 配置</div>
                      <TopicLine label="📤 上行 — 遥测数据（每10s）" value={mqttTopics.telemetry} />
                      <TopicLine label="📥 下行 — 控制指令" value={mqttTopics.command} />
                      <div style={{ marginTop:10, padding:10, background:'rgba(0,0,0,0.03)', borderRadius:8, fontSize:11 }}>
                        <div style={{ color:C.muted, marginBottom:4 }}>心跳格式（60s 一次）：</div>
                        <pre style={{ fontSize:10, color:'#888', overflow:'auto' }}>{`{"type":"heartbeat","robot_id":"${selected.device_id}","timestamp":${Math.floor(Date.now()/1000)},"battery":82,"status":"idle"}`}</pre>
                      </div>
                    </div>
                  ) : <div style={{ textAlign:'center', padding:20, color:C.muted }}>加载 Topic 信息...</div>}
                </>
              ),
            },
            {
              key:'patrol', label:'🗺️ 多大棚巡逻',
              children: <PatrolConfig device={selected} />,
            },
          ]} />
        )}
      </Drawer>
    </div>
  );
}
