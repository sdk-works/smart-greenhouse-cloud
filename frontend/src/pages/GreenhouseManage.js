import React, { useState, useEffect } from 'react';
import {
  Table, Button, Modal, Form, Input, InputNumber, Select,
  Space, message, Popconfirm, Tag, Drawer, Descriptions, Tabs,
  Alert, InputNumber as AntInputNumber, Tooltip,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined, EyeOutlined,
  ReloadOutlined, EnvironmentOutlined, AimOutlined, DragOutlined,
} from '@ant-design/icons';
import { greenhouseApi, settingsApi } from '../services/api';
import { formatUTCtoLocal } from '../services/api';

const C = { teal:'#00D9A5', tealDk:'#00A87A', border:'rgba(0,217,165,0.18)', bg:'rgba(255,255,255,0.7)', muted:'#8aab9e', text:'#2d4a3e' };

function GlassCard({ children, style={} }) {
  return (
    <div style={{ background:'rgba(255,255,255,0.72)', backdropFilter:'blur(12px)', border:`1px solid ${C.border}`, borderRadius:12, position:'relative', overflow:'hidden', boxShadow:'0 4px 20px rgba(0,100,70,0.06)', ...style }}>
      <div style={{ position:'absolute', top:0, left:'15%', right:'15%', height:1, background:`linear-gradient(90deg,transparent,${C.teal}55,transparent)` }} />
      {children}
    </div>
  );
}

function PageTitle({ children }) {
  return (
    <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:16 }}>
      <div style={{ width:3, height:18, background:C.teal, borderRadius:2 }} />
      <span style={{ color:C.text, fontSize:15, fontWeight:700 }}>{children}</span>
    </div>
  );
}

const CROPS = ['番茄','黄瓜','草莓','辣椒','生菜','菠菜','韭菜','香菜','芹菜','茄子','西葫芦'];
const ACTIONS = [
  { value:'capture_image',  label:'📷 拍照' },
  { value:'measure_env',    label:'🌡️ 测量环境' },
  { value:'pest_detect',    label:'🔍 病虫害检测' },
  { value:'soil_sample',    label:'🌱 土壤采样' },
];

