import React, { useState, useEffect } from 'react';
import { Select, Tabs, Button, Spin } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { telemetryApi, greenhouseApi } from '../services/api';
import { formatUTCtoLocal } from '../services/api';

const C = {
  teal:'#00D9A5', tealDk:'#00A87A',
  border:'rgba(0,217,165,0.18)', muted:'#8aab9e', text:'#2d4a3e',
};

function GlassCard({ title, icon, children }) {
  return (
    <div style={{
      background:'rgba(255,255,255,0.68)', backdropFilter:'blur(14px)',
      border:`1px solid ${C.border}`, borderRadius:12, overflow:'hidden',
      position:'relative', boxShadow:'0 4px 20px rgba(0,100,70,0.06)',
    }}>
      <div style={{ position:'absolute', top:0, left:'15%', right:'15%', height:1, background:`linear-gradient(90deg,transparent,${C.teal}55,transparent)` }} />
      {title && (
        <div style={{ fontSize:12, color:C.tealDk, fontWeight:700, padding:'10px 14px', borderBottom:`1px solid rgba(0,217,165,0.08)`, display:'flex', alignItems:'center', gap:6 }}>
          <span>{icon}</span>{title}
        </div>
      )}
      <div style={{ padding:14 }}>{children}</div>
    </div>
  );
}

const CHARTS = [
  { metric:'temperature',    label:'温度',   color:'#FF7043', unit:'°C',   max:50    },
  { metric:'humidity',       label:'湿度',   color:'#4FC3F7', unit:'%',    max:100   },
  { metric:'soil_moisture',  label:'土壤湿度', color:'#81C784', unit:'%',  max:100   },
  { metric:'soil_temperature',label:'土壤温度', color:'#FF8A65', unit:'°C', max:50    },
  { metric:'soil_ec',        label:'土壤EC', color:'#26A69A', unit:'μS/cm', max:3000 },
  { metric:'ph_value',       label:'土壤pH', color:'#AB47BC', unit:'', max:14       },
  { metric:'soil_nitrogen',  label:'土壤氮', color:'#42A5F5', unit:'mg/kg', max:500 },
  { metric:'soil_phosphorus',label:'土壤磷', color:'#FFA726', unit:'mg/kg', max:500 },
  { metric:'soil_potassium', label:'土壤钾', color:'#66BB6A', unit:'mg/kg', max:500 },
  { metric:'co2_level',      label:'CO₂',   color:'#CE93D8', unit:'ppm',  max:1500  },
  { metric:'light_intensity',label:'光照强度', color:'#FFB74D', unit:'lux', max:50000 },
  { metric:'battery_level',  label:'电量',   color:'#00D9A5', unit:'%',    max:100   },
];

