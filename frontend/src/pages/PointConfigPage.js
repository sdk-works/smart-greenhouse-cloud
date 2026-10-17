import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, Form, Grid, Input, InputNumber, List, Modal, Popconfirm, Select, Space, Tag, Typography, message } from 'antd';
import { EnvironmentOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import api, { greenhouseApi, pointConfigApi, formatUTCtoLocal } from '../services/api';
// 旧版入口暂停，恢复时启用此import及页尾的兼容Tabs。
// import LegacyPointConfigPage from './LegacyPointConfigPage';
import PlanDrawing from './PlanDrawing';
import { layoutToForm, formToLayout } from './pointMapLayout';
import { newPointId, nextName, editableDocument, matchVisit, staleArrival } from './pointMapUtils';

const ROBOT = 'ROB001';
const TITLES = { solar:'日光温室', glass:'连栋玻璃温室', polycarbonate:'连栋阳光板温室', blank:'自定义平面图' };
const flags = ['collect_environment', 'collect_soil', 'take_photo', 'enabled'];
const labels = ['环境数据', '土壤数据', '拍照', '启用采集'];
const errorText = e => typeof e.response?.data?.detail === 'string' ? e.response.data.detail : Array.isArray(e.response?.data?.detail) ? e.response.data.detail.map(x=>x.msg).join('；') : '操作失败，请检查输入或重新加载后重试';
const time = t => formatUTCtoLocal(t);

export function MapWorkspace({greenhouseId}) {
  const mobile=!Grid.useBreakpoint().lg;
  const [greenhouses,setGreenhouses]=useState([]), [gh,setGh]=useState('');
  const [doc,setDoc]=useState(null), [selected,setSelected]=useState(null), [placing,setPlacing]=useState(null);
  const [discoveries,setDiscoveries]=useState([]), [status,setStatus]=useState({}), [monitor,setMonitor]=useState({});
  const [busy,setBusy]=useState(false), [network,setNetwork]=useState(false), [now,setNow]=useState(Date.now());
  const [editing,setEditing]=useState(null), [settingsOpen,setSettingsOpen]=useState(false), [batchOpen,setBatchOpen]=useState(false);
  const [bindingCode,setBindingCode]=useState(null), [listenSince,setListenSince]=useState(null);
  const [form]=Form.useForm(), [settings]=Form.useForm(), [batch]=Form.useForm();
  const ghRef=useRef(gh); ghRef.current=gh;
  const saveLock=useRef(false);
  const refreshMonitor=useCallback(async()=>{
    try {
      const [d,s,m]=await Promise.all([pointConfigApi.discoveries(ROBOT),pointConfigApi.status(ROBOT),api.get(`/point-config/map-monitor/${ROBOT}`)]);
      setDiscoveries(d.data);setStatus(s.data);setMonitor(m.data);setNetwork(true);
    } catch {setNetwork(false);}
  },[]);
  useEffect(()=>{
    greenhouseApi.list().then(r=>{const gs=r.data||[];setGreenhouses(gs);setGh(greenhouseId||gs.find(g=>g.name==='西一棚')?.id||gs[0]?.id||'');}).catch(e=>message.error(errorText(e)));
    refreshMonitor();const timer=setInterval(refreshMonitor,5000), clock=setInterval(()=>setNow(Date.now()),1000);
    return()=>{clearInterval(timer);clearInterval(clock);};
  },[refreshMonitor,greenhouseId]);
  const loadMap=useCallback(async id=>{
    setDoc(null);setSelected(null);setPlacing(null);
    try{const r=await api.get(`/point-config/maps/${id}`);if(ghRef.current===id)setDoc(r.data);}
    catch(e){message.error(errorText(e));}
  },[]);
  useEffect(()=>{if(gh)loadMap(gh);},[gh,loadMap]);
  const persist=async updated=>{
    const r=await api.put(`/point-config/maps/${gh}`,editableDocument(updated));
    setDoc(r.data);refreshMonitor();return r.data;
  };
  const run=async fn=>{
    if(saveLock.current)return;saveLock.current=true;setBusy(true);
    try{await fn();}catch(e){if(!e.errorFields)message.error(errorText(e));}
    finally{saveLock.current=false;setBusy(false);}
  };
  const beginPoint=(p,coords)=>{
    const value=p || {id:newPointId(),point_name:nextName(doc.next_name,doc.points.map(x=>x.point_name),false),zone:'',note:'',x:null,y:null,...doc.defaults};
    setEditing(value);form.setFieldsValue({...value,...coords});
  };
  const savePoint=()=>run(async()=>{
    const values=await form.validateFields(); const p={...editing,...values};
    const exists=doc.points.some(x=>x.id===p.id);
    const points=exists?doc.points.map(x=>x.id===p.id?p:x):[...doc.points,p];
    const defaults=Object.fromEntries(['sample_seconds',...flags].map(k=>[k,p[k]]));
    await persist({...doc,points,defaults,next_name:nextName(p.point_name,points.map(x=>x.point_name))});
    setSelected(p.id);setEditing(null);
    if(placing!=='new')setPlacing(null);
    message.success('采集点已保存；采集参数与关联变更需发布后生效');
  });
  const changeBinding=code=>run(async()=>{
    const r=await api.put(`/point-config/maps/${gh}/points/${selected}/binding`,{revision:doc.revision,robot_id:ROBOT,nav_point_code:code});
    setDoc(r.data);setBindingCode(null);await refreshMonitor();message.success('关联已保存到草稿，请发布并等待工控机确认');
  });
  const publish=mode=>run(async()=>{
    const r=await pointConfigApi.publish(ROBOT,mode);
    if(mode==='discovery')setListenSince(Date.now());
    message.success(`配置v${r.data.config_version}已发送，等待工控机应用确认`);await refreshMonitor();
  });
  if(!gh)return <Card><Empty description="请先在大棚管理中创建大棚"/></Card>;
  const p=doc?.points.find(x=>x.id===selected);
  const arrival=monitor.latest_arrival;
  const lastPoint=doc && matchVisit(doc.points,arrival);
  const stale=staleArrival(arrival,now)||!network;
  const latestResult=p && (monitor.point_results||[]).find(v=>matchVisit([p],v));
  const incoming=discoveries.filter(d=>!listenSince || new Date(d.last_seen_at).getTime()>=listenSince);
  const bindingSummary=discoveries.find(d=>d.nav_point_code===bindingCode)?.assignment;
  const activePublished=p?.binding && (monitor.published_points||[]).find(x=>x.mapping_id===p.binding.mapping_id);
  return <Space direction="vertical" size={16} style={{width:'100%'}}>
    <Card styles={{body:{padding:18}}}>
      <Space wrap style={{width:'100%',justifyContent:'space-between'}}>
        <Space><EnvironmentOutlined style={{fontSize:25,color:'#25825d'}}/><div><Typography.Title level={4} style={{margin:0}}>大棚采集点图</Typography.Title><Typography.Text type="secondary">提前布点 · 监听关联 · 到点回看</Typography.Text></div></Space>
        <Space wrap>{!greenhouseId&&<Select aria-label="选择大棚" value={gh} disabled={busy||!!editing||settingsOpen} style={{minWidth:170}} onChange={setGh} options={greenhouses.map(g=>({value:g.id,label:g.name}))}/>}<Button icon={<ReloadOutlined/>} disabled={busy} onClick={()=>{loadMap(gh);refreshMonitor();}}>重新加载</Button></Space>
      </Space>
    </Card>
    <Alert type={!network?'warning':monitor.draft_differs?'warning':'info'} showIcon message={!network?'监测连接中断，以下为最后收到的信息':`工控机：v${status.config_version||0} · ${status.mode==='operational'?'正式采集':'发现模式'} · ${{applied:'已应用',pending:'等待确认',publish_failed:'发布失败'}[status.sync_status]||status.sync_status||'未发布'}${monitor.draft_differs?' · 有未发布的采集配置':''}`} description="图上只标记最近一次确认到点，不代表连续实时定位。发现模式只登记编号、不执行采集；发布作用于ROB001全部已关联点。"/>
    <Space wrap>
      <Popconfirm title="切换为监听配置？" description="会下发发现模式，暂停到点采集。现有点位和关联保留。请在停车后操作。" onConfirm={()=>publish('discovery')}><Button loading={busy}>开启监听配置</Button></Popconfirm>
      <Button onClick={()=>setListenSince(listenSince?null:Date.now())}>{listenSince?'显示全部监听记录':'仅看此后收到的编号'}</Button>
      <Popconfirm title="发布全部已关联点并进入正式采集？" description="请在停车后操作。未关联的图上点不下发；等待“已应用”后再测试。" onConfirm={()=>publish('operational')}><Button type="primary" loading={busy}>发布采集配置</Button></Popconfirm>
      <Tag color={network?'green':'orange'}>{network?'每5秒更新':'连接异常'}</Tag>
    </Space>
    {doc ? <div style={{display:'grid',gridTemplateColumns:mobile?'1fr':'minmax(0,1fr) 340px',gap:16}}>
      <Space direction="vertical" size={16} style={{minWidth:0,width:'100%'}}>
        <Card title={<Space wrap><span>{greenhouses.find(g=>g.id===gh)?.name} · {TITLES[doc.layout.template]}</span><Tag>{doc.points.length}个点</Tag></Space>} extra={<Button onClick={()=>{settings.setFieldsValue({...layoutToForm(doc.layout),...doc.defaults});setSettingsOpen(true);}}>布局与默认参数</Button>} styles={{body:{padding:14}}}>
          <Space wrap style={{marginBottom:12}}>
            <Button type={placing==='new'?'primary':'default'} icon={<PlusOutlined/>} onClick={()=>setPlacing(placing==='new'?null:'new')}>{placing==='new'?'结束连续布点':'图上连续布点'}</Button>
            <Button onClick={()=>beginPoint(null)}>新增待放置点</Button>
            <Button disabled={!doc.points.length} onClick={()=>{batch.setFieldsValue({...doc.defaults,ids:doc.points.map(x=>x.id)});setBatchOpen(true);}}>批量改采集参数</Button>
          </Space>
          {placing && <Alert style={{marginBottom:10}} type="info" message={placing==='new'?'点击棚内位置添加点；保存后可继续点击下一个位置。':'点击棚内位置，为选中的点设置坐标。'} action={<Button size="small" onClick={()=>setPlacing(null)}>取消放置</Button>}/>}
          <PlanDrawing doc={doc} selected={selected} lastPoint={lastPoint?.id} stale={stale} placing={placing} onPoint={pt=>{setSelected(pt.id);setPlacing(null);}} onPlace={coords=>beginPoint(placing==='new'?null:doc.points.find(x=>x.id===placing),coords)}/>
          <Space wrap style={{marginTop:12}}><Tag>灰：待关联</Tag><Tag color="green">绿：已关联草稿</Tag><Tag color="blue">蓝：当前选中</Tag><Tag color="gold">金色圈：最近确认到点</Tag></Space>
          <div style={{marginTop:8,color:'#60726a',fontSize:12}}>蓝色虚线为采集通道，绿色细线为种植行；均为布局示意，不改变厂家导航路线。位置可在点位编辑中输入，手机可横向滑动地图。</div>
        </Card>
        <Card size="small" title="最近确认到点">
          {arrival?<><Space wrap><Tag color={stale?'default':'gold'}>{stale?'历史到点 · 非当前位置':'最近收到的到点'}</Tag><b>{arrival.point_name||`导航${arrival.nav_point_code}`}</b><span>导航编号 {arrival.nav_point_code}</span></Space><div style={{marginTop:8}}>到达时间：{time(arrival.arrived_at)} · {arrival.status==='sampled'?'采集结果已收到':arrival.status==='arrived'?'已到达，等待采集结果':arrival.status}</div><div style={{color:'#777',marginTop:6}}>{lastPoint?`${lastPoint.point_name}${lastPoint.x===null?'尚未放置到图上':''}；标记使用当前配置的点位位置。`:'此事件无法关联到当前大棚的点位图。'} 超过60秒仅作为历史到点展示。</div></>:<Empty description="尚未收到到点事件；监听编号不等于确认实际位置"/>}
        </Card>
        <Card size="small" title="图上点位" extra={<span style={{color:'#888'}}>未放置 {doc.points.filter(x=>x.x===null).length} 个</span>}>
          <List dataSource={doc.points} locale={{emptyText:'先添加计划采集的位置，再监听导航编号关联'}} renderItem={pt=><List.Item style={{background:selected===pt.id?'#f0f8f3':undefined,padding:'10px 8px'}} actions={[<Button key="select" size="small" onClick={()=>setSelected(pt.id)}>选择</Button>,<Button key="place" size="small" onClick={()=>{setSelected(pt.id);setPlacing(pt.id);}}>放置</Button>]}><List.Item.Meta title={<Space><b>{pt.point_name}</b>{pt.zone&&<span>{pt.zone}</span>}<Tag color={pt.binding?'green':'default'}>{pt.binding?`导航${pt.binding.nav_point_code}`:'待关联'}</Tag></Space>} description={`${pt.x===null?'位置未设置':`X ${pt.x} / Y ${pt.y}${doc.layout.calibrated?' 米':'（示意）'}`} · 采样${pt.sample_seconds}秒${pt.enabled?'':' · 停用'}`}/></List.Item>}/>
        </Card>
      </Space>
      <Space direction="vertical" size={16} style={{width:'100%',minWidth:0}}>
        <Card size="small" title={p?`选中点位 · ${p.point_name}`:'点位详情'}>
          {p?<Space direction="vertical" style={{width:'100%'}}><div>区域：{p.zone||'未分区'}</div><div>位置：{p.x===null?'待放置':`X ${p.x}，Y ${p.y}${doc.layout.calibrated?' 米':'（示意）'}`}</div><div>采样：{p.sample_seconds}秒 · {p.collect_soil?'土壤 ':''}{p.collect_environment?'环境 ':''}{p.take_photo?'拍照':''}</div><div>关联：{p.binding?`导航${p.binding.nav_point_code}`:'未关联'}</div><div>下发：{activePublished && status.sync_status==='applied'?'有关联发布记录，请留意上方是否有草稿变更':'当前关联尚未确认应用'}</div><Space wrap><Button onClick={()=>beginPoint(p)}>编辑点位</Button><Button onClick={()=>setPlacing(p.id)}>图上放置</Button>{p.binding?<Popconfirm title="解除导航关联？" description="图上点和历史数据保留，发布后工控机才停止按原关联采集。" onConfirm={()=>changeBinding(null)}><Button danger disabled={busy}>解除关联</Button></Popconfirm>:<Popconfirm title="删除这个未关联点？" onConfirm={()=>run(async()=>{await persist({...doc,points:doc.points.filter(x=>x.id!==p.id)});setSelected(null);})}><Button danger>删除点位</Button></Popconfirm>}</Space>{latestResult&&<Alert type="info" message="此点最近一次结果" description={`${time(latestResult.arrived_at)} · ${latestResult.status==='sampled'?'已收到采集结果':'已到达，等待结果'}`}/>}</Space>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="点击图上或列表中的采集点"/>}
        </Card>
        <Card size="small" title="导航报文监听" extra={<Tag>{listenSince?'本次筛选':'全部记录'}</Tag>}>
          <div style={{color:'#777',marginBottom:10}}>先选择图上点，再选收到的导航编号。重复编号合并计数；不同路线同编号需先解除旧关联。</div>
          <Select aria-label="选择导航编号" style={{width:'100%',marginBottom:10}} placeholder="选择收到的导航编号" value={bindingCode} onChange={setBindingCode} showSearch options={incoming.map(d=>({value:d.nav_point_code,label:`导航${d.nav_point_code}${d.assignment?` → ${d.assignment.point_name}`:' · 未分配'}`}))}/>
          <Popconfirm title={`将导航${bindingCode}关联到${p?.point_name}？`} description="请核对小车实际所在位置。关联先保存到草稿。" onConfirm={()=>changeBinding(bindingCode)}><Button block type="primary" disabled={!p||!!p.binding||!bindingCode||!!bindingSummary||busy}>关联到 {p?.point_name||'选中点位'}</Button></Popconfirm>
          {bindingSummary&&<div style={{color:'#ad6800',marginTop:8}}>此编号已分配给 {greenhouses.find(g=>g.id===bindingSummary.greenhouse_id)?.name}／{bindingSummary.point_name}，需先解除原关联。</div>}
          <List style={{maxHeight:400,overflowY:'auto',marginTop:12}} size="small" dataSource={incoming} locale={{emptyText:'此筛选范围内还没有收到编号'}} renderItem={d=><List.Item onClick={()=>setBindingCode(d.nav_point_code)} style={{cursor:'pointer',background:bindingCode===d.nav_point_code?'#f0f8f3':undefined}}><div><Space><b>导航 {d.nav_point_code}</b><Tag>{d.seen_count} 次</Tag>{d.assignment&&<Tag color="green">{d.assignment.point_name}</Tag>}</Space><div style={{fontSize:12,color:'#777'}}>{time(d.last_seen_at)}</div></div></List.Item>}/>
        </Card>
      </Space>
    </div>:<Card loading/>}
    <Modal title={editing?`配置采集点`:'采集点'} open={!!editing} onCancel={()=>{if(!busy)setEditing(null);}} onOk={savePoint} confirmLoading={busy} okText="保存点位" cancelText="取消" destroyOnClose>
      <Form form={form} layout="vertical"><Form.Item name="point_name" label="点位名称（保存后自动建议下一个编号）" rules={[{required:true,whitespace:true,max:100}]}><Input/></Form.Item><Form.Item name="zone" label="所属区域"><Input placeholder="例如：中区、北侧通道" maxLength={100}/></Form.Item>
        <Space align="start"><Form.Item name="x" label={`X 向东${doc?.layout.calibrated?'（米）':'（示意）'}`}><InputNumber min={0} max={doc?.layout.width} precision={2}/></Form.Item><Form.Item name="y" label={`Y 向北${doc?.layout.calibrated?'（米）':'（示意）'}`}><InputNumber min={0} max={doc?.layout.height} precision={2}/></Form.Item></Space>
        <Form.Item name="sample_seconds" label="连续采样时长（秒）" extra="本次保存的采集参数会用于下一个新增点；不等于探针稳定等待时间。" rules={[{required:true}]}><InputNumber min={1} max={120} precision={0}/></Form.Item><Space wrap>{flags.map((k,i)=><Form.Item key={k} name={k} valuePropName="checked"><Checkbox>{labels[i]}</Checkbox></Form.Item>)}</Space><Form.Item name="note" label="备注"><Input.TextArea maxLength={500}/></Form.Item>
      </Form>
    </Modal>
    <Modal title="大棚布局与新增点默认参数" open={settingsOpen} onCancel={()=>{if(!busy)setSettingsOpen(false);}} confirmLoading={busy} okText="保存布局" cancelText="取消" onOk={()=>run(async()=>{const v=await settings.validateFields();await persist({...doc,layout:formToLayout(v),defaults:Object.fromEntries(['sample_seconds',...flags].map(k=>[k,v[k]]))});setSettingsOpen(false);message.success('布局与新增点默认值已保存，已有点采集参数保持原值');})}>
      <Form form={settings} layout="vertical"><Form.Item name="template" label="布局模板"><Select options={Object.entries(TITLES).map(([value,label])=>({value,label}))}/></Form.Item><Space><Form.Item name="length_value" label="大棚长度" rules={[{required:true}]}><InputNumber min={1} max={2000}/></Form.Item><Form.Item name="width_value" label="大棚宽度" rules={[{required:true}]}><InputNumber min={1} max={2000}/></Form.Item></Space><Form.Item name="length_axis" label="长度对应方向"><Select options={[{value:'ew',label:'长度沿东西方向，宽度沿南北方向'},{value:'ns',label:'长度沿南北方向，宽度沿东西方向'}]}/></Form.Item><Form.Item name="planting_direction" label="种植行方向"><Select options={[{value:'ns',label:'南北向'},{value:'ew',label:'东西向'}]}/></Form.Item><Space wrap><Form.Item name="collection_lane_count" label="采集通道数量（趟数）" extra="0表示尚未规划" rules={[{required:true}]}><InputNumber min={0} max={50} precision={0}/></Form.Item><Form.Item name="collection_direction" label="采集通道方向"><Select style={{width:140}} options={[{value:'ns',label:'南北向'},{value:'ew',label:'东西向'}]}/></Form.Item></Space><Form.Item name="calibrated" valuePropName="checked"><Checkbox>长宽已按现场米数填写，点位使用米制坐标</Checkbox></Form.Item><Space><Form.Item name="aisle_side" label="主通道位置"><Select style={{width:130}} options={[{value:'north',label:'北侧'},{value:'south',label:'南侧'},{value:'east',label:'东侧'},{value:'west',label:'西侧'},{value:'center',label:'中间（东西向）'}]}/></Form.Item><Form.Item name="aisle_width" label="主通道宽度" rules={[{required:true}]}><InputNumber min={0.3} max={20}/></Form.Item><Form.Item name="bays" label="连栋跨数" rules={[{required:true}]}><InputNumber min={1} max={30} precision={0}/></Form.Item></Space><Form.Item name="sample_seconds" label="新增点默认采样时长（秒）" rules={[{required:true}]}><InputNumber min={1} max={120} precision={0}/></Form.Item><Space wrap>{flags.map((k,i)=><Form.Item key={k} name={k} valuePropName="checked"><Checkbox>{labels[i]}</Checkbox></Form.Item>)}</Space><Alert type="info" message="长度方向改变会调整棚体尺寸；已有点坐标保持不变，越界时拒绝保存。采集通道虚线是等距规划示意，不自动生成导航路线。"/></Form>
    </Modal>
    <Modal title="批量修改采集参数" open={batchOpen} onCancel={()=>setBatchOpen(false)} confirmLoading={busy} okText="保存选中点" cancelText="取消" onOk={()=>run(async()=>{const v=await batch.validateFields();const defaults=Object.fromEntries(['sample_seconds',...flags].map(k=>[k,v[k]]));await persist({...doc,points:doc.points.map(p=>v.ids.includes(p.id)?{...p,...defaults}:p)});setBatchOpen(false);message.success('批量参数已保存，请发布后使用');})}>
      <Form form={batch} layout="vertical"><Form.Item name="ids" label="修改哪些点" rules={[{required:true,type:'array',min:1}]}><Select mode="multiple" options={(doc?.points||[]).map(p=>({value:p.id,label:p.point_name}))}/></Form.Item><Form.Item name="sample_seconds" label="采样时长（秒）" rules={[{required:true}]}><InputNumber min={1} max={120} precision={0}/></Form.Item><Space wrap>{flags.map((k,i)=><Form.Item key={k} name={k} valuePropName="checked"><Checkbox>{labels[i]}</Checkbox></Form.Item>)}</Space></Form>
    </Modal>
  </Space>;
}

// 旧版保留源文件LegacyPointConfigPage.js，暂不加载和展示。
// 恢复时导入Tabs与LegacyPointConfigPage，并将以下组件改回双页签。
// <Tabs items={[{key:'map',label:'图上规划与监听',children:<MapWorkspace/>},
// {key:'legacy',label:'旧版配置（兼容）',children:<LegacyPointConfigPage/>}]}/>
export default function PointConfigPage(){return <MapWorkspace/>;}
