/**
 * LandingPage.js — V8 农业清新大屏首页
 * 风格：智慧温室控制中心，早晨阳光下，清新专业充满生机
 * 色彩：青绿#00D9A5 + 柠檬黄#F5E663 + 天空蓝#4FC3F7
 * 背景：农田/温室实景感，毛玻璃悬浮卡片
 */
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

// ── 设计常量 ──────────────────────────────────────────────────
const C = {
  teal:    '#00D9A5',
  yellow:  '#F5E663',
  sky:     '#4FC3F7',
  orange:  '#FFB74D',
  purple:  '#CE93D8',
  green2:  '#81C784',
  white:   'rgba(255,255,255,0.92)',
  glass:   'rgba(255,255,255,0.72)',
  glassBorder: 'rgba(255,255,255,0.6)',
};

// ── 全局动画样式 ──────────────────────────────────────────────
const globalStyles = `
@keyframes float { 0%{transform:translateY(0px)} 50%{transform:translateY(-6px)} 100%{transform:translateY(0px)} }
@keyframes pulse { 0%{transform:scale(1);opacity:1} 50%{transform:scale(1.15);opacity:0.8} 100%{transform:scale(1);opacity:1} }
@keyframes warningFlash { 0%{background:rgba(255,138,101,0.15)} 50%{background:rgba(255,69,0,0.25)} 100%{background:rgba(255,138,101,0.15)} }
@keyframes breathRed { 0%{box-shadow:0 0 8px rgba(255,69,0,0.4)} 50%{box-shadow:0 0 20px rgba(255,69,0,0.8)} 100%{box-shadow:0 0 8px rgba(255,69,0,0.4)} }
@keyframes flowLine { 0%{background-position:0 0} 100%{background-position:200% 0} }
`;

// ── 背景：温室场景 SVG + 渐变 ─────────────────────────────────
function GreenBackground() {
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 0,
      background: 'linear-gradient(160deg, #e8f7f0 0%, #d4f0e8 25%, #c8eaf5 55%, #dff0e8 80%, #e8f5e9 100%)',
      overflow: 'hidden',
    }}>
      <div style={{ position:'absolute', top:'-10%', left:'-5%', width:'60%', height:'60%', borderRadius:'50%', background:'radial-gradient(circle, rgba(0,217,165,0.12) 0%, transparent 70%)' }} />
      <div style={{ position:'absolute', top:'30%', right:'-10%', width:'50%', height:'50%', borderRadius:'50%', background:'radial-gradient(circle, rgba(79,195,247,0.15) 0%, transparent 70%)' }} />
      <div style={{ position:'absolute', bottom:'-5%', left:'20%', width:'40%', height:'40%', borderRadius:'50%', background:'radial-gradient(circle, rgba(129,199,132,0.18) 0%, transparent 70%)' }} />
      <svg style={{ position:'absolute', inset:0, width:'100%', height:'100%', opacity:0.06 }} viewBox="0 0 1440 900">
        <path d="M200,800 Q400,200 720,180 Q1040,200 1240,800" stroke="#00A87A" strokeWidth="2" fill="none" />
        <path d="M300,800 Q460,280 720,260 Q980,280 1140,800" stroke="#00A87A" strokeWidth="1.5" fill="none" />
        <path d="M400,800 Q520,340 720,320 Q920,340 1040,800" stroke="#00A87A" strokeWidth="1" fill="none" />
        {[700,720,740,760,780,800].map(y => (
          <line key={y} x1="0" y1={y} x2="1440" y2={y} stroke="#00A87A" strokeWidth="0.5" />
        ))}
        {[360,480,600,720,840,960,1080].map(x => (
          <line key={x} x1={x} y1="150" x2={x} y2="800" stroke="#00A87A" strokeWidth="0.5" strokeDasharray="4,8" />
        ))}
      </svg>
      <FloatingLeaves />
    </div>
  );
}

