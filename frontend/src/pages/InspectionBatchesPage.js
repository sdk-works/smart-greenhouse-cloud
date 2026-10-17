import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Drawer, Empty, Input, Space, Spin, Tag, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { inspectionBatchApi, formatUTCtoLocal } from '../services/api';

const statusMap = {
  running:['运行中','green'], partial:['部分完成/中断','orange'],
  completed:['已完成','blue'], interrupted:['已中断','red'], cancelled:['已取消','default'],
};

export default function InspectionBatchesPage() {
  const [rows,setRows]=useState([]), [loading,setLoading]=useState(false);
  const [detail,setDetail]=useState(null), [label,setLabel]=useState('');
  const load=async()=>{setLoading(true);try{setRows((await inspectionBatchApi.list()).data||[]);}catch(e){message.error('批次加载失败');}finally{setLoading(false);}};
  useEffect(()=>{load();const t=setInterval(load,30000);return()=>clearInterval(t);},[]);
  const open=async(id)=>{try{const d=(await inspectionBatchApi.get(id)).data;setDetail(d);setLabel(d.navigation_route_label||'');}catch(e){message.error('详情加载失败');}};
  const save=async()=>{try{await inspectionBatchApi.setRouteLabel(detail.id,label||null);message.success('路线备注已保存');open(detail.id);load();}catch(e){message.error('保存失败');}};
  return <div style={{maxWidth:1200,margin:'0 auto'}}>
    <Alert showIcon type="info" style={{marginBottom:12}} message="批次由真实到点/采集事件自动生成"
      description="当前平台不控制厂家导航。路线默认显示未知；15分钟没有新的到点事件会标记为部分完成/中断，不会误报正常完成。" />
    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}><b style={{fontSize:18}}>自动巡检批次</b><Button icon={<ReloadOutlined/>} onClick={load}>刷新</Button></div>
    <Spin spinning={loading}>{rows.length===0?<Empty description="尚无真实巡检批次；收到下一次到点或采集数据后会自动出现"/>:
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(280px,1fr))',gap:12}}>{rows.map(r=>{
        const st=statusMap[r.status]||[r.status,'default']; return <Card key={r.id} hoverable onClick={()=>open(r.id)} bodyStyle={{padding:16}}>
          <div style={{display:'flex',justifyContent:'space-between'}}><b>{r.robot_id}</b><Tag color={st[1]}>{st[0]}</Tag></div>
          <div style={{marginTop:10,color:'#60756d'}}>开始：{formatUTCtoLocal(r.started_at)}</div>
          <div style={{marginTop:6}}>厂家路线：<b>{r.navigation_route_label||'未知'}</b></div>
          <Space wrap style={{marginTop:10}}><Tag>到点 {r.visit_count}</Tag><Tag color="green">已采集 {r.sampled_count}</Tag><Tag>不同点位 {r.distinct_point_count}</Tag></Space>
        </Card>})}</div>}</Spin>
    <Drawer title="巡检批次详情" width={Math.min(620,window.innerWidth)} open={!!detail} onClose={()=>setDetail(null)}>
      {detail&&<><Alert type="warning" showIcon message="路线名称仅为人工备注，不代表云端已经控制导航" style={{marginBottom:14}}/>
        <div style={{marginBottom:14}}>状态：<Tag color={(statusMap[detail.status]||[])[1]}>{(statusMap[detail.status]||[detail.status])[0]}</Tag></div>
        <Space.Compact style={{width:'100%',marginBottom:18}}><Input value={label} onChange={e=>setLabel(e.target.value)} placeholder="可选：填写厂家App中实际选择的路线"/><Button type="primary" onClick={save}>保存</Button></Space.Compact>
        {(detail.visits||[]).map((v,i)=><Card size="small" key={v.id} style={{marginBottom:8}}><div style={{display:'flex',justifyContent:'space-between'}}><b>{i+1}. 导航点 {v.nav_point_code}</b><Tag color={v.status==='sampled'?'green':'orange'}>{v.status==='sampled'?'采集完成':'已到达'}</Tag></div><div style={{color:'#718078',marginTop:6}}>{v.point_name||'未映射业务点位'} · {formatUTCtoLocal(v.arrived_at)}</div></Card>)}
      </>}
    </Drawer>
  </div>;
}
