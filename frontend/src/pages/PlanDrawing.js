import React, { useRef } from 'react';
import { layoutGeometry } from './pointMapLayout';

export default function PlanDrawing({ doc, selected, lastPoint, stale, onPoint, onPlace, placing }) {
  const l = doc.layout;
  const scale = Math.min(880/l.width, 300/l.height);
  const w = l.width*scale, h = l.height*scale, left=(1000-w)/2, top=(410-h)/2;
  const {aisle,lanes}=layoutGeometry(l);
  const px=x=>left+x*scale, py=y=>top+(l.height-y)*scale;
  const svgRef = useRef();
  const click = e => {
    if (!placing) return;
    const pt = svgRef.current.createSVGPoint(); pt.x=e.clientX; pt.y=e.clientY;
    const pos=pt.matrixTransform(svgRef.current.getScreenCTM().inverse());
    const x=(pos.x-left)/scale, y=l.height-(pos.y-top)/scale;
    if (x>=0 && x<=l.width && y>=0 && y<=l.height) onPlace({x:+x.toFixed(2),y:+y.toFixed(2)});
  };
  return <div style={{overflowX:'auto', background:'#f5faf7',borderRadius:12,border:'1px solid #dcebe2'}}>
    <svg ref={svgRef} viewBox="0 0 1000 420" role="img" aria-label="大棚采集点平面图" onClick={click} style={{display:'block',width:'100%',minWidth:650,cursor:placing?'crosshair':'default'}}>
      <defs><pattern id="bed-lines" width="12" height="12" patternUnits="userSpaceOnUse"><path d={l.planting_direction==='ew'?'M0 0H12':'M0 0V12'} stroke="#b9d9bf" strokeWidth="2"/></pattern></defs>
      <text x="500" y="32" textAnchor="middle" fill="#527a65" fontSize="13">种植{l.planting_direction==='ew'?'东西':'南北'}向 · 采集通道 {l.collection_lane_count||0} 条{l.collection_lane_count?`（${l.collection_direction==='ew'?'东西':'南北'}向）`:'（未规划）'}</text>
      <text x="26" y="32" fill="#2f6846" fontSize="15">北 ↑</text>
      <text x="975" y="32" textAnchor="end" fill="#64816f" fontSize="14">{l.calibrated?'棚内米制坐标':'示意布局 · 尺寸未标定'}</text>
      <rect x={left} y={top} width={w} height={h} fill="#e4efdf" stroke="#628b6b" strokeWidth="3" rx="3"/>
      {l.template!=='blank' && <rect x={left+2} y={top+2} width={Math.max(0,w-4)} height={Math.max(0,h-4)} fill="url(#bed-lines)"/>}
      {['glass','polycarbonate'].includes(l.template) && Array.from({length:l.bays-1},(_,i)=><line key={i} x1={left+w*(i+1)/l.bays} x2={left+w*(i+1)/l.bays} y1={top} y2={top+h} stroke="#8ea8b3" strokeWidth="2" strokeDasharray="6 4"/>)}
      <rect x={px(aisle.x)} y={py(aisle.y+aisle.h)} width={aisle.w*scale} height={aisle.h*scale} fill="#fff9e8" stroke="#d9cfae"/>
      {lanes.map(lane=><g key={lane.number} data-testid="collection-lane"><title>采集通道 {lane.number}</title>{lane.segments.map((s,i)=><line key={i} x1={px(s.x1)} y1={py(s.y1)} x2={px(s.x2)} y2={py(s.y2)} stroke="#5b91bf" strokeWidth="2" strokeDasharray="7 5"/>)}{lane.segments[0]&&<text x={px(lane.segments[0].x1)+5} y={py(lane.segments[0].y1)-5} fontSize="11" fill="#356a94">{lane.number}</text>}</g>)}
      <text x={left+w/2} y={top+h+28} textAnchor="middle" fill="#64816f" fontSize="14">{l.length_axis==='ns'?'宽':'长'} {l.width} {l.calibrated?'米':'（示意长度）'} · 东 →</text>
      <text x={left-10} y={top+h/2} textAnchor="end" fill="#64816f" fontSize="13">{l.length_axis==='ns'?'长':'宽'} {l.height}{l.calibrated?'m':''}</text>
      <text x={left} y={top+h+47} fill="#64816f" fontSize="12">西南角原点：X向东，Y向北</text>
      {doc.points.filter(p=>p.x!==null && p.y!==null).map(p=>{
        const x=left+p.x*scale,y=top+(l.height-p.y)*scale;
        const color=p.id===selected?'#1677ff':p.id===lastPoint?(stale?'#8c98a1':'#d48806'):p.binding?'#19865c':'#8c98a1';
        return <g key={p.id} role="button" tabIndex={0} aria-label={`采集点 ${p.point_name}`} onClick={e=>{e.stopPropagation();onPoint(p);}} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onPoint(p);}}} style={{cursor:'pointer'}}>
          <title>{p.point_name} · {p.binding?`导航${p.binding.nav_point_code}`:'待关联'}</title>
          {p.id===lastPoint && <circle cx={x} cy={y} r="18" fill="none" stroke={color} strokeWidth="2" strokeDasharray="4 3"/>}
          <circle cx={x} cy={y} r="10" fill={color} stroke="white" strokeWidth="3"/>
          <text x={x} y={y-19} textAnchor="middle" fontSize="15" fontWeight="600" fill="#244434" stroke="#f5faf7" strokeWidth="4" paintOrder="stroke">{p.point_name}</text>
        </g>;
      })}
    </svg>
  </div>;
}