function FloatingLeaves() {
  const items = [
    { emoji:'🌿', x:'8%',  y:'15%', size:24, delay:0    },
    { emoji:'🌱', x:'92%', y:'20%', size:20, delay:0.5  },
    { emoji:'🍃', x:'5%',  y:'65%', size:18, delay:1    },
    { emoji:'🌿', x:'88%', y:'70%', size:22, delay:1.5  },
    { emoji:'🌻', x:'50%', y:'5%',  size:20, delay:0.8  },
    { emoji:'🌾', x:'20%', y:'85%', size:16, delay:0.3  },
    { emoji:'🌾', x:'75%', y:'88%', size:16, delay:1.2  },
  ];
  return (
    <>
      {items.map((it, i) => (
        <div key={i} style={{
          position:'absolute', left:it.x, top:it.y, fontSize:it.size,
          opacity:0.35, animation:`float ${3 + i * 0.3}s ease-in-out ${it.delay}s infinite`,
        }}>{it.emoji}</div>
      ))}
    </>
  );
}

// ── 毛玻璃卡片 ────────────────────────────────────────────────
function GlassPanel({ children, style = {}, glow }) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.65)',
      backdropFilter: 'blur(20px)',
      WebkitBackdropFilter: 'blur(20px)',
      border: '1px solid rgba(255,255,255,0.75)',
      borderRadius: 16,
      boxShadow: `0 8px 32px rgba(0,100,70,0.08), inset 0 1px 0 rgba(255,255,255,0.8)${glow ? `, 0 0 30px ${glow}22` : ''}`,
      position: 'relative', overflow: 'hidden', ...style,
    }}>
      <div style={{ position:'absolute', top:0, left:'10%', right:'10%', height:1, background:`linear-gradient(90deg,transparent,${C.teal}66,transparent)` }} />
      {children}
    </div>
  );
}

// ── 环形仪表盘 ─────────────────────────────────────────────────
function GreenGauge({ value, max, label, icon, color, unit }) {
  const pct = Math.min(value / max, 1);
  const r = 36, cx = 44, cy = 44;
  const circumference = 2 * Math.PI * r;
  const startOffset = circumference * 0.25;
  const arcLen = circumference * 0.75;
  const dashOffset = arcLen * (1 - pct);

  return (
    <div style={{ textAlign:'center', padding:'8px 4px' }}>
      <div style={{ position:'relative', width:88, height:88, margin:'0 auto' }}>
        <svg width="88" height="88" viewBox="0 0 88 88">
          <circle cx={cx} cy={cy} r={r}
            fill="none" stroke="rgba(0,0,0,0.06)"
            strokeWidth="7" strokeLinecap="round"
            strokeDasharray={`${arcLen} ${circumference}`}
            strokeDashoffset={-startOffset}
            transform={`rotate(135, ${cx}, ${cy})`}
          />
          <circle cx={cx} cy={cy} r={r}
            fill="none" stroke={color}
            strokeWidth="7" strokeLinecap="round"
            strokeDasharray={`${arcLen - dashOffset} ${circumference}`}
            strokeDashoffset={-startOffset}
            transform={`rotate(135, ${cx}, ${cy})`}
            style={{ filter:`drop-shadow(0 0 4px ${color}88)`, transition:'stroke-dasharray 0.6s ease' }}
          />
          <text x={cx} y={cy - 4} textAnchor="middle" fontSize="18">{icon}</text>
          <text x={cx} y={cy + 14} textAnchor="middle" fontSize="13" fontWeight="700" fill={color}>
            {typeof value === 'number' ? value.toFixed(1) : value}
          </text>
          <text x={cx} y={cy + 26} textAnchor="middle" fontSize="9" fill="#8aab9e">{unit}</text>
        </svg>
      </div>
      <div style={{ fontSize:11, color:'#6b8f80', fontWeight:600, marginTop:-2 }}>{label}</div>
    </div>
  );
}

// ── 任务进度条 ─────────────────────────────────────────────────
function PlantProgress({ name, progress, status, color }) {
  const icons = ['🌱','🌿','🌳'];
  const stage = progress < 33 ? 0 : progress < 66 ? 1 : 2;
  return (
    <div style={{ marginBottom:12 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:5 }}>
        <span style={{ fontSize:12, color:'#3d6a59', fontWeight:600 }}>{name}</span>
        <div style={{ display:'flex', alignItems:'center', gap:6 }}>
          <span style={{ fontSize:14 }}>{icons[stage]}</span>
          <span style={{ fontSize:11, color, fontWeight:600 }}>
            {status === '执行中' ? `${progress}%` : status}
          </span>
        </div>
      </div>
      <div style={{ height:6, background:'rgba(0,0,0,0.06)', borderRadius:3, overflow:'hidden' }}>
        <div style={{
          height:'100%', width:`${progress}%`, borderRadius:3,
          background:`linear-gradient(90deg, ${color}88, ${color})`,
          transition:'width 1.2s ease', boxShadow:`0 0 8px ${color}44`,
        }} />
      </div>
    </div>
  );
}

