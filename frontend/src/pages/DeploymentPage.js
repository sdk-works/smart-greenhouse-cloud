/**
 * DeploymentPage.js — 巡检方案管理（V9核心页面）
 * 功能：创建方案 → 选大棚 → 配置点位映射 → 预览配置 → 一键发布
 */
import React, { useState, useEffect } from 'react';
import {
  Table, Button, Modal, Form, Input, Select, InputNumber,
  Space, message, Popconfirm, Tag, Drawer, Tabs, Alert,
  Steps, Descriptions, Switch, Badge, List, Tooltip,
  Card, Row, Col,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, ReloadOutlined, SendOutlined,
  EyeOutlined, CheckCircleOutlined, SwapOutlined, EditOutlined,
  CopyOutlined, WarningOutlined,
} from '@ant-design/icons';
import api from '../services/api';
import { greenhouseApi, deviceApi } from '../services/api';
import { formatUTCtoLocal } from '../services/api';

const C = { teal:'#00D9A5', tealDk:'#00A87A', border:'rgba(0,217,165,0.18)', muted:'#8aab9e', text:'#2d4a3e', warn:'#FFB74D', danger:'#FF7043' };

function GlassCard({ children, style={} }) {
  return (
    <div style={{ background:'rgba(255,255,255,0.68)', backdropFilter:'blur(14px)', border:`1px solid ${C.border}`, borderRadius:12, position:'relative', overflow:'hidden', boxShadow:'0 4px 20px rgba(0,100,70,0.06)', ...style }}>
      <div style={{ position:'absolute', top:0, left:'15%', right:'15%', height:1, background:`linear-gradient(90deg,transparent,${C.teal}55,transparent)` }} />
      {children}
    </div>
  );
}

const STATUS_MAP = {
  draft:    { color:'#FFB74D', text:'草稿', icon:'🟡' },
  active:   { color:C.teal,   text:'已发布', icon:'🟢' },
  archived: { color:'#ccc',   text:'已归档', icon:'⚪' },
};