export default function DataCenter() {
  const [telemetry,   setTelemetry]   = useState([]);
  const [greenhouses, setGreenhouses] = useState([]);
  const [selectedGh,  setSelectedGh]  = useState(null);
  const [hours,       setHours]       = useState(24);
  const [loading,     setLoading]     = useState(false);

  useEffect(() => {
    greenhouseApi.list().then(r => {
      setGreenhouses(r.data);
      if (r.data.length > 0) setSelectedGh(r.data[0].id);
    });
  }, []);
  useEffect(() => { if (selectedGh) load(); }, [selectedGh, hours]);

  const load = async () => {
    setLoading(true);
    try {
      const r = await telemetryApi.list({ greenhouse_id:selectedGh, hours, limit:200 });
      setTelemetry([...r.data].reverse());
    } finally { setLoading(false); }
  };

  // 清新折线图配置
  const makeChart = (metric, label, color, unit, max) => ({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: C.border,
      textStyle: { color: C.text, fontSize: 12 },
      formatter: params => {
        if (!params[0]) return '';
        const ts = telemetry[params[0].dataIndex]?.timestamp;
        return `${ts ? formatUTCtoLocal(ts, 'short') : ''}<br/>
                <span style="color:${color}">●</span> ${label}: <b>${params[0].value}${unit}</b>`;
      },
    },
    grid: { left:44, right:12, top:16, bottom:44 },
    xAxis: {
      type: 'category',
      data: telemetry.map(t => formatUTCtoLocal(t.timestamp, 'short')),
      axisLabel: { color:C.muted, fontSize:10, rotate:25 },
      axisLine:  { lineStyle: { color:'rgba(0,0,0,0.06)' } },
      splitLine: { show:false },
    },
    yAxis: {
      type: 'value',
      max,
      axisLabel: { color:C.muted, fontSize:10 },
      splitLine: { lineStyle: { color:'rgba(0,0,0,0.04)' } },
    },
    series: [{
      type: 'line', smooth: true, symbol: 'none',
      data: telemetry.map(t => t[metric] != null ? +t[metric].toFixed(2) : null),
      itemStyle: { color },
      lineStyle: { width:2 },
      areaStyle: { color: { type:'linear', x:0, y:0, x2:0, y2:1, colorStops:[{ offset:0, color:color+'44' },{ offset:1, color:color+'00' }] } },
      connectNulls: false,
    }],
  });

  return (
    <div className="fade-in">
      {/* 顶部 */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:16 }}>
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <div style={{ width:3, height:18, background:C.teal, borderRadius:2 }} />
          <span style={{ color:C.text, fontSize:15, fontWeight:700 }}>数据中心</span>
          <span style={{ fontSize:12, color:C.muted }}>（{telemetry.length} 条记录）</span>
        </div>
        <div style={{ display:'flex', gap:8 }}>
          <Select value={selectedGh} onChange={setSelectedGh} style={{ width:160 }} placeholder="选择大棚">
            {greenhouses.map(g => <Select.Option key={g.id} value={g.id}>{g.name}</Select.Option>)}
          </Select>
          <Select value={hours} onChange={setHours} style={{ width:100 }}>
            {[1,6,12,24,48,168].map(h => <Select.Option key={h} value={h}>{h<24?`${h}小时`:`${h/24}天`}</Select.Option>)}
          </Select>
          <Button icon={<ReloadOutlined />} onClick={load} style={{ borderColor:C.border, color:C.muted }}>刷新</Button>
        </div>
      </div>

      <Tabs
        defaultActiveKey="charts"
        items={[
          {
            key:'charts', label:'📈 趋势图表',
            children: (
              <Spin spinning={loading}>
                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginTop:12 }}>
                  {CHARTS.map(c => (
                    <GlassCard key={c.metric} title={c.label} icon={<span style={{ color:c.color, fontSize:13 }}>●</span>}>
                      {telemetry.length > 0 ? (
                        <ReactECharts option={makeChart(c.metric,c.label,c.color,c.unit,c.max)} style={{ height:180 }} />
                      ) : (
                        <div style={{ height:180, display:'flex', alignItems:'center', justifyContent:'center', color:C.muted, fontSize:12 }}>
                          等待数据...
                        </div>
                      )}
                    </GlassCard>
                  ))}
                </div>
              </Spin>
            ),
          },
          {
            key:'table', label:`📋 原始数据（${telemetry.length}条）`,
            children: (
              <div style={{ marginTop:12, borderRadius:12, overflow:'hidden', border:`1px solid ${C.border}`, background:'rgba(255,255,255,0.68)', backdropFilter:'blur(14px)' }}>
                <div style={{ overflowX:'auto' }}>
                  <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                    <thead>
                      <tr style={{ background:'rgba(0,217,165,0.07)' }}>
                        {['时间（本地）','任务','土壤水分%','土温°C','EC μS/cm','pH','氮 mg/kg','磷 mg/kg','钾 mg/kg','样本数','空气温度°C','湿度%','CO₂ppm','光照lux','电量%'].map(h => (
                          <th key={h} style={{ padding:'9px 12px', textAlign:'left', color:C.tealDk, fontWeight:700, borderBottom:`1px solid rgba(0,217,165,0.12)`, whiteSpace:'nowrap' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {telemetry.slice(0,100).map((t,i) => (
                        <tr key={t.id} style={{ borderBottom:'1px solid rgba(0,0,0,0.03)', background: i%2===0 ? 'transparent' : 'rgba(0,217,165,0.02)' }}>
                          <td style={{ padding:'7px 12px', color:C.muted, whiteSpace:'nowrap' }}>{formatUTCtoLocal(t.timestamp)}</td>
                          <td style={{ padding:'7px 12px', color:C.muted, whiteSpace:'nowrap' }}>{t.task_id ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#81C784', fontWeight:600 }}>{t.soil_moisture?.toFixed(1) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#FF8A65', fontWeight:600 }}>{t.soil_temperature?.toFixed(1) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#26A69A', fontWeight:600 }}>{t.soil_ec?.toFixed(0) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#AB47BC', fontWeight:600 }}>{t.ph_value?.toFixed(1) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#42A5F5', fontWeight:600 }}>{t.soil_nitrogen?.toFixed(0) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#FFA726', fontWeight:600 }}>{t.soil_phosphorus?.toFixed(0) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#66BB6A', fontWeight:600 }}>{t.soil_potassium?.toFixed(0) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:C.muted }}>{t.sample_count ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#FF7043', fontWeight:600 }}>{t.temperature?.toFixed(1) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#4FC3F7', fontWeight:600 }}>{t.humidity?.toFixed(1) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#CE93D8', fontWeight:600 }}>{t.co2_level?.toFixed(0) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:'#FFB74D', fontWeight:600 }}>{t.light_intensity?.toFixed(0) ?? '—'}</td>
                          <td style={{ padding:'7px 12px', color:C.tealDk, fontWeight:600 }}>{t.battery_level?.toFixed(0) ?? '—'}</td>
                        </tr>
                      ))}
                      {telemetry.length === 0 && (
                        <tr><td colSpan={15} style={{ textAlign:'center', padding:40, color:C.muted }}>
                          <div style={{ fontSize:28, marginBottom:8 }}>📊</div>暂无数据，请选择大棚并等待设备上报
                        </td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}