// ── 数字滚动 ──────────────────────────────────────────────────
function CountUp({ to, unit = '', decimals = 0 }) {
  const [val, setVal] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const dur = 1800;
    const tick = () => {
      const p = Math.min((Date.now() - start) / dur, 1);
      const ease = 1 - Math.pow(1 - p, 3);
      setVal(+(ease * to).toFixed(decimals));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, [to]);
  return <><span style={{ fontVariantNumeric:'tabular-nums' }}>{val.toFixed(decimals)}</span>{unit}</>;
}

// ── 机器人地图（强化流动光效 + 脉冲光环）──────────────────────
function GreenhouseMapView({ robotPos, path }) {
  const canvasRef = useRef(null);
  const W = 520, H = 300;
  const MAP_W = 100, MAP_H = 60;
  const toC = (x, y) => [(x / MAP_W) * W, (y / MAP_H) * H];

  const sensors = [[15,12],[40,12],[65,12],[90,12],[15,32],[40,32],[65,32],[90,32],[15,52],[40,52],[65,52],[90,52]];
  const waypoints = [[8,10],[25,10],[42,10],[60,10],[78,10],[92,10],[92,30],[78,30],[60,30],[42,30],[25,30],[8,30],[8,50],[25,50],[42,50],[60,50],[78,50],[92,50]];

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, W, H);

    const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
    bgGrad.addColorStop(0, 'rgba(232,247,236,0.6)');
    bgGrad.addColorStop(1, 'rgba(200,235,220,0.8)');
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = 'rgba(0,168,122,0.15)';
    ctx.lineWidth = 1;
    for (let x = 0; x <= MAP_W; x += 10) {
      const [cx] = toC(x, 0);
      ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, H); ctx.stroke();
    }
    for (let y = 0; y <= MAP_H; y += 10) {
      const [, cy] = toC(0, y);
      ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(W, cy); ctx.stroke();
    }

    if (path.length > 1) {
      for (let i = 1; i < path.length; i++) {
        const alpha = (i / path.length) * 0.8 + 0.2;
        ctx.strokeStyle = `rgba(0,217,165,${alpha})`;
        ctx.lineWidth = 3;
        ctx.shadowColor = 'rgba(0,217,165,0.7)';
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.moveTo(...toC(path[i-1][0], path[i-1][1]));
        ctx.lineTo(...toC(path[i][0], path[i][1]));
        ctx.stroke();
        ctx.shadowBlur = 0;
      }
    }

    ctx.setLineDash([4, 6]);
    ctx.strokeStyle = 'rgba(0,168,122,0.2)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    waypoints.forEach(([x, y], i) => {
      const [cx, cy] = toC(x, y);
      i === 0 ? ctx.moveTo(cx, cy) : ctx.lineTo(cx, cy);
    });
    ctx.stroke();
    ctx.setLineDash([]);

    sensors.forEach(([x, y]) => {
      const [cx, cy] = toC(x, y);
      ctx.beginPath();
      ctx.arc(cx, cy, 4, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(129,199,132,0.7)';
      ctx.fill();
      ctx.strokeStyle = '#66BB6A';
      ctx.lineWidth = 1;
      ctx.stroke();
    });

    waypoints.forEach(([x, y]) => {
      const [cx, cy] = toC(x, y);
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(Math.PI / 4);
      ctx.fillStyle = 'rgba(245,230,99,0.5)';
      ctx.strokeStyle = 'rgba(200,180,0,0.4)';
      ctx.lineWidth = 0.8;
      ctx.fillRect(-3, -3, 6, 6);
      ctx.strokeRect(-3, -3, 6, 6);
      ctx.restore();
    });

    const [rx, ry] = toC(robotPos[0], robotPos[1]);
    
    // 机器人脉冲光环（P0强化）
    const pulseGlow = ctx.createRadialGradient(rx, ry, 2, rx, ry, 30);
    pulseGlow.addColorStop(0, 'rgba(0,217,165,0.5)');
    pulseGlow.addColorStop(0.5, 'rgba(0,217,165,0.2)');
    pulseGlow.addColorStop(1, 'rgba(0,217,165,0)');
    ctx.beginPath(); ctx.arc(rx, ry, 30, 0, Math.PI * 2);
    ctx.fillStyle = pulseGlow; ctx.fill();

    const glow = ctx.createRadialGradient(rx, ry, 2, rx, ry, 22);
    glow.addColorStop(0, 'rgba(0,217,165,0.35)');
    glow.addColorStop(1, 'rgba(0,217,165,0)');
    ctx.beginPath(); ctx.arc(rx, ry, 22, 0, Math.PI * 2);
    ctx.fillStyle = glow; ctx.fill();
    
    ctx.beginPath(); ctx.arc(rx, ry, 10, 0, Math.PI * 2);
    ctx.fillStyle = '#00D9A5';
    ctx.shadowColor = '#00D9A5'; ctx.shadowBlur = 15;
    ctx.fill(); ctx.shadowBlur = 0;
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
    ctx.font = '12px serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText('🤖', rx, ry);

    const legendItems = [['#00D9A5','机器人轨迹'],['#81C784','传感器节点'],['#F5E663','巡检点位']];
    legendItems.forEach(([color, text], i) => {
      const lx = 10, ly = H - 14 - (2 - i) * 16;
      ctx.beginPath(); ctx.arc(lx + 4, ly, 4, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.fill();
      ctx.font = '10px "PingFang SC",sans-serif';
      ctx.fillStyle = '#6b8f80'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.fillText(text, lx + 12, ly);
    });

  }, [robotPos, path]);

  return (
    <canvas ref={canvasRef} width={W} height={H}
      style={{ width:'100%', height:'100%', borderRadius:10 }} />
  );
}