// ── 点位映射管理器 ────────────────────────────────────────────
function MappingManager({ depId, greenhouses }) {
  const [mappings, setMappings] = useState([]);
  const [loading,  setLoading]  = useState(false);
  const [addModal, setAddModal] = useState(false);
  const [editing,  setEditing]  = useState(null);
  const [form] = Form.useForm();

  useEffect(() => { if (depId) loadMappings(); }, [depId]);

  const loadMappings = async () => {
    setLoading(true);
    try {
      const r = await api.get(`/deployments/${depId}/point-mapping`);
      setMappings(r.data);
    } finally { setLoading(false); }
  };

  const openAdd = () => { setEditing(null); form.resetFields(); setAddModal(true); };
  const openEdit = (m) => { setEditing(m); form.setFieldsValue({ chassis_point: m.chassis_point, greenhouse_id: m.greenhouse_id, greenhouse_point: m.greenhouse_point, description: m.description }); setAddModal(true); };

  const handleSubmit = async (values) => {
    try {
      if (editing) {
        await api.put(`/deployments/${depId}/point-mapping/${editing.id}`, values);
        message.success('映射已更新');
      } else {
        await api.post(`/deployments/${depId}/point-mapping`, values);
        message.success('映射已添加');
      }
      setAddModal(false); form.resetFields(); loadMappings();
    } catch (e) {
      message.error(e.response?.data?.detail || '操作失败');
    }
  };

  const handleDelete = async (id) => {
    try {
      await api.delete(`/deployments/${depId}/point-mapping/${id}`);
      message.success('已删除'); loadMappings();
    } catch { message.error('删除失败'); }
  };

  const handleValidate = async () => {
    const r = await api.post(`/deployments/${depId}/point-mapping/validate`);
    if (r.data.valid) message.success(`✅ 校验通过，共 ${r.data.total} 条映射`);
    else Modal.error({ title:'映射校验失败', content: r.data.errors.map((e,i) => <div key={i}>• {e}</div>) });
  };

  const columns = [
    {
      title: '底盘点位', dataIndex: 'chassis_point', width: 120,
      render: v => (
        <Tag style={{ background:'rgba(255,183,77,0.12)', border:'1px solid rgba(255,183,77,0.3)', color:'#8a6a00', borderRadius:20, fontWeight:700 }}>
          {v}
        </Tag>
      ),
    },
    { title:'→', width: 30, render: () => <span style={{ color:C.muted, fontSize:16 }}>→</span> },
    { title:'所属大棚', dataIndex:'greenhouse_name', render:v => <span style={{ color:C.tealDk, fontWeight:600 }}>{v || '—'}</span> },
    {
      title:'大棚点位', dataIndex:'greenhouse_point',
      render: v => <Tag style={{ background:'rgba(0,217,165,0.1)', border:'1px solid rgba(0,217,165,0.25)', color:C.tealDk, borderRadius:20 }}>{v}</Tag>,
    },
    { title:'备注', dataIndex:'description', render:v => <span style={{ color:C.muted, fontSize:12 }}>{v || '—'}</span> },
    {
      title:'操作', key:'op', width: 120,
      render: (_,r) => (
        <Space size={4}>
          <Button size="small" type="text" icon={<EditOutlined />} style={{ color:C.tealDk }} onClick={() => openEdit(r)}>编辑</Button>
          <Popconfirm title="删除此映射？" onConfirm={() => handleDelete(r.id)} okText="删除" cancelText="取消">
            <Button size="small" type="text" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 获取已选大棚的点位列表
  const getWaypoints = (ghId) => {
    const gh = greenhouses.find(g => g.id === ghId);
    return (gh?.map_data?.waypoints || []);
  };

  return (
    <>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
        <Space>
          <SwapOutlined style={{ color:C.tealDk }} />
          <span style={{ color:C.text, fontWeight:600 }}>底盘点位 ↔ 大棚点位 映射关系</span>
          <Tag style={{ background:'rgba(0,217,165,0.08)', border:`1px solid rgba(0,217,165,0.2)`, color:C.tealDk, borderRadius:20 }}>{mappings.length} 条</Tag>
        </Space>
        <Space>
          <Button onClick={handleValidate} style={{ borderColor:C.border, color:C.muted }} icon={<CheckCircleOutlined />}>校验映射</Button>
          <Button icon={<ReloadOutlined />} onClick={loadMappings} style={{ borderColor:C.border, color:C.muted }}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>添加映射</Button>
        </Space>
      </div>

      <Alert type="info" showIcon style={{ marginBottom:12, borderRadius:8 }}
        message="配置规则：同一底盘点位（如A点）只能映射到一个大棚点位；同一大棚点位可被多个底盘点位映射"
      />

      <Table
        dataSource={mappings} columns={columns} rowKey="id"
        loading={loading} size="middle" pagination={{ pageSize:10 }}
        locale={{ emptyText:<div style={{ padding:24, color:C.muted, textAlign:'center' }}>
          <SwapOutlined style={{ fontSize:32, marginBottom:8 }} /><br/>
          暂无映射，点击"添加映射"配置底盘点位与大棚点位的对应关系
        </div>}}
      />

      {/* 添加/编辑弹窗 */}
      <Modal
        title={<span style={{ color:C.tealDk }}>{editing ? '✏️ 编辑映射' : '🔗 添加点位映射'}</span>}
        open={addModal} onCancel={() => { setAddModal(false); form.resetFields(); }}
        onOk={() => form.submit()} okText={editing ? '保存' : '添加'} width={460}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit} style={{ marginTop:12 }}>
          <Form.Item name="chassis_point" label="底盘点位名称（机器人上报的点位）" rules={[{ required:true }]}
            extra="如：A点、B点、Point1 —— 必须与机器人实际上报的点位名完全一致">
            <Input placeholder="A点" />
          </Form.Item>

          <Form.Item name="greenhouse_id" label="对应大棚" rules={[{ required:true }]}>
            <Select placeholder="选择大棚" onChange={() => form.setFieldValue('greenhouse_point', undefined)}>
              {greenhouses.map(g => <Select.Option key={g.id} value={g.id}>{g.name}</Select.Option>)}
            </Select>
          </Form.Item>

          <Form.Item shouldUpdate={(p, n) => p.greenhouse_id !== n.greenhouse_id}>
            {({ getFieldValue }) => {
              const ghId = getFieldValue('greenhouse_id');
              const wps  = getWaypoints(ghId);
              return (
                <Form.Item name="greenhouse_point" label="对应大棚点位" rules={[{ required:true }]}
                  extra={wps.length === 0 ? '该大棚暂无点位，请先在大棚管理中配置点位' : ''}>
                  <Select placeholder="选择大棚点位" disabled={!ghId}>
                    {wps.length > 0
                      ? wps.map(w => <Select.Option key={w.id} value={w.label || `点位${w.id}`}>{w.label || `点位${w.id}`}</Select.Option>)
                      : <Select.Option value="自定义" key="custom">自定义输入</Select.Option>
                    }
                  </Select>
                </Form.Item>
              );
            }}
          </Form.Item>

          <Form.Item name="description" label="备注">
            <Input placeholder="可选，如：此处为温室入口拍摄点" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

// ── 主页面 ────────────────────────────────────────────────────
export default function DeploymentPage() {
  const [deployments, setDeployments] = useState([]);
  const [greenhouses, setGreenhouses] = useState([]);
  const [devices,     setDevices]     = useState([]);
  const [loading,     setLoading]     = useState(false);
  const [createModal, setCreateModal] = useState(false);
  const [submitting,  setSubmitting]  = useState(false);
  const [publishing,  setPublishing]  = useState({});
  const [drawer,      setDrawer]      = useState(null);
  const [drawerTab,   setDrawerTab]   = useState('mapping');
  const [previewData, setPreviewData] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => { loadAll(); }, []);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [dr, gr, devR] = await Promise.all([
        api.get('/deployments/'),
        greenhouseApi.list(),
        deviceApi.list({ device_type:'robot' }),
      ]);
      setDeployments(dr.data); setGreenhouses(gr.data); setDevices(devR.data);
    } finally { setLoading(false); }
  };

  const handleCreate = async (values) => {
    setSubmitting(true);
    try {
      const device = devices.find(d => d.id === values.device_id);
      await api.post('/deployments/', {
        name: values.name, device_id: values.device_id,
        greenhouses: values.greenhouses || [],
        patrol_speed: values.patrol_speed || 0.5,
        capture_interval: values.capture_interval || 5,
        return_battery_threshold: values.return_battery_threshold || 20,
        auto_patrol: values.auto_patrol || false,
      });
      message.success('方案已创建，请继续配置点位映射关系');
      setCreateModal(false); form.resetFields(); loadAll();
    } catch (e) { message.error(e.response?.data?.detail || '创建失败'); }
    finally { setSubmitting(false); }
  };

  const handlePublish = async (dep) => {
    setPublishing(p => ({ ...p, [dep.id]: true }));
    try {
      const r = await api.post(`/deployments/${dep.id}/publish`);
      if (r.data.success) message.success(`✅ 方案已发布！配置版本 v${r.data.config_version}`);
      else message.warning(r.data.message);
      loadAll();
    } catch (e) { message.error(e.response?.data?.detail || '发布失败'); }
    finally { setPublishing(p => ({ ...p, [dep.id]: false })); }
  };

  const handlePreview = async (dep) => {
    try {
      const r = await api.get(`/deployments/${dep.id}/preview-config`);
      setPreviewData(r.data); setPreviewOpen(true);
    } catch { message.error('获取配置失败'); }
  };

  const handleDelete = async (id) => {
    try { await api.delete(`/deployments/${id}`); message.success('已删除'); loadAll(); }
    catch { message.error('删除失败'); }
  };

  const openDrawer = (dep) => { setDrawer(dep); setDrawerTab('mapping'); };

  const columns = [
    {
      title:'方案名称', dataIndex:'name', width:180,
      render:(v,r) => (
        <div>
          <div style={{ color:C.tealDk, fontWeight:700 }}>{v}</div>
          <div style={{ fontSize:11, color:C.muted }}>v{r.version} · 配置v{r.config_version}</div>
        </div>
      ),
    },
    {
      title:'绑定机器人', dataIndex:'device_id',
      render:v => { const d = devices.find(x => x.id === v); return d ? <code style={{ fontSize:11, color:C.tealDk, background:'rgba(0,217,165,0.06)', padding:'1px 5px', borderRadius:4 }}>{d.device_id}</code> : <span style={{ color:'#ccc' }}>未绑定</span>; },
    },
    {
      title:'状态', dataIndex:'status',
      render: v => { const s = STATUS_MAP[v] || STATUS_MAP.draft; return <Tag style={{ background:`${s.color}18`, border:`1px solid ${s.color}44`, color:s.color, borderRadius:20, fontWeight:600 }}>{s.icon} {s.text}</Tag>; },
    },
    {
      title:'点位映射', dataIndex:'point_mappings',
      render: pms => <Tag style={{ background:'rgba(0,217,165,0.08)', border:'1px solid rgba(0,217,165,0.2)', color:C.tealDk, borderRadius:20 }}>{pms?.length || 0} 条映射</Tag>,
    },
    { title:'发布时间', dataIndex:'published_at', render:v => <span style={{ fontSize:12, color:C.muted }}>{v ? formatUTCtoLocal(v,'short') : '未发布'}</span> },
    {
      title:'操作', key:'op', width:280,
      render:(_,r) => (
        <Space size={4} wrap>
          <Button size="small" type="text" icon={<SwapOutlined />} style={{ color:C.tealDk }} onClick={() => openDrawer(r)}>映射配置</Button>
          <Button size="small" type="text" icon={<EyeOutlined />} style={{ color:C.muted }} onClick={() => handlePreview(r)}>预览配置</Button>
          <Button size="small" type="primary" icon={<SendOutlined />} loading={publishing[r.id]} onClick={() => handlePublish(r)}>发布</Button>
          <Popconfirm title="删除此方案？" onConfirm={() => handleDelete(r.id)} okText="删除" cancelText="取消">
            <Button size="small" type="text" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div className="fade-in">
      <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:16 }}>
        <div style={{ width:3, height:18, background:C.teal, borderRadius:2 }} />
        <span style={{ color:C.text, fontSize:15, fontWeight:700 }}>巡检方案管理</span>
        <Tooltip title="巡检方案将机器人、大棚、点位映射绑定为一个完整配置，一键发布到工控机">
          <span style={{ fontSize:13, cursor:'help' }}>❓</span>
        </Tooltip>
      </div>

      <Alert type="info" showIcon style={{ marginBottom:14, borderRadius:8 }}
        message="工作流程：创建方案 → 配置点位映射（底盘点位↔大棚点位）→ 预览配置 → 发布到机器人"
      />

      <div style={{ display:'flex', justifyContent:'flex-end', marginBottom:12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModal(true)}>新建巡检方案</Button>
      </div>

      <GlassCard>
        <Table
          dataSource={deployments} columns={columns} rowKey="id"
          loading={loading} size="middle" pagination={{ pageSize:8 }}
          locale={{ emptyText:<div style={{ padding:30, color:C.muted, textAlign:'center' }}>暂无方案，点击"新建巡检方案"</div> }}
        />
      </GlassCard>

      {/* 创建弹窗 */}
      <Modal
        title={<span style={{ color:C.tealDk }}>🚀 新建巡检方案</span>}
        open={createModal} onCancel={() => { setCreateModal(false); form.resetFields(); }}
        onOk={() => form.submit()} okText="创建" confirmLoading={submitting} width={500}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate} style={{ marginTop:12 }}>
          <Form.Item name="name" label="方案名称" rules={[{ required:true }]}>
            <Input placeholder="如：机器人巡检方案A" />
          </Form.Item>
          <Form.Item name="device_id" label="绑定机器人" rules={[{ required:true }]}>
            <Select placeholder="选择机器人">
              {devices.map(d => <Select.Option key={d.id} value={d.id}><Badge status={d.is_online?'success':'default'} />{d.name} ({d.device_id})</Select.Option>)}
            </Select>
          </Form.Item>
          <Form.Item name="greenhouses" label="关联大棚（可多选）">
            <Select mode="multiple" placeholder="选择关联大棚">
              {greenhouses.map(g => <Select.Option key={g.id} value={g.id}>{g.name}</Select.Option>)}
            </Select>
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}><Form.Item name="patrol_speed" label="巡逻速度(m/s)" initialValue={0.5}><InputNumber style={{width:'100%'}} min={0.1} max={3} step={0.1}/></Form.Item></Col>
            <Col span={12}><Form.Item name="capture_interval" label="拍照间隔（点位数）" initialValue={5}><InputNumber style={{width:'100%'}} min={1} max={20}/></Form.Item></Col>
            <Col span={12}><Form.Item name="return_battery_threshold" label="低电返回(%)" initialValue={20}><InputNumber style={{width:'100%'}} min={5} max={50}/></Form.Item></Col>
            <Col span={12}><Form.Item name="auto_patrol" label="自动巡逻" valuePropName="checked" initialValue={false}><Switch checkedChildren="开启" unCheckedChildren="关闭"/></Form.Item></Col>
          </Row>
        </Form>
      </Modal>

      {/* 点位映射配置 Drawer */}
      <Drawer
        title={<span style={{ color:C.tealDk }}>🔗 {drawer?.name} — 配置</span>}
        open={!!drawer} onClose={() => setDrawer(null)} width={780}
      >
        {drawer && (
          <Tabs activeKey={drawerTab} onChange={setDrawerTab} items={[
            {
              key:'mapping', label:'📍 点位映射配置',
              children: <MappingManager depId={drawer.id} greenhouses={greenhouses} />,
            },
            {
              key:'info', label:'📋 方案信息',
              children: (
                <Descriptions column={2} size="small">
                  <Descriptions.Item label="方案ID" span={2}><code style={{ color:C.tealDk }}>{drawer.id}</code></Descriptions.Item>
                  <Descriptions.Item label="方案版本">v{drawer.version}</Descriptions.Item>
                  <Descriptions.Item label="配置版本">v{drawer.config_version}</Descriptions.Item>
                  <Descriptions.Item label="状态">{STATUS_MAP[drawer.status]?.text}</Descriptions.Item>
                  <Descriptions.Item label="发布时间">{drawer.published_at ? formatUTCtoLocal(drawer.published_at) : '未发布'}</Descriptions.Item>
                </Descriptions>
              ),
            },
          ]} />
        )}
      </Drawer>

      {/* 配置预览 Modal */}
      <Modal
        title={<span style={{ color:C.tealDk }}>📋 完整下发配置预览</span>}
        open={previewOpen} onCancel={() => setPreviewOpen(false)} footer={null} width={680}
      >
        {previewData && (
          <div style={{ position:'relative' }}>
            <Button
              size="small" icon={<CopyOutlined />}
              style={{ position:'absolute', top:8, right:8, zIndex:1, borderColor:C.border, color:C.muted }}
              onClick={() => { navigator.clipboard?.writeText(JSON.stringify(previewData, null, 2)); message.success('已复制'); }}
            >复制</Button>
            <pre style={{
              background:'rgba(0,217,165,0.03)', border:`1px solid rgba(0,217,165,0.12)`,
              borderRadius:8, padding:14, fontSize:12, color:C.text,
              maxHeight:500, overflow:'auto', lineHeight:1.6,
            }}>
              {JSON.stringify(previewData, null, 2)}
            </pre>
          </div>
        )}
      </Modal>
    </div>
  );
}
