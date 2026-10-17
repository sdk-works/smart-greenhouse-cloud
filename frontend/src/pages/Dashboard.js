import React, { useState, useEffect, useRef } from 'react';
import { Badge, Tag, Spin } from 'antd';
import {
  WifiOutlined, RobotOutlined, DatabaseOutlined,
  CameraOutlined, EnvironmentOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { dashboardApi, telemetryApi, createWebSocket } from '../services/api';
import { formatUTCtoLocal } from '../services/api';

// V8 农业清新色系
const C = {
  teal:    '#00D9A5',
  tealDk:  '#00A87A',
  yellow:  '#F5E663',
  sky:     '#4FC3F7',
  orange:  '#FFB74D',
  purple:  '#CE93D8',
  green2:  '#81C784',
  border:  'rgba(0,217,165,0.18)',
  text:    '#2d4a3e',
  muted:   '#8aab9e',
};

// 毛玻璃卡片
function GlassCard({ title, icon, extra, children, style = {} }) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.68)',
      backdropFilter: 'blur(14px)',
      WebkitBackdropFilter: 'blur(14px)',
      border: `1px solid ${C.border}`,
      borderRadius: 12,
      overflow: 'hidden',
      position: 'relative',
      boxShadow: '0 4px 20px rgba(0,100,70,0.07)',
      ...style,
    }}>
      <div style={{ position:'absolute', top:0, left:'15%', right:'15%', height:1, background:`linear-gradient(90deg,transparent,${C.teal}55,transparent)` }} />
      {(title || extra) && (
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'10px 14px', borderBottom:`1px solid rgba(0,217,165,0.08)` }}>
          <div style={{ fontSize:12, color:C.tealDk, fontWeight:700, display:'flex', alignItems:'center', gap:6 }}>
            {icon}<span>{title}</span>
          </div>
          {extra}
        </div>
      )}
      <div style={{ padding:14 }}>{children}</div>
    </div>
  );
}