// ── 主页面 ─────────────────────────────────────────────────────
export default function LandingPage() {
  const navigate = useNavigate();
  const [time, setTime] = useState(new Date());
  const [robotPos, setRobotPos] = useState([8, 10]);
  const [path, setPath] = useState([[8, 10]]);
  const [env, setEnv] = useState({ temp:24.5, hum:72.3, co2:420, soil:45.2, light:18500 });

  const WAYPOINTS = [
    [8,10],[25,10],[42,10],[60,10],[78,10],[92,10],
    [92,30],[78,30],[60,30],[42,30],[25,30],[8,30],
    [8,50],[25,50],[42,50],[60,50],[78,50],[92,50],
  ];
  const wpIdx = useRef(0);

  useEffect(() => {
    const style = document.createElement('style');
    style.innerHTML = globalStyles;
    document.head.appendChild(style);
    return () => document.head.removeChild(style);
  }, []);

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const moveId = setInterval(() => {
      wpIdx.current = (wpIdx.current + 1) % WAYPOINTS.length;
      const target = WAYPOINTS[wpIdx.current];
      setRobotPos(target);
      setPath(prev => [...prev.slice(-40), target]);
    }, 700);
    const envId = setInterval(() => {
      setEnv(d => ({
        temp:  +(d.temp  + (Math.random() - 0.5) * 0.4).toFixed(1),
        hum:   +(Math.min(99, Math.max(30, d.hum   + (Math.random() - 0.5) * 1))).toFixed(1),
        co2:   +(Math.max(300, d.co2   + (Math.random() - 0.5) * 15)).toFixed(0),
        soil:  +(Math.min(95, Math.max(10, d.soil  + (Math.random() - 0.5) * 0.5))).toFixed(1),
        light: +(Math.max(0, d.light + (Math.random() - 0.5) * 300)).toFixed(0),
      }));
    }, 2500);
    return () => { clearInterval(moveId); clearInterval(envId); };
  }, []);

  const stats = [
    { label:'温室大棚', value:12, unit:'个', icon:'🏠', color:C.teal    },
    { label:'在线机器人', value:3, unit:'台', icon:'🤖', color:C.sky     },
    { label:'今日巡检', value:47, unit:'次', icon:'🔍', color:C.yellow   },
    { label:'图片采集', value:284,unit:'张', icon:'📷', color:C.orange  },
    { label:'异常告警', value:2,  unit:'条', icon:'⚠️', color:'#FF8A65' },
    { label:'健康植株', value:98, unit:'%',  icon:'🌱', color:C.green2  },
  ];

  return (
    <div style={{ width:'100vw', height:'100vh', position:'relative', overflow:'hidden', fontFamily:'"PingFang SC","Source Han Sans CN","Microsoft YaHei",sans-serif' }}>
      <GreenBackground />

      <div style={{ position:'relative', zIndex:1, padding:'0 20px', maxWidth:1440, margin:'0 auto', height:'100%' }}>
        <div style={{
          height:60, display:'flex', alignItems:'center', justifyContent:'space-between',
          borderBottom:'1px solid rgba(0,217,165,0.15)',
        }}>
          <div style={{ display:'flex', alignItems:'center', gap:12 }}>
            <div style={{
              width:38, height:38, borderRadius:12,
              background:`linear-gradient(135deg, ${C.teal}, #00A87A)`,
              display:'flex', alignItems:'center', justifyContent:'center',
              fontSize:20, boxShadow:`0 4px 12px rgba(0,168,122,0.3)`,
            }}>🌿</div>
            <div>
              <div style={{ fontSize:18, fontWeight:700, color:'#1a4a38', letterSpacing:1 }}>
                智慧温室巡检机器人平台
              </div>
              <div style={{ fontSize:10, color:'#7aaf96' }}>
                Smart Greenhouse Robot Platform · 农业大数据大屏 · 智慧农场可视化
              </div>
            </div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:20 }}>
            <div style={{ textAlign:'right' }}>
              <div style={{ fontSize:22, fontWeight:700, color:C.teal, letterSpacing:2, fontVariantNumeric:'tabular-nums' }}>
                {time.toLocaleTimeString('zh-CN', { hour12:false })}
              </div>
              <div style={{ fontSize:10, color:'#8aab9e' }}>
                {time.toLocaleDateString('zh-CN')} · UTC+8
              </div>
            </div>
            <button
              onClick={() => navigate('/dashboard')}
              style={{
                padding:'10px 24px',
                background:`linear-gradient(135deg, ${C.teal}, #00A87A)`,
                border:'none', borderRadius:24, cursor:'pointer',
                fontSize:14, fontWeight:700, color:'#fff',
                boxShadow:`0 6px 20px rgba(0,168,122,0.35)`,
                transition:'all 0.25s', letterSpacing:1,
              }}
              onMouseEnter={e => { e.currentTarget.style.transform='translateY(-2px)'; e.currentTarget.style.boxShadow=`0 10px 30px rgba(0,168,122,0.45)`; }}
              onMouseLeave={e => { e.currentTarget.style.transform='translateY(0)'; e.currentTarget.style.boxShadow=`0 6px 20px rgba(0,168,122,0.35)`; }}
            >
              进入管理平台 →
            </button>
          </div>
        </div>

        <div style={{ display:'grid', gridTemplateColumns:'240px 1fr 240px', gap:14, padding:'14px 0' }}>
          <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
            <GlassPanel glow={C.teal}>
              <div style={{ padding:'12px 14px' }}>
                <SectionTitle icon="🌡️" title="环境监测" />
                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:4 }}>
                  <GreenGauge value={env.temp} max={50}   label="温度"   icon="☀️" color="#FF7043" unit="°C" />
                  <GreenGauge value={env.hum}  max={100}  label="湿度"   icon="💧" color={C.sky}   unit="%" />
                  <GreenGauge value={env.co2}  max={1500} label="CO₂"    icon="🍃" color={C.teal}  unit="ppm" />
                  <GreenGauge value={env.soil} max={100}  label="土壤湿度" icon="🌱" color={C.green2} unit="%" />
                </div>
                <div style={{ marginTop:8, padding:'7px 10px', background:'rgba(245,230,99,0.15)', borderRadius:8, border:`1px solid rgba(245,230,99,0.4)`, display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                  <span style={{ fontSize:11, color:'#8a7a30', display:'flex', alignItems:'center', gap:4 }}>☀️ 光照强度</span>
                  <span style={{ fontSize:13, color:'#C9A800', fontWeight:700 }}>{env.light.toLocaleString()} lux</span>
                </div>
              </div>
            </GlassPanel>

            <GlassPanel>
              <div style={{ padding:'12px 14px' }}>
                <SectionTitle icon="🏠" title="大棚状态" />
                {[
                  { name:'1号棚·番茄', temp:24.5, hum:72, status:'normal' },
                  { name:'2号棚·黄瓜', temp:22.1, hum:68, status:'normal' },
                  { name:'3号棚·草莓', temp:36.2, hum:85, status:'warn'   },
                  { name:'4号棚·辣椒', temp:25.0, hum:70, status:'normal' },
                ].map(g => (
                  <div key={g.name} style={{
                    display:'flex', alignItems:'center', justifyContent:'space-between',
                    padding:'8px 10px', marginBottom:6,
                    background: g.status==='warn' 
                      ? 'rgba(255,69,0,0.15)' 
                      : 'rgba(0,217,165,0.05)',
                    borderRadius:10,
                    border: g.status==='warn'
                      ? '2px solid rgba(255,69,0,0.6)'
                      : '1px solid rgba(0,217,165,0.1)',
                    animation: g.status==='warn' ? 'warningFlash 1.5s infinite' : 'none',
                  }}>
                    <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                      <div style={{ 
                        width:8, height:8, borderRadius:'50%', 
                        background: g.status==='warn' ? '#FF4500' : C.teal, 
                        boxShadow: g.status==='warn' 
                          ? '0 0 12px #FF4500' 
                          : `0 0 6px ${C.teal}` 
                      }} />
                      <span style={{ fontSize:12, color:'#3d6a59', fontWeight:600 }}>{g.name}</span>
                    </div>
                    <div style={{ 
                      fontSize:12, 
                      fontWeight: g.status==='warn' ? 700 : 500,
                      color: g.status==='warn' ? '#FF0000' : '#8aab9e' 
                    }}>
                      {g.temp}°C · {g.hum}%
                    </div>
                  </div>
                ))}
              </div>
            </GlassPanel>
          </div>

          <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
            <GlassPanel style={{ flex:1 }} glow={C.teal}>
              <div style={{ padding:'12px 14px 8px' }}>
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10 }}>
                  <SectionTitle icon="🗺️" title="温室实景 · 机器人实时追踪" />
                  <div style={{ display:'flex', alignItems:'center', gap:6, fontSize:11, color:C.teal }}>
                    <div style={{ width:7, height:7, borderRadius:'50%', background:C.teal, animation:'breathGreen 2s infinite' }} />
                    实时追踪
                  </div>
                </div>
                <div style={{ width:'100%', aspectRatio:'5/2.9', borderRadius:10, overflow:'hidden', boxShadow:'0 4px 16px rgba(0,100,70,0.1)' }}>
                  <GreenhouseMapView robotPos={robotPos} path={path} />
                </div>

                <div style={{ display:'grid', gridTemplateColumns:'repeat(5,1fr)', gap:8, marginTop:10 }}>
                  {[
                    { label:'编号', value:'ROB-001', color:C.teal  },
                    { label:'状态', value:'巡检中',  color:C.green2 },
                    { label:'电量', value:'82%',     color:C.yellow },
                    { label:'速度', value:'0.6m/s',  color:C.sky   },
                    { label:'位置', value:`(${robotPos[0].toFixed(0)},${robotPos[1].toFixed(0)})`, color:C.orange },
                  ].map(s => (
                    <div key={s.label} style={{
                      textAlign:'center', padding:'7px 4px',
                      background:'rgba(255,255,255,0.5)', borderRadius:8,
                      border:'1px solid rgba(0,217,165,0.1)',
                    }}>
                      <div style={{ fontSize:10, color:'#8aab9e', marginBottom:2 }}>{s.label}</div>
                      <div style={{ fontSize:12, color:s.color, fontWeight:700 }}>{s.value}</div>
                    </div>
                  ))}
                </div>
              </div>
            </GlassPanel>
          </div>

          <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
            <GlassPanel glow={C.yellow}>
              <div style={{ padding:'12px 14px' }}>
                <SectionTitle icon="📋" title="任务进度" />
                <PlantProgress name="1号棚早间巡检" progress={72} status="执行中"  color={C.teal}   />
                <PlantProgress name="2号棚病虫检测" progress={100} status="✅ 完成" color={C.green2} />
                <PlantProgress name="3号棚数据采集" progress={0}   status="待执行"  color="#ccc"     />
                <div style={{ marginTop:8, display:'flex', justifyContent:'space-between', fontSize:11, color:'#8aab9e' }}>
                  <span>今日完成</span><span style={{ color:C.teal, fontWeight:700 }}>6 / 8 个任务</span>
                </div>
              </div>
            </GlassPanel>

            <GlassPanel>
              <div style={{ padding:'12px 14px' }}>
                <SectionTitle icon="🔍" title="AI 病害识别" />
                <AIDetectPanel />
                <div style={{ marginTop:8 }}>
                  {[
                    { label:'今日识别', value:'184张', color:C.teal },
                    { label:'发现异常', value:'3处',   color:'#FF8A65' },
                    { label:'识别准确率', value:'96.8%', color:C.green2 },
                  ].map(r => (
                    <div key={r.label} style={{ display:'flex', justifyContent:'space-between', fontSize:11, color:'#8aab9e', marginBottom:4 }}>
                      <span>{r.label}</span><span style={{ color:r.color, fontWeight:700 }}>{r.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </GlassPanel>

            <GlassPanel>
              <div style={{ padding:'12px 14px' }}>
                <SectionTitle icon="⚡" title="系统状态" />
                {[
                  { label:'MQTT', ok:true  },
                  { label:'数据库', ok:true },
                  { label:'Redis', ok:true },
                  { label:'AI推理', ok:false },
                ].map(s => (
                  <div key={s.label} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
                    <span style={{ fontSize:12, color:'#6b8f80' }}>{s.label}</span>
                    <span style={{
                      fontSize:10, padding:'3px 10px', borderRadius:20,
                      background: s.ok ? 'rgba(0,217,165,0.12)' : 'rgba(255,69,0,0.15)',
                      color: s.ok ? '#00A87A' : '#FF0000',
                      fontWeight: s.ok ? 500 : 700,
                      border: `1px solid ${s.ok ? 'rgba(0,217,165,0.25)' : 'rgba(255,69,0,0.6)'}`,
                      animation: !s.ok ? 'breathRed 1.5s infinite' : 'none',
                    }}>
                      {s.ok ? '● 正常' : '● 离线'}
                    </span>
                  </div>
                ))}
              </div>
            </GlassPanel>
          </div>
        </div>

        <div style={{ display:'grid', gridTemplateColumns:'repeat(6,1fr)', gap:10, paddingBottom:16 }}>
          {stats.map(s => (
            <GlassPanel key={s.label} style={{ padding:'12px 14px' }}>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <div style={{ fontSize:22, animation: s.label==='在线机器人' ? 'float 3s ease-in-out infinite' : 'none' }}>{s.icon}</div>
                <div>
                  <div style={{ fontSize:10, color:'#8aab9e' }}>{s.label}</div>
                  <div style={{ fontSize:22, fontWeight:800, color:s.color, lineHeight:1.2 }}>
                    <CountUp to={s.value} unit={s.unit} />
                  </div>
                </div>
              </div>
            </GlassPanel>
          ))}
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ icon, title }) {
  return (
    <div style={{ fontSize:12, color:'#3d6a59', fontWeight:700, marginBottom:10, display:'flex', alignItems:'center', gap:5 }}>
      <span>{icon}</span>{title}
    </div>
  );
}

function AIDetectPanel() {
  const [scan, setScan] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setScan(s => (s + 1) % 100), 18);
    return () => clearInterval(id);
  }, []);
  return (
    <div style={{ position:'relative', height:90, borderRadius:8, overflow:'hidden', background:'linear-gradient(135deg,rgba(232,247,236,0.8),rgba(220,240,230,0.9))' }}>
      <div style={{ position:'absolute', left:0, right:0, top:`${scan}%`, height:1.5, background:`linear-gradient(90deg,transparent,rgba(0,217,165,0.6),transparent)`, boxShadow:`0 0 6px rgba(0,217,165,0.6)` }} />
      {[
        { x:25, y:20, w:35, h:28, label:'叶斑病 94%', c:'#FF7043' },
        { x:62, y:15, w:24, h:18, label:'正常 99%',   c:'#00A87A' },
      ].map((b, i) => (
        <div key={i} style={{
          position:'absolute', left:`${b.x}%`, top:`${b.y}%`, width:`${b.w}%`, height:`${b.h}%`,
          border:`1.5px solid ${b.c}`, borderRadius:3,
          boxShadow:`0 0 8px ${b.c}44`,
        }}>
          <div style={{ position:'absolute', top:-16, left:0, background:b.c, color:'#fff', fontSize:9, padding:'1px 5px', borderRadius:3, whiteSpace:'nowrap' }}>{b.label}</div>
        </div>
      ))}
      <div style={{ position:'absolute', bottom:4, right:6, fontSize:9, color:'#8aab9e' }}>AI 实时分析</div>
    </div>
  );
}