// ── 点位管理子组件 ─────────────────────────────────────────────
function WaypointManager({ greenhouse }) {
  const [waypoints, setWaypoints] = useState([]);
  const [loading, setLoading]     = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing]     = useState(null);
  const [form] = Form.useForm();

  useEffect(() => { if (greenhouse?.id) loadWaypoints(); }, [greenhouse]);

  const loadWaypoints = async () => {
    setLoading(true);
    try {
      const r = await settingsApi.getGreenhouseMap(greenhouse.id);
      setWaypoints(r.data.waypoints || []);
    } catch { setWaypoints([]); }
    finally { setLoading(false); }
  };

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (wp) => {
    setEditing(wp);
    form.setFieldsValue({ x:wp.x, y:wp.y, label:wp.label, description:wp.description, actions:wp.actions || [] });
    setModalOpen(true);
  };

  const handleSubmit = async (values) => {
    try {
      if (editing) {
        const updated = waypoints.map(w => w.id === editing.id ? { ...w, ...values } : w);
        await settingsApi.updateGreenhouseMap(greenhouse.id, {
          width: 100, height: 60, waypoints: updated, sensor_positions: []
        });
        message.success('点位已更新');
      } else {
        // 修复：严格按照后端需要的格式传参
        await settingsApi.addWaypoint(greenhouse.id, {
          id: 0,
          x: values.x,
          y: values.y,
          label: values.label,
          actions: values.actions || [],
          description: values.description
        });
        message.success('点位已添加');
      }
      setModalOpen(false);
      form.resetFields();
      loadWaypoints();
    } catch (e) {
      message.error(e.response?.data?.detail || '操作失败');
    }
  };

  const handleDelete = async (wp) => {
    try {
      await settingsApi.deleteWaypoint(greenhouse.id, wp.id);
      message.success('已删除');
      loadWaypoints();
    } catch { message.error('删除失败'); }
  };

  const wpColumns = [
    { title:'序号', dataIndex:'id', width:60, render:v => <Tag style={{ background:'rgba(0,217,165,0.1)', border:`1px solid rgba(0,217,165,0.25)`, color:C.tealDk, borderRadius:20, fontSize:11 }}>#{v}</Tag> },
    { title:'点位名称', dataIndex:'label', render:(v,r) => <span style={{ color:C.text, fontWeight:600 }}>{v || `点位 #${r.id}`}</span> },
    { title:'坐标 (m)', key:'pos', render:(_,r) => <span style={{ fontFamily:'monospace', fontSize:12, color:C.muted }}>X:{r.x?.toFixed(1)} Y:{r.y?.toFixed(1)}</span> },
    { title:'执行动作', dataIndex:'actions', render: vs => (vs || []).map(a => {
      const act = ACTIONS.find(x => x.value === a);
      return <Tag key={a} style={{ fontSize:11, borderRadius:20, border:'1px solid rgba(0,217,165,0.2)', color:'#3d6a59' }}>{act?.label || a}</Tag>;
    })},
    { title:'描述', dataIndex:'description', render:v => <span style={{ fontSize:12, color:C.muted }}>{v || '—'}</span> },
    { title:'操作', key:'op', width:140, render:(_,r) => (
      <Space size={4}>
        <Button size="small" type="text" icon={<EditOutlined />} style={{ color:C.tealDk }} onClick={() => openEdit(r)}>编辑</Button>
        <Popconfirm title="删除此点位？" onConfirm={() => handleDelete(r)} okText="删除" cancelText="取消">
          <Button size="small" type="text" icon={<DeleteOutlined />} danger>删除</Button>
        </Popconfirm>
      </Space>
    )},
  ];

  if (!greenhouse) {
    return <Alert type="info" message="请先在左侧列表选择大棚，再管理其点位" style={{ borderRadius:8 }} showIcon />;
  }

  return (
    <>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
        <Space>
          <EnvironmentOutlined style={{ color:C.tealDk }} />
          <span style={{ color:C.text, fontWeight:600 }}>{greenhouse.name} — 点位管理</span>
          <Tag style={{ background:'rgba(0,217,165,0.1)', border:`1px solid rgba(0,217,165,0.25)`, color:C.tealDk, borderRadius:20 }}>共 {waypoints.length} 个点位</Tag>
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadWaypoints} style={{ borderColor:C.border, color:C.muted }}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加点位</Button>
        </Space>
      </div>

      <Alert type="info" showIcon style={{ marginBottom:12, borderRadius:8 }}
        message="简单配置说明：输入点位名称和坐标（米），选择到达此点后机器人执行的动作，保存即可"
      />

      <Table
        dataSource={waypoints} columns={wpColumns} rowKey="id"
        loading={loading} size="middle" pagination={{ pageSize:10 }}
        locale={{ emptyText:<div style={{ padding:24, color:C.muted }}>暂无点位，点击"添加点位"新增</div> }}
      />

      {/* 点位编辑弹窗 */}
      <Modal
        title={<span style={{ color:C.tealDk }}>{editing ? '✏️ 编辑点位' : '📍 添加巡检点位'}</span>}
        open={modalOpen}
        onCancel={() => { setModalOpen(false); form.resetFields(); }}
        onOk={() => form.submit()}
        okText={editing ? '保存' : '添加'}
        width={460}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit} style={{ marginTop:12 }}>
          <Form.Item name="label" label="点位名称">
            <Input placeholder="如：A区1号采样点、大门右侧" />
          </Form.Item>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
            <Form.Item name="x" label="X 坐标（米）" rules={[{ required:true, message:'必填' }]}>
              <InputNumber style={{ width:'100%' }} min={0} step={0.5} placeholder="0~100" />
            </Form.Item>
            <Form.Item name="y" label="Y 坐标（米）" rules={[{ required:true, message:'必填' }]}>
              <InputNumber style={{ width:'100%' }} min={0} step={0.5} placeholder="0~60" />
            </Form.Item>
          </div>
          <Form.Item name="actions" label="到达此点后执行的动作">
            <Select mode="multiple" placeholder="选择动作（可多选）">
              {ACTIONS.map(a => <Select.Option key={a.value} value={a.value}>{a.label}</Select.Option>)}
            </Select>
          </Form.Item>
          <Form.Item name="description" label="备注说明">
            <Input placeholder="可选，如：此处易发现病虫害" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