// 统计卡片
function StatCard({ title, value, suffix, icon, color }) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.68)',
      backdropFilter: 'blur(14px)',
      border: `1px solid ${color}33`,
      borderRadius: 12, padding: '12px 14px',
      position: 'relative', overflow: 'hidden',
      boxShadow: '0 3px 14px rgba(0,100,70,0.05)',
    }}>
      <div style={{ position:'absolute', top:0, left:0, right:0, height:2, background:`linear-gradient(90deg,transparent,${color}88,transparent)` }} />
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          <div style={{ fontSize:11, color:C.muted, marginBottom:5 }}>{title}</div>
          <div style={{ fontSize:26, fontWeight:800, color, lineHeight:1.1 }}>
            {value}
            <span style={{ fontSize:12, fontWeight:400, marginLeft:3, color:C.muted }}>{suffix}</span>
          </div>
        </div>
        <div style={{ fontSize:20, color:color + 'aa' }}>{icon}</div>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [stats,    setStats]    = useState({});
  const [overview, setOverview] = useState([]);
  const [history,  setHistory]  = useState([]);
  const [liveEnv,  setLiveEnv]  = useState(null);
  const [loading,  setLoading]  = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    loadAll();
    const t = setInterval(loadStats, 30000);
    wsRef.current = createWebSocket(data => {
      if (data.data) {
        const normalized = {
          ...data.data,
          ph_value: data.data.ph_value ?? data.data.ph,
        };
        setLiveEnv(normalized);
        setHistory(prev => [
          ...prev,
          { ...normalized, time: formatUTCtoLocal(data.timestamp, 'time') },
        ].slice(-50));
      }
      if (data.type !== 'heartbeat') loadStats();
    });
    return () => { clearInterval(t); wsRef.current?.close(); };
  }, []);

  const loadAll = async () => {
    setLoading(true);
    try {
      await Promise.all([loadStats(), loadHistory()]);
    } catch (err) {
      console.log("Dashboard 加载异常", err);
    } finally {
      setLoading(false);
    }
  };
  const loadStats = async () => {
    try {
      const [s, o] = await Promise.all([
        dashboardApi.stats().catch(()=>({data:{}})),
        dashboardApi.overview().catch(()=>({data:[]}))
      ]);
      setStats(s.data || {});
      setOverview(o.data || []);
    } catch (err) {
      console.log("统计数据加载失败", err);
      // 兜底，保证页面不崩溃
      setStats({});
      setOverview([]);
    }
  };

  const loadHistory = async () => {
    try {
      const r = await telemetryApi.list({ hours: 6, limit: 60 }).catch(()=>({data:[]}));
      const data = [...r.data].reverse().map(t => ({
        time:          formatUTCtoLocal(t.timestamp, 'time'),
        temperature:   t.temperature || 0,
        humidity:      t.humidity || 0,
        co2_level:     t.co2_level || 0,
        light_intensity: t.light_intensity,
        soil_moisture: t.soil_moisture || 0,
        soil_temperature: t.soil_temperature,
        soil_ec: t.soil_ec,
        ph_value: t.ph_value,
        soil_nitrogen: t.soil_nitrogen,
        soil_phosphorus: t.soil_phosphorus,
        soil_potassium: t.soil_potassium,
      }));
      setHistory(data);
      if (data.length > 0) setLiveEnv(data[data.length - 1]);
    } catch (err) {
      console.log("历史数据加载失败", err);
    }
  };

  const onlineRate = stats?.total_devices
    ? Math.round((stats.online_devices / stats.total_devices) * 100)
    : 0;

  const trendOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: C.border,
      textStyle: { color: C.text, fontSize: 12 },
    },
    legend: {
      data: ['温度', '湿度', 'CO₂'],
      bottom: 0,
      textStyle: { color: C.muted, fontSize: 11 },
    },
    grid: { left: 44, right: 12, top: 16, bottom: 36 },
    xAxis: {
      type: 'category',
      data: history.map(t => t.time),
      axisLabel: { color: C.muted, fontSize: 10, rotate: 25 },
      axisLine:  { lineStyle: { color: 'rgba(0,0,0,0.06)' } },
      splitLine: { show: false },
    },
    yAxis: [
      { type: 'value', axisLabel: { color: C.muted, fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(0,0,0,0.04)' } } },
      { type: 'value', axisLabel: { color: C.muted, fontSize: 10 }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '温度', type: 'line', smooth: true, symbol: 'none',
        data: history.map(t => t.temperature?.toFixed(1)),
        itemStyle: { color: '#FF7043' }, lineStyle: { width: 2 },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: '#FF704333' }, { offset: 1, color: '#FF704300' }] } },
      },
      {
        name: '湿度', type: 'line', smooth: true, symbol: 'none',
        data: history.map(t => t.humidity?.toFixed(1)),
        itemStyle: { color: C.sky }, lineStyle: { width: 2 },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: `${C.sky}33` }, { offset: 1, color: `${C.sky}00` }] } },
      },
      {
        name: 'CO₂', type: 'line', smooth: true, symbol: 'none', yAxisIndex: 1,
        data: history.map(t => t.co2_level?.toFixed(0)),
        itemStyle: { color: C.purple }, lineStyle: { width: 2, type: 'dashed' },
      },
    ],
  };

  const radarOption = {
    backgroundColor: 'transparent',
    radar: {
      indicator: [
        { name: '设备在线率', max: 100 }, { name: '数据完整性', max: 100 },
        { name: '巡检完成率', max: 100 }, { name: '响应速度',   max: 100 },
        { name: '系统健康度', max: 100 },
      ],
      splitArea:  { areaStyle: { color: ['rgba(0,217,165,0.03)', 'rgba(0,217,165,0.06)'] } },
      axisLine:   { lineStyle: { color: 'rgba(0,217,165,0.2)' } },
      splitLine:  { lineStyle: { color: 'rgba(0,217,165,0.15)' } },
      name:       { textStyle: { color: C.muted, fontSize: 11 } },
    },
    series: [{
      type: 'radar',
      data: [{
        value: [onlineRate, 92, 85, 96, 88],
        itemStyle:   { color: C.teal },
        areaStyle:   { color: `${C.teal}20` },
        lineStyle:   { color: C.teal, width: 2 },
        symbolSize: 5,
      }],
    }],
  };

  const statCards = [
    { title: '大棚总数',   value: stats?.total_greenhouses    || 0, suffix: '个', icon: <EnvironmentOutlined />, color: C.teal   },
    { title: '机器人总数', value: stats?.total_devices        || 0, suffix: '台', icon: <RobotOutlined />,      color: C.purple },
    { title: '在线设备',   value: stats?.online_devices       || 0, suffix: '台', icon: <WifiOutlined />,       color: C.green2 },
    { title: '进行中任务', value: stats?.active_tasks         || 0, suffix: '个', icon: <ThunderboltOutlined />,color: C.orange },
    { title: '今日数据',   value: stats?.today_telemetry_count|| 0, suffix: '条', icon: <DatabaseOutlined />,   color: C.sky    },
    { title: '今日图片',   value: stats?.today_images_count   || 0, suffix: '张', icon: <CameraOutlined />,     color: C.yellow },
  ];

  const envItems = [
    { label: '温度',     val: liveEnv?.temperature,   unit: '°C',  color: '#FF7043', max: 50   },
    { label: '湿度',     val: liveEnv?.humidity,       unit: '%',   color: C.sky,     max: 100  },
    { label: '土壤湿度', val: liveEnv?.soil_moisture,  unit: '%',   color: C.green2,  max: 100  },
    { label: '土壤温度', val: liveEnv?.soil_temperature,unit: '°C', color: '#FF8A65', max: 50   },
    { label: '土壤EC',   val: liveEnv?.soil_ec,          unit: ' μS/cm', color:'#26A69A', max:3000 },
    { label: '土壤pH',   val: liveEnv?.ph_value,         unit: '', color:'#AB47BC', max:14 },
    { label: '氮',       val: liveEnv?.soil_nitrogen,    unit: ' mg/kg', color:'#42A5F5', max:500 },
    { label: '磷',       val: liveEnv?.soil_phosphorus,  unit: ' mg/kg', color:'#FFA726', max:500 },
    { label: '钾',       val: liveEnv?.soil_potassium,   unit: ' mg/kg', color:'#66BB6A', max:500 },
    { label: 'CO₂',     val: liveEnv?.co2_level,       unit: 'ppm', color: C.purple,  max: 1500 },
    { label: '光照强度', val: liveEnv?.light_intensity, unit: ' lux', color: C.yellow, max: 100000 },
  ];

  return (
    <div className="fade-in">
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:16 }}>
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <div style={{ width:3, height:18, background:C.teal, borderRadius:2 }} />
          <span style={{ color:C.text, fontSize:15, fontWeight:700 }}>运营中心</span>
        </div>
        <div style={{ fontSize:12, color:C.muted }}>
          设备在线率 <span style={{ color: onlineRate > 80 ? C.tealDk : '#FF7043', fontWeight:700 }}>{onlineRate}%</span>
        </div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'repeat(6,1fr)', gap:10, marginBottom:14 }}>
        {statCards.map(s => <StatCard key={s.title} {...s} />)}
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 260px', gap:12, marginBottom:12 }}>
        <GlassCard
          title="实时环境趋势（近6小时）"
          icon={<ThunderboltOutlined style={{ color:C.teal }} />}
          extra={<Badge status="processing" color={C.teal} text={<span style={{ color:C.tealDk, fontSize:11 }}>实时</span>} />}
        >
          <Spin spinning={loading && history.length === 0}>
            {history.length > 0 ? (
              <ReactECharts option={trendOption} style={{ height: 220 , minHeight:220}} />
            ) : (
              <div style={{ height:220, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', gap:10 }}>
                <div style={{ fontSize:36, opacity:0.3 }}>📡</div>
                <div style={{ fontSize:12, color:C.muted }}>等待设备上报数据...</div>
                <div style={{ fontSize:11, color:'#bbb' }}>启动模拟器：python3 simulator/robot_simulator.py</div>
              </div>
            )}
          </Spin>
        </GlassCard>

        <GlassCard title="当前环境" icon="🌡️">
          <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
            {envItems.map(item => (
              <div key={item.label} style={{ padding:'8px 10px', background:'rgba(0,217,165,0.03)', borderRadius:8, border:`1px solid rgba(0,217,165,0.08)` }}>
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:5 }}>
                  <span style={{ fontSize:11, color:C.muted }}>{item.label}</span>
                  <span style={{ fontSize:16, color:item.color, fontWeight:700 }}>
                    {item.val != null ? `${item.val.toFixed(1)}${item.unit}` : '—'}
                  </span>
                </div>
                <div style={{ height:3, background:'rgba(0,0,0,0.06)', borderRadius:2 }}>
                  <div style={{
                    height:'100%', borderRadius:2,
                    width: item.val != null ? `${Math.min((item.val / item.max) * 100, 100)}%` : '0%',
                    background: `linear-gradient(90deg,${item.color}66,${item.color})`,
                    transition: 'width 0.5s ease',
                    boxShadow: `0 0 4px ${item.color}66`,
                  }} />
                </div>
              </div>
            ))}
          </div>
        </GlassCard>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'220px 1fr', gap:12 }}>
        <GlassCard title="系统健康度" icon="⚡">
          <ReactECharts option={radarOption} style={{ height: 180 }} />
        </GlassCard>

        <GlassCard title="大棚状态一览" icon={<EnvironmentOutlined style={{ color:C.teal }} />}>
          {overview.length > 0 ? (
            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(190px,1fr))', gap:8 }}>
              {overview.map(gh => (
                <div key={gh.id} style={{
                  padding:'10px 12px',
                  background:'rgba(255,255,255,0.5)',
                  border:`1px solid rgba(0,0,0,0.05)`,
                  borderRadius:8, cursor:'default',
                  transition:'border-color 0.2s, box-shadow 0.2s',
                }}>
                  <div style={{ fontSize:12, color:C.text, fontWeight:700, marginBottom:5 }}>{gh.name}</div>
                  <div style={{ display:'flex', justifyContent:'space-between', fontSize:11, color:C.muted }}>
                    <span>🤖 {gh.online_count}/{gh.device_count} 台在线</span>
                    <span style={{ color: gh.temperature > 35 ? '#FF7043' : (gh.temperature != null ? C.tealDk : '#ccc'), fontWeight:600 }}>
                      {gh.temperature != null ? `${gh.temperature.toFixed(1)}°C` : '—'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ textAlign:'center', padding:30, color:C.muted, fontSize:12 }}>
              <div style={{ fontSize:28, marginBottom:8 }}>🏠</div>
              暂无大棚数据，请先创建大棚
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
