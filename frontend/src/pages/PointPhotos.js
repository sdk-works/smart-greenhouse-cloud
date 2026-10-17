import React,{useEffect,useState} from 'react';
import {Alert,Card,Empty,Image,Pagination,Spin} from 'antd';
import api,{formatUTCtoLocal as time} from '../services/api';
const placeholder="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='240'%3E%3Crect width='100%25' height='100%25' fill='%23edf3ef'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' fill='%23666'%3E点击查看原图%3C/text%3E%3C/svg%3E";
export default function PointPhotos({greenhouseId,pointId,days,initialPhotos,total}){
  const [page,setPage]=useState(1),[items,setItems]=useState(initialPhotos),[loading,setLoading]=useState(false),[error,setError]=useState('');
  useEffect(()=>{if(page===1){setItems(initialPhotos);setError('');return;}let active=true;setLoading(true);setError('');
    api.get('/photo-catalog',{params:{greenhouse_id:greenhouseId,point_id:pointId,page,page_size:24,start:new Date(Date.now()-days*86400000).toISOString()}}).then(r=>{if(active)setItems(r.data.items.map(x=>({...x,url:x.storage_url})));}).catch(()=>{if(active){setItems([]);setError('照片加载失败，请切换页码重试');}}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};
  },[page,initialPhotos,greenhouseId,pointId,days]);
  return <><Alert type="info" message="每页24张缩略图，点击放大时加载原图。只展示能确定任务对应关系的照片。"/>{error&&<Alert type="error" message={error}/>}<Spin spinning={loading}><Image.PreviewGroup><div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(180px,1fr))',gap:12,marginTop:16}}>{items.map(im=><Card key={im.id} size="small"><Image loading="lazy" src={im.thumbnail_url||placeholder} preview={{src:im.url}} width="100%"/><div>{time(im.captured_at)}</div><div style={{fontSize:11,overflowWrap:'anywhere'}}>任务：{im.task_id}</div></Card>)}</div></Image.PreviewGroup>{!loading&&!items.length&&<Empty description="暂无已确认关联的照片"/>}</Spin><Pagination current={page} pageSize={24} total={total} showSizeChanger={false} onChange={setPage} style={{marginTop:16}}/></>;
}
