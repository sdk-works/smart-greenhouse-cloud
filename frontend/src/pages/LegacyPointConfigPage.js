import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Checkbox, Form, Grid, Input, InputNumber, List, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd';
import { createWebSocket, greenhouseApi, pointConfigApi } from '../services/api';

const ROBOT_ID = 'ROB001';

export default function PointConfigPage() {
  const [rows, setRows] = useState([]), [greenhouses, setGreenhouses] = useState([]);
  const [status, setStatus] = useState({}), [editing, setEditing] = useState(null);
  const [form] = Form.useForm();
  const mobile = !Grid.useBreakpoint().md;
  const refresh = useCallback(async () => {
    try {
      const [d, s, g] = await Promise.all([
        pointConfigApi.discoveries(ROBOT_ID), pointConfigApi.status(ROBOT_ID), greenhouseApi.list()]);
      setRows(d.data || []); setStatus(s.data || {});
      setGreenhouses(Array.isArray(g.data) ? g.data : (g.data?.items || []));
    } catch (e) { message.error(e.response?.data?.detail || '点位信息读取失败'); }
  }, []);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 30000);
    const ws = createWebSocket(event => {
      if (['point_discovered', 'point_config', 'config_ack'].includes(event?.type)) refresh();
    });
    return () => { clearInterval(timer); ws?.close(); };
  }, [refresh]);
  const edit = row => { setEditing(row); form.setFieldsValue({ sample_seconds:10, collect_soil:true, collect_environment:true, take_photo:true, enabled:true, ...row.assignment }); };
  const save = async () => { const value = await form.validateFields(); await pointConfigApi.assign(ROBOT_ID, editing.nav_point_code, value); message.success('点位分配已保存；发布后才会下发工控机'); setEditing(null); refresh(); };
  const publish = async mode => { try { const r = await pointConfigApi.publish(ROBOT_ID, mode); message.success(`已发布 v${r.data.config_version}（${mode === 'discovery' ? '发现' : '正式'}模式）`); refresh(); } catch(e) { message.error(e.response?.data?.detail || '发布失败'); } };
  const resetDiscovery = async () => { try { const r = await pointConfigApi.resetDiscovery(ROBOT_ID); message.success(`已开始新一轮打点：清理 ${r.data.cleared_discoveries} 个旧编号，配置 v${r.data.config_version}`); refresh(); } catch(e) { message.error(e.response?.data?.detail || '重新打点失败'); } };
  const deleteDiscovery = async code => { try { await pointConfigApi.deleteDiscovery(ROBOT_ID, code); message.success(`已从当前工作区删除导航点 ${code}`); refresh(); } catch(e) { message.error(e.response?.data?.detail || '删除失败'); } };
  const columns = [
    { title:'导航编号', dataIndex:'nav_point_code', render:v=><Tag color="blue" style={{fontSize:14,fontWeight:700,padding:'3px 10px'}}>导航点 {v}</Tag> },
    { title:'发现次数', dataIndex:'seen_count' },
    { title:'最近收到', dataIndex:'last_seen_at', render:v=>v ? new Date(v).toLocaleString() : '-' },
    { title:'分配状态', render:(_,r)=>r.assignment ? <Tag color="green">{r.assignment.point_name}</Tag> : <Tag color="orange">待分配</Tag> },
    { title:'大棚', render:(_,r)=>greenhouses.find(g=>g.id===r.assignment?.greenhouse_id)?.name || '-' },
    { title:'采集', render:(_,r)=>r.assignment ? `${r.assignment.sample_seconds}秒 / ${r.assignment.enabled?'启用':'停用'}` : '-' },
    { title:'操作', render:(_,r)=><Space wrap><Button type="link" onClick={()=>edit(r)}>{r.assignment?'修改':'分配'}</Button>{r.assignment&&<Popconfirm title="确认取消这个点位的分配？" onConfirm={async()=>{await pointConfigApi.unassign(ROBOT_ID,r.nav_point_code);refresh();}}><Button danger type="link">取消分配</Button></Popconfirm>}<Popconfirm title={`删除导航点 ${r.nav_point_code}？`} description="仅从当前打点工作区删除，历史发布和采集数据保留。" onConfirm={()=>deleteDiscovery(r.nav_point_code)}><Button danger type="link">删除记录</Button></Popconfirm></Space> },
  ];
  return <Space direction="vertical" size={16} style={{width:'100%'}}>
    <Card title={mobile?'采集点配置':'导航点位发现与采集配置'} styles={{body:{padding:mobile?12:24}}} extra={!mobile&&<Space><Button onClick={()=>publish('discovery')}>继续发现点位</Button><Popconfirm title="开始新一轮重新打点？" description="将清空当前点位工作区并下发空的发现模式；历史数据和发布记录会保留。" okText="确认重新打点" cancelText="取消" onConfirm={resetDiscovery}><Button danger>开始重新打点</Button></Popconfirm><Popconfirm title="确认切换为正式采集模式？" description="只有已配置且启用的点位会执行采集。" onConfirm={()=>publish('operational')}><Button type="primary">发布正式采集模式</Button></Popconfirm></Space>}>
      {mobile&&<Space direction="vertical" style={{width:'100%',marginBottom:12}}><Button block onClick={()=>publish('discovery')}>继续发现点位</Button><Popconfirm title="开始新一轮重新打点？" description="清空当前工作区，历史数据保留。" onConfirm={resetDiscovery}><Button block danger>开始重新打点</Button></Popconfirm><Popconfirm title="确认切换为正式采集模式？" onConfirm={()=>publish('operational')}><Button block type="primary">发布正式采集模式</Button></Popconfirm></Space>}
      <Alert showIcon type={status.sync_status==='applied'?'success':'info'} message={`工控机配置：v${status.config_version||0} / ${status.mode==='operational'?'正式采集':'发现点位'} / ${status.sync_status||'未发布'}`} description="发现模式只登记导航编号、不采集；点位分配完成并发布正式模式后，工控机才按本表执行。" />
      {mobile ? <List style={{marginTop:12}} dataSource={rows} locale={{emptyText:'等待导航上报点位'}} renderItem={r=><List.Item style={{padding:'8px 0'}}><Card size="small" style={{width:'100%'}} title={<Space><Tag color="blue" style={{fontSize:15,fontWeight:700}}>导航点 {r.nav_point_code}</Tag>{r.assignment?<Tag color="green">已分配</Tag>:<Tag color="orange">待分配</Tag>}</Space>} extra={<Button size="small" type="primary" onClick={()=>edit(r)}>{r.assignment?'修改':'分配'}</Button>}><Space direction="vertical" size={6} style={{width:'100%'}}><div>最近收到：{r.last_seen_at?new Date(r.last_seen_at).toLocaleString():'-'}</div><div>发现次数：{r.seen_count}</div><div>采集点：{r.assignment?.point_name||'-'}</div><div>所属大棚：{greenhouses.find(g=>g.id===r.assignment?.greenhouse_id)?.name||'-'}</div><Space wrap>{r.assignment&&<Popconfirm title="确认取消分配？" onConfirm={async()=>{await pointConfigApi.unassign(ROBOT_ID,r.nav_point_code);refresh();}}><Button danger size="small">取消分配</Button></Popconfirm>}<Popconfirm title={`删除导航点 ${r.nav_point_code}？`} description="历史数据会保留。" onConfirm={()=>deleteDiscovery(r.nav_point_code)}><Button danger size="small" type="dashed">删除记录</Button></Popconfirm></Space></Space></Card></List.Item>} /> : <Table style={{marginTop:16}} rowKey="id" columns={columns} dataSource={rows} pagination={{pageSize:20}} />}
    </Card>
    <Modal title={`配置导航点 ${editing?.nav_point_code||''}`} open={!!editing} onCancel={()=>setEditing(null)} onOk={save} destroyOnClose width={mobile?'calc(100vw - 24px)':520} styles={{body:{maxHeight:mobile?'68vh':'none',overflowY:'auto'}}} okText="保存配置" cancelText="取消">
      <Form form={form} layout="vertical">
        <Form.Item name="greenhouse_id" label="所属大棚" rules={[{required:true}]}><Select options={greenhouses.map(g=>({value:g.id,label:g.name}))}/></Form.Item>
        <Form.Item name="point_name" label="采集点名称" rules={[{required:true,max:100}]}><Input placeholder="例如：1号棚东侧第3点"/></Form.Item>
        <Form.Item name="sample_seconds" label="采集时长（秒）" rules={[{required:true}]}><InputNumber min={1} max={120} style={{width:'100%'}}/></Form.Item>
        <div style={{display:'grid',gridTemplateColumns:mobile?'1fr 1fr':'repeat(4,1fr)',gap:'0 8px'}}><Form.Item name="collect_environment" valuePropName="checked"><Checkbox>环境数据</Checkbox></Form.Item><Form.Item name="collect_soil" valuePropName="checked"><Checkbox>土壤数据</Checkbox></Form.Item><Form.Item name="take_photo" valuePropName="checked"><Checkbox>拍照</Checkbox></Form.Item><Form.Item name="enabled" valuePropName="checked"><Checkbox>启用</Checkbox></Form.Item></div>
        <Form.Item name="note" label="备注"><Input.TextArea maxLength={500}/></Form.Item>
      </Form>
    </Modal>
  </Space>;
}