// ── 主页面 ────────────────────────────────────────────────────
export default function GreenhouseManage() {
  const [list, setList]             = useState([]);
  const [loading, setLoading]       = useState(false);
  const [modalOpen, setModalOpen]   = useState(false);
  const [editRecord, setEditRecord] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail]         = useState(null);
  const [activeTab, setActiveTab]   = useState('info');
  const [form] = Form.useForm();

  useEffect(() => { load(); }, []);

  const load = async () => {
    setLoading(true);
    try { const r = await greenhouseApi.list(); setList(r.data); }
    finally { setLoading(false); }
  };

  const openCreate = () => { setEditRecord(null); form.resetFields(); setModalOpen(true); };
  const openEdit   = (r)  => { setEditRecord(r); form.setFieldsValue({ name:r.name, location:r.location, area:r.area, crop_type:r.crop_type, description:r.description }); setModalOpen(true); };

  const openDetail = async (record) => {
    setDetail(null); setActiveTab('info'); setDrawerOpen(true);
    try {
      const [g, s] = await Promise.all([greenhouseApi.get(record.id), greenhouseApi.stats(record.id)]);
      setDetail({ ...g.data, stats: s.data });
    } catch { setDetail(record); }
  };

  const handleSubmit = async (values) => {
    setSubmitting(true);
    try {
      if (editRecord) { await greenhouseApi.update(editRecord.id, values); message.success('大棚信息已更新'); }
      else             { await greenhouseApi.create(values);              message.success('大棚创建成功！');   }
      setModalOpen(false); form.resetFields(); load();
    } catch (e) { message.error(e.response?.data?.detail || '操作失败'); }
    finally { setSubmitting(false); }
  };

  const handleDelete = async (id) => {
    try { await greenhouseApi.delete(id); message.success('已删除'); load(); }
    catch { message.error('删除失败'); }
  };

  const columns = [
    { title:'大棚名称', dataIndex:'name', render:v => <span style={{ color:C.tealDk, fontWeight:700 }}>{v}</span> },
    { title:'位置', dataIndex:'location', render:v => v || <span style={{ color:'#ccc' }}>—</span> },
    { title:'作物', dataIndex:'crop_type', render:v => v ? <Tag style={{ background:'rgba(0,217,165,0.08)', border:'1px solid rgba(0,217,165,0.2)', color:'#3d6a59', borderRadius:20 }}>{v}</Tag> : '—' },
    { title:'面积', dataIndex:'area', render:v => v ? <span style={{ color:C.muted }}>{v.toLocaleString()}m²</span> : '—' },
    { title:'状态', dataIndex:'is_active', render:v => (
      <div style={{ display:'flex', alignItems:'center', gap:5 }}>
        <div style={{ width:6, height:6, borderRadius:'50%', background: v ? C.teal : '#ccc', boxShadow: v ? `0 0 6px ${C.teal}` : 'none' }} />
        <span style={{ color: v ? C.tealDk : '#bbb', fontSize:12 }}>{v ? '正常' : '停用'}</span>
      </div>
    )},
    { title:'创建时间', dataIndex:'created_at', render:v => <span style={{ fontSize:12, color:C.muted }}>{formatUTCtoLocal(v,'short')}</span> },
    { title:'操作', key:'op', render:(_,r) => (
      <Space size={4}>
        <Button size="small" type="text" icon={<EyeOutlined />} style={{ color:C.tealDk }} onClick={() => openDetail(r)}>详情/点位</Button>
        <Button size="small" type="text" icon={<EditOutlined />} style={{ color:C.muted }} onClick={() => openEdit(r)}>编辑</Button>
        <Popconfirm title="确认删除？" description="相关数据不会被删除" onConfirm={() => handleDelete(r.id)} okText="删除" cancelText="取消">
          <Button size="small" type="text" danger icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      </Space>
    )},
  ];

  return (
    <div className="fade-in">
      <PageTitle>大棚管理</PageTitle>

      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
        <Tag style={{ background:'rgba(0,217,165,0.08)', border:`1px solid rgba(0,217,165,0.2)`, color:C.tealDk, borderRadius:20, fontSize:12 }}>
          共 {list.filter(g => g.is_active).length} 个活跃大棚
        </Tag>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load} style={{ borderColor:C.border, color:C.muted }}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建大棚</Button>
        </Space>
      </div>

      <GlassCard>
        <Table dataSource={list} columns={columns} rowKey="id" loading={loading} size="middle"
          pagination={{ pageSize:10, style:{ padding:'8px 16px' } }}
          locale={{ emptyText:<div style={{ padding:30, color:C.muted, textAlign:'center' }}>暂无大棚，点击"新建大棚"</div> }}
        />
      </GlassCard>

      {/* 新建/编辑弹窗 */}
      <Modal
        title={<span style={{ color:C.tealDk }}>{editRecord ? '✏️ 编辑大棚' : '🏠 新建大棚'}</span>}
        open={modalOpen}
        onCancel={() => { setModalOpen(false); form.resetFields(); }}
        onOk={() => form.submit()} okText={editRecord ? '保存' : '创建'}
        confirmLoading={submitting} width={480}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit} style={{ marginTop:16 }}>
          <Form.Item name="name" label="大棚名称" rules={[{ required:true, message:'请输入大棚名称' }]}>
            <Input placeholder="如：1号大棚·番茄区" />
          </Form.Item>
          <Form.Item name="location" label="位置">
            <Input placeholder="如：东区A-1" />
          </Form.Item>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
            <Form.Item name="area" label="面积（m²）">
              <InputNumber style={{ width:'100%' }} min={0} placeholder="2000" />
            </Form.Item>
            <Form.Item name="crop_type" label="作物类型">
              <Select allowClear placeholder="选择作物">
                {CROPS.map(c => <Select.Option key={c} value={c}>{c}</Select.Option>)}
              </Select>
            </Form.Item>
          </div>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="大棚用途、特点等" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 详情抽屉（含点位管理 Tab）*/}
      <Drawer
        title={<span style={{ color:C.tealDk }}>🏠 {detail?.name || '大棚详情'}</span>}
        open={drawerOpen} onClose={() => setDrawerOpen(false)}
        width={680}
      >
        {detail ? (
          <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
            {
              key:'info', label:'基本信息',
              children: (
                <>
                  <Descriptions column={2} size="small" style={{ marginBottom:16 }}>
                    <Descriptions.Item label="名称" span={2}><span style={{ color:C.tealDk, fontWeight:700 }}>{detail.name}</span></Descriptions.Item>
                    <Descriptions.Item label="位置">{detail.location || '—'}</Descriptions.Item>
                    <Descriptions.Item label="作物">{detail.crop_type || '—'}</Descriptions.Item>
                    <Descriptions.Item label="面积">{detail.area ? `${detail.area.toLocaleString()}m²` : '—'}</Descriptions.Item>
                    <Descriptions.Item label="状态"><span style={{ color: detail.is_active ? C.tealDk : '#bbb' }}>{detail.is_active ? '正常使用' : '已停用'}</span></Descriptions.Item>
                    <Descriptions.Item label="创建" span={2}>{formatUTCtoLocal(detail.created_at)}</Descriptions.Item>
                    {detail.description && <Descriptions.Item label="描述" span={2}>{detail.description}</Descriptions.Item>}
                  </Descriptions>
                  {detail.stats && (
                    <>
                      <div style={{ fontSize:12, color:C.tealDk, marginBottom:8, fontWeight:700 }}>📊 近1小时均值</div>
                      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:8 }}>
                        {[
                          { label:'设备总数', value:detail.stats.total_devices },
                          { label:'在线设备', value:detail.stats.online_devices },
                          { label:'平均温度', value:`${detail.stats.avg_temperature}°C` },
                          { label:'平均湿度', value:`${detail.stats.avg_humidity}%` },
                          { label:'平均土壤', value:`${detail.stats.avg_soil_moisture}%` },
                          { label:'平均CO₂',  value:`${detail.stats.avg_co2}ppm` },
                        ].map(s => (
                          <div key={s.label} style={{ padding:'8px 10px', background:'rgba(0,217,165,0.05)', borderRadius:8, border:`1px solid rgba(0,217,165,0.12)` }}>
                            <div style={{ fontSize:10, color:C.muted, marginBottom:2 }}>{s.label}</div>
                            <div style={{ fontSize:15, color:C.tealDk, fontWeight:700 }}>{s.value}</div>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </>
              ),
            },
            {
              key:'waypoints', label:`📍 点位管理（${(detail.map_data?.waypoints || []).length}）`,
              children: <WaypointManager greenhouse={detail} />,
            },
          ]} />
        ) : (
          <div style={{ textAlign:'center', padding:40, color:C.muted }}>
            <div style={{ fontSize:32, marginBottom:8, animation:'spin 2s linear infinite', display:'inline-block' }}>🌿</div>
            <div>加载中...</div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
