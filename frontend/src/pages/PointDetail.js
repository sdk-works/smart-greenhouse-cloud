import PointPhotos from './PointPhotos';
import React,{useEffect,useState} from 'react';
import {Alert,Card,Descriptions,Drawer,Empty,Image,Select,Space,Table,Tabs,Tag} from 'antd';
import ReactECharts from 'echarts-for-react';
import api,{formatUTCtoLocal as time} from '../services/api';
export const METRICS={temperature:['气温','℃'],humidity:['空气湿度','%'],light_intensity:['光照','lx'],co2_level:['CO₂','ppm'],soil_moisture:['土壤水分','%'],soil_temperature:['土温','℃'],soil_ec:['土壤电导率','μS/cm'],ph_value:['pH',''],soil_nitrogen:['氮','mg/kg'],soil_phosphorus:['磷','mg/kg'],soil_potassium:['钾','mg/kg']};
export function reading(row,key){
  const q=row?.quality||{};
  if(key.startsWith('soil_')||key==='ph_value'){if(q.soil_valid===false)return null;}
  else if(q.environment_present===false)return null;
  const v=row?.values?.[key];return typeof v==='number'&&Number.isFinite(v)?v:null;
}
export default function PointDetail({greenhouseId,pointId,onClose}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[days,setDays]=useState(7),[metric,setMetric]=useState('temperature');
  useEffect(()=>{if(!pointId)return;let active=true;setData(null);setError('');
    api.get(`/workbench/greenhouses/${greenhouseId}/points/${pointId}`,{params:{days}}).then(r=>{if(active)setData(r.data);}).catch(()=>{if(active)setError('点位详情加载失败，请关闭后重试。');});return()=>{active=false;};
  },[greenhouseId,pointId,days]);
  const latest=data?.observations[0], rows=data?.observations||[], p=data?.point;
  const trend={tooltip:{trigger:'axis',valueFormatter:v=>v===null?'无有效读数':`${v} ${METRICS[metric][1]}`},grid:{left:65,right:20,bottom:55,top:20},xAxis:{type:'time'},yAxis:{type:'value',name:METRICS[metric][1],scale:true},series:[{type:'scatter',symbolSize:8,data:[...rows].reverse().map(r=>[r.captured_at,reading(r,metric)]).filter(x=>x[1]!==null)}]};
  return <Drawer title={p?`${p.point_name} · 采集记录`:'采集点详情'} open={!!pointId} onClose={onClose} width="min(900px, 100vw)">
    {error?<Alert type="error" message={error}/>:!data?<Card loading/>:<Space direction="vertical" size={16} style={{width:'100%'}}>
      <Descriptions size="small" column={2} items={[{key:'zone',label:'区域',children:p.zone||'未设置'},{key:'nav',label:'当前导航编号',children:p.binding?.nav_point_code||'未关联'},{key:'xy',label:'图上位置',children:p.x===null?'待放置':`X ${p.x} / Y ${p.y}`},{key:'time',label:'采样时长',children:`${p.sample_seconds} 秒`}]}/>
      <Space wrap><span>查看范围</span><Select aria-label="历史时间范围" value={days} onChange={setDays} options={[{value:7,label:'近7天'},{value:30,label:'近30天'},{value:90,label:'近90天'},{value:365,label:'近一年'}]}/><Tag>{data.total} 次数据记录</Tag></Space>
      {data.truncated&&<Alert type="warning" message="当前展示最近500条，请缩短时间范围；统计总数包含全部记录。"/>}
      {data.position_history.filter(h=>h.x!==null).length>1&&<Alert type="warning" message="此点位的图上坐标曾调整，下方历史包含调整前后的记录。比较趋势前请核对实际位置。"/>}
      <Tabs items={[
        {key:'latest',label:'最近数据',children:latest?<><Alert type="info" message={`采集：${time(latest.captured_at)}`} description={`上传接收：${time(latest.received_at)}；此为所选范围内最近一次采集，不代表实时值。`}/><Descriptions style={{marginTop:16}} bordered size="small" column={2} items={Object.entries(METRICS).map(([key,[label,unit]])=>({key,label,children:reading(latest,key)===null?'无有效读数':`${reading(latest,key)} ${unit}`}))}/><p>来源：{latest.quality.source||'未标注'}；土壤样本数：{latest.sample_count??latest.quality.soil_sample_count??'未标注'}。有读数不等于已完成传感器精度校验。</p></>:<Empty description="所选时间范围内暂无数据"/>},
        {key:'trend',label:'历史趋势',children:<><Select aria-label="趋势指标" style={{minWidth:180}} value={metric} onChange={setMetric} options={Object.entries(METRICS).map(([value,[label,unit]])=>({value,label:`${label}${unit?`（${unit}）`:''}`}))}/>{rows.length?<ReactECharts option={trend} notMerge style={{height:320}}/>:<Empty description="暂无趋势数据"/>}<p>每个圆点代表一次采集；缺失或标记无效的读数不绘制。点位改名、重新对应导航编号后，历史仍保留在这里。</p></>},
        {key:'photos',label:`照片（${data.photo_total??data.photos.length}）`,children:<PointPhotos greenhouseId={greenhouseId} pointId={pointId} days={days} initialPhotos={data.photos} total={data.photo_total??data.photos.length}/>},
        {key:'records',label:'采集与到访明细',children:<><Table rowKey="id" size="small" dataSource={rows} scroll={{x:680}} pagination={{pageSize:10}} columns={[{title:'采集时间',dataIndex:'captured_at',render:time},{title:'上传接收时间',dataIndex:'received_at',render:time},{title:'当时点位名',dataIndex:'point_name'},{title:'任务编号',dataIndex:'task_id'}]}/><Table rowKey="id" size="small" dataSource={data.visits} pagination={{pageSize:10}} columns={[{title:'到达时间',dataIndex:'arrived_at',render:time},{title:'结果',dataIndex:'status',render:v=>({sampled:'采集结果已收到',arrived:'已到达，未收到结果'}[v]||v)},{title:'异常',dataIndex:'error_code',render:v=>v||'—'}]}/></>}
      ]}/>
    </Space>}
  </Drawer>;
}
