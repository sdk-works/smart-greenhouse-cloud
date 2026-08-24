import React, { useState, useEffect } from 'react';
import { Table, Button, Modal, Form, Input, Select, Switch, Space, message, Tag, Popconfirm, Alert } from 'antd';
import { PlusOutlined, ReloadOutlined, UserOutlined } from '@ant-design/icons';
import api from '../services/api';
import { formatUTCtoLocal } from '../services/api';

const C = { teal:'#00D9A5', tealDk:'#00A87A', border:'rgba(0,217,165,0.18)', muted:'#8aab9e', text:'#2d4a3e' };

const ROLE_MAP = {
  super_admin: { label:'超级管理员', color:'#CE93D8', desc:'全部权限（我方）' },
  admin:       { label:'客户管理员', color:'#00D9A5', desc:'L1+L2全部权限' },
  operator:    { label:'运维人员',   color:'#4FC3F7', desc:'L1+L2查看权限' },
  viewer:      { label:'观察者',     color:'#ccc',    desc:'仅L1查看权限' },
};

function GlassCard({ children }) {
  return (
    <div style={{ background:'rgba(255,255,255,0.68)', backdropFilter:'blur(14px)', border:`1px solid ${C.border}`, borderRadius:12, position:'relative', overflow:'hidden', boxShadow:'0 4px 20px rgba(0,100,70,0.06)' }}>
      <div style={{ position:'absolute', top:0, left:'15%', right:'15%', height:1, background:`linear-gradient(90deg,transparent,${C.teal}55,transparent)` }} />
      {children}
    </div>
  );
}

export default function UserManage() {
  const [users,    setUsers]    = useState([]);
  const [loading,  setLoading]  = useState(false);
  const [modal,    setModal]    = useState(false);
  const [submitting,setSubmitting] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => { loadUsers(); }, []);

  const loadUsers = async () => {
    setLoading(true);
    try { const r = await api.get('/auth/users'); setUsers(r.data); }
    catch (e) { if (e.response?.status !== 403) message.error('加载失败'); }
    finally { setLoading(false); }
  };

  const handleCreate = async (values) => {
    setSubmitting(true);
    try {
      await api.post('/auth/users', values);
      message.success(`用户 "${values.username}" 创建成功`);
      setModal(false); form.resetFields(); loadUsers();
    } catch (e) { message.error(e.response?.data?.detail || '创建失败'); }
    finally { setSubmitting(false); }
  };

  const handleToggle = async (userId, is_active) => {
    try { await api.put(`/auth/users/${userId}/toggle`); message.success(is_active ? '已禁用' : '已启用'); loadUsers(); }
    catch { message.error('操作失败'); }
  };

  const handleRoleChange = async (userId, role) => {
    try { await api.put(`/auth/users/${userId}/role`, null, { params:{ role } }); message.success('角色已更新'); loadUsers(); }
    catch (e) { message.error(e.response?.data?.detail || '更新失败'); }
  };

  const columns = [
    { title:'用户名', dataIndex:'username', render:v=><span style={{color:C.text,fontWeight:600}}>{v}</span> },
    { title:'邮箱', dataIndex:'email', render:v=>v||<span style={{color:'#ccc'}}>—</span> },
    {
      title:'角色', dataIndex:'role',
      render:(v,r) => {
        const info = ROLE_MAP[v] || ROLE_MAP.viewer;
        return (
          <Select value={v} size="small" style={{ width:130 }}
            onChange={role => handleRoleChange(r.id, role)}
            disabled={v === 'super_admin'}
          >
            {Object.entries(ROLE_MAP).map(([k, m]) => (
              <Select.Option key={k} value={k} disabled={k==='super_admin'}>
                <Tag style={{ borderRadius:20, background:`${m.color}18`, border:`1px solid ${m.color}44`, color:m.color, fontSize:11 }}>{m.label}</Tag>
              </Select.Option>
            ))}
          </Select>
        );
      },
    },
    {
      title:'权限说明', dataIndex:'role',
      render: v => <span style={{fontSize:11,color:C.muted}}>{ROLE_MAP[v]?.desc}</span>,
    },
    {
      title:'状态', dataIndex:'is_active',
      render:(v,r) => (
        <Switch checked={v} size="small"
          checkedChildren="启用" unCheckedChildren="禁用"
          onChange={() => handleToggle(r.id, v)}
          disabled={r.role === 'super_admin'}
        />
      ),
    },
    { title:'创建时间', dataIndex:'created_at', render:v=><span style={{fontSize:12,color:C.muted}}>{formatUTCtoLocal(v,'short')}</span> },
  ];

  return (
    <div className="fade-in">
      <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:16}}>
        <div style={{width:3,height:18,background:C.teal,borderRadius:2}} />
        <span style={{color:C.text,fontSize:15,fontWeight:700}}>用户管理 & 权限</span>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:10,marginBottom:14}}>
        {Object.entries(ROLE_MAP).map(([k,m]) => (
          <div key={k} style={{padding:'10px 14px',background:'rgba(255,255,255,0.68)',backdropFilter:'blur(14px)',border:`1px solid ${m.color}33`,borderRadius:10}}>
            <Tag style={{borderRadius:20,background:`${m.color}18`,border:`1px solid ${m.color}44`,color:m.color,fontSize:12,marginBottom:4}}>{m.label}</Tag>
            <div style={{fontSize:11,color:C.muted}}>{m.desc}</div>
            <div style={{fontSize:18,fontWeight:800,color:m.color,marginTop:4}}>{users.filter(u=>u.role===k).length}</div>
          </div>
        ))}
      </div>

      <Alert type="info" showIcon style={{marginBottom:12,borderRadius:8}}
        message={<span>权限分层：<b>超级管理员</b>（我方全部权限）→ <b>客户管理员</b>（L1+L2含点位映射配置）→ <b>运维人员</b>（L1+L2查看）→ <b>观察者</b>（仅L1查看）</span>}
      />

      <div style={{display:'flex',justifyContent:'flex-end',marginBottom:12}}>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadUsers} style={{borderColor:C.border,color:C.muted}}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={()=>setModal(true)}>添加用户</Button>
        </Space>
      </div>

      <GlassCard>
        <Table dataSource={users} columns={columns} rowKey="id" loading={loading} size="middle"
          pagination={{pageSize:10,style:{padding:'8px 16px'}}}
          locale={{emptyText:<div style={{padding:30,color:C.muted,textAlign:'center'}}>暂无用户，点击"添加用户"</div>}}
        />
      </GlassCard>

      <Modal title={<span style={{color:C.tealDk}}>➕ 添加用户</span>}
        open={modal} onCancel={()=>{setModal(false);form.resetFields();}}
        onOk={()=>form.submit()} okText="创建" confirmLoading={submitting} width={420}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate} style={{marginTop:12}}>
          <Form.Item name="username" label="用户名" rules={[{required:true}]}><Input placeholder="输入用户名" /></Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{required:true,min:6,message:'密码至少6位'}]}><Input.Password placeholder="至少6位" /></Form.Item>
          <Form.Item name="email"    label="邮箱（可选）"><Input placeholder="user@example.com" /></Form.Item>
          <Form.Item name="role"     label="角色" initialValue="viewer">
            <Select>
              {Object.entries(ROLE_MAP).filter(([k])=>k!=='super_admin').map(([k,m])=>(
                <Select.Option key={k} value={k}><Tag style={{borderRadius:20,background:`${m.color}18`,border:`1px solid ${m.color}44`,color:m.color}}>{m.label}</Tag> — {m.desc}</Select.Option>
              ))}
            </Select>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
