import LoginPage from './pages/LoginPage';
import React, { useState, useEffect, useRef } from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation, Navigate } from 'react-router-dom';
import { Badge, Avatar, Space, Typography, Dropdown, ConfigProvider, Tooltip, message, Drawer } from 'antd';
import {
  DashboardOutlined, DatabaseOutlined, AppstoreOutlined,
  PictureOutlined, SettingOutlined, BellOutlined,
  UserOutlined, HomeOutlined, EnvironmentOutlined,
  DeploymentUnitOutlined, TeamOutlined, LogoutOutlined, MenuOutlined,
} from '@ant-design/icons';

import LandingPage      from './pages/LandingPage';
import Dashboard        from './pages/Dashboard';
import GreenhouseWorkbench from './pages/GreenhouseWorkbench';
import DeviceManagement from './pages/DeviceManagement';
import DataCenter       from './pages/DataCenter';
import ImageGallery     from './pages/ImageGallery';
import SystemSettings   from './pages/SystemSettings';
import DeploymentPage   from './pages/DeploymentPage';
import UserManage       from './pages/UserManage';
// 旧点位入口统一转到大棚工作台，旧组件源文件保留。
import InspectionBatchesPage from './pages/InspectionBatchesPage';
import { createWebSocket, authApi } from './services/api';

const { Text } = Typography;

const NAV = [
  { key:'/dashboard',   icon:<DashboardOutlined />,       label:'运营中心'  },
  { key:'/greenhouses', icon:<EnvironmentOutlined />,    label:'大棚工作台'  },
  { key:'/devices',     icon:<AppstoreOutlined />,       label:'机器人管理' },
  { key:'/deployments', icon:<DeploymentUnitOutlined />,label:'巡检任务'  },
  { key:'/inspection-batches', icon:<DeploymentUnitOutlined />,label:'巡检批次'  },
  { key:'/data',        icon:<DatabaseOutlined />,       label:'数据中心'  },
  { key:'/images',      icon:<PictureOutlined />,        label:'图像管理'  },
  { key:'/users',       icon:<TeamOutlined />,           label:'用户管理'  },
  { key:'/settings',    icon:<SettingOutlined />,        label:'系统配置'  },
];

const C = { teal:'#00D9A5', tealDk:'#00A87A', border:'rgba(0,217,165,0.18)', muted:'#8aab9e', text:'#1a4a38' };

// ── 侧边栏 ───────────────────────────────────────────────
function Sider({ collapsed, onCollapse }) {
  const location = useLocation();
  return (
    <div style={{ width:collapsed?58:196, minHeight:'100vh', background:'rgba(255,255,255,0.82)', backdropFilter:'blur(20px)', borderRight:'1px solid '+C.border, display:'flex', flexDirection:'column', transition:'width 0.2s', position:'fixed', left:0, top:0, bottom:0, zIndex:100, boxShadow:'0 4px 12px rgba(0,100,70,0.06)' }}>
      <div onClick={onCollapse} style={{ height:60, display:'flex', alignItems:'center', padding:collapsed?'0 15px':'0 18px', borderBottom:'1px solid '+C.border, cursor:'pointer', gap:12 }}>
        <div style={{ width:28, height:28, borderRadius:8, background:'linear-gradient(135deg,'+C.teal+','+C.tealDk+')', display:'flex', alignItems:'center', justifyContent:'center', fontSize:16, color:'#fff', flexShrink:0 }}>🌿</div>
        {!collapsed && <div style={{ fontSize:15, fontWeight:700, color:C.text }}>智慧温室平台</div>}
      </div>

      <div style={{ flex:1, padding:'8px 6px', overflowY:'auto' }}>
        {NAV.map(item => {
          const active = location.pathname.startsWith(item.key);
          return (
            <Link key={item.key} to={item.key} style={{ textDecoration:'none' }}>
              <div style={{ display:'flex', alignItems:'center', gap:12, padding:'10px 12px', borderRadius:10, marginBottom:4, background:active?'rgba(0,217,165,0.12)':'transparent', color:active?C.tealDk:'#555', fontSize:14, fontWeight:active?600:500 }}>
                <div style={{ fontSize:18, width:20, display:'flex', justifyContent:'center' }}>{item.icon}</div>
                {!collapsed && <span>{item.label}</span>}
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

// ── 顶部栏 ───────────────────────────────────────────────
function TopBar({ alerts, onClearAlerts, wsStatus, mobile, onMenu }) {
  const location = useLocation();
  const title = NAV.find(n => location.pathname.startsWith(n.key))?.label || '控制台';

  const handleLogout = () => {
    localStorage.removeItem('token');
    message.success('已退出登录');
    window.location.href = '/login';
  };

  const userMenu = {
    items: [
      { key:'logout', icon:<LogoutOutlined />, label:'退出登录', onClick:handleLogout }
    ]
  };

  return (
    <div style={{ height:60, background:'rgba(255,255,255,0.9)', backdropFilter:'blur(12px)', borderBottom:'1px solid '+C.border, display:'flex', alignItems:'center', justifyContent:'space-between', padding:'0 20px', boxShadow:'0 2px 8px rgba(0,0,0,0.04)' }}>
      <div style={{display:'flex',alignItems:'center',gap:10}}>
        {mobile && <MenuOutlined onClick={onMenu} style={{fontSize:21,color:C.text,cursor:'pointer'}} />}
        <div style={{ fontSize:16, fontWeight:600, color:C.text }}>{title}</div>
      </div>
      <div style={{ display:'flex', alignItems:'center', gap:16 }}>
        {!mobile && <div style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, color: wsStatus==='connected'?C.tealDk:(wsStatus==='unauthorized'||wsStatus==='forbidden'?'#c0392b':'#999') }}>
          <div style={{ width:6, height:6, borderRadius:'50%', background:wsStatus==='connected'?C.teal:(wsStatus==='unauthorized'||wsStatus==='forbidden'?'#c0392b':'#ccc') }} />
          {wsStatus==='connected' ? '实时通道已连接' : (wsStatus==='unauthorized' ? '实时通道未登录' : wsStatus==='forbidden' ? '实时通道无权限' : '实时通道连接中...')}
        </div>}

        {!mobile && <Link to="/landing" style={{ display:'flex', alignItems:'center', gap:4, color:C.tealDk, textDecoration:'none', fontSize:12 }}>
          <HomeOutlined /> 返回首页大屏
        </Link>}

        <div style={{ position:'relative' }}>
          <BellOutlined style={{ fontSize:19, color:'#666' }} />
        </div>

        <Dropdown menu={userMenu}>
          <div style={{ display:'flex', alignItems:'center', gap:8, cursor:'pointer' }}>
            <div style={{ width:32, height:32, borderRadius:'50%', background:'linear-gradient(135deg,'+C.teal+','+C.tealDk+')' }}>👤</div>
            <div>
              <div style={{ fontSize:13, fontWeight:600 }}>管理员</div>
              <div style={{ fontSize:11, color:'#888' }}>在线</div>
            </div>
          </div>
        </Dropdown>
      </div>
    </div>
  );
}

// ── 登录校验 ───────────────────────────────────────────────
function RequireAuth({ children }) {
  const token = localStorage.getItem('token');
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function VersionWatcher() {
  const [baseline, setBaseline] = useState(null);
  const [nextVersion, setNextVersion] = useState(null);
  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const response = await fetch(`/version.json?t=${Date.now()}`, { cache:'no-store' });
        if (!response.ok) return;
        const data = await response.json();
        if (!active || !data.version) return;
        if (!baseline) setBaseline(data.version);
        else if (data.version !== baseline) setNextVersion(data.version);
      } catch (_) {}
    };
    check(); const timer = setInterval(check, 60000);
    return () => { active = false; clearInterval(timer); };
  }, [baseline]);
  if (!nextVersion) return null;
  return <button onClick={() => window.location.reload()} style={{position:'fixed',zIndex:9999,left:'50%',top:12,transform:'translateX(-50%)',border:0,borderRadius:22,padding:'10px 18px',background:'#00A87A',color:'#fff',fontWeight:700,boxShadow:'0 6px 24px rgba(0,80,55,.28)',cursor:'pointer'}}>发现新版本，点击刷新</button>;
}

// ── 主布局 ───────────────────────────────────────────────
function MainLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobile, setMobile] = useState(() => window.innerWidth < 768);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [alerts, setAlerts] = useState([]);
  const [wsStatus, setWsStatus] = useState('connecting');
  const wsRef = useRef(null);

  useEffect(() => {
    wsRef.current = createWebSocket(msg => {
      if (msg.type === 'alert') setAlerts(prev => [...prev, msg].slice(-8));
    }, setWsStatus);
    return () => wsRef.current?.close();
  }, []);
  useEffect(() => {
    const update = () => setMobile(window.innerWidth < 768);
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  return (
    <RequireAuth>
      <div style={{ display:'flex', minHeight:'100vh' }}>
        {!mobile && <Sider collapsed={collapsed} onCollapse={() => setCollapsed(!collapsed)} />}
        <Drawer title="智慧温室平台" placement="left" width={280} open={mobileOpen} onClose={()=>setMobileOpen(false)} styles={{body:{padding:10}}}>
          {NAV.map(item => <Link key={item.key} to={item.key} onClick={()=>setMobileOpen(false)} style={{display:'flex',alignItems:'center',gap:12,padding:'14px 12px',fontSize:16,color:C.text,textDecoration:'none',borderBottom:'1px solid #f0f0f0'}}>{item.icon}<span>{item.label}</span></Link>)}
          <Link to="/landing" onClick={()=>setMobileOpen(false)} style={{display:'flex',alignItems:'center',gap:12,padding:'14px 12px',fontSize:16,color:C.tealDk,textDecoration:'none'}}><HomeOutlined />返回首页大屏</Link>
        </Drawer>
        <div style={{ 
          flex:1, 
          minWidth:0,
          marginLeft:mobile?0:(collapsed?58:196), 
          display:'flex', 
          flexDirection:'column',
          minHeight: '100vh'
        }}>
          <TopBar alerts={alerts} onClearAlerts={() => setAlerts([])} wsStatus={wsStatus} mobile={mobile} onMenu={()=>setMobileOpen(true)} />
          <div style={{ flex:1, padding:mobile?8:16, overflow:'auto', minWidth:0 }}>
            <Routes>
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/greenhouses" element={<GreenhouseWorkbench />} />
              <Route path="/greenhouses/:greenhouseId" element={<GreenhouseWorkbench />} />
              <Route path="/devices" element={<DeviceManagement />} />
              <Route path="/deployments" element={<DeploymentPage />} />
              <Route path="/inspection-batches" element={<InspectionBatchesPage />} />
              <Route path="/point-config" element={<Navigate to="/greenhouses" replace />} />
              <Route path="/data" element={<DataCenter />} />
              <Route path="/images" element={<ImageGallery />} />
              <Route path="/users" element={<UserManage />} />
              <Route path="/settings" element={<SystemSettings />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </div>
        </div>
      </div>
    </RequireAuth>
  );
}

// ── 入口 ───────────────────────────────────────────────
export default function App() {
  return (
    <ConfigProvider theme={{token:{colorPrimary:'#00D9A5',colorSuccess:'#00D9A5',borderRadius:8}}}>
      <VersionWatcher />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Navigate to="/login" replace />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/landing" element={<RequireAuth><LandingPage /></RequireAuth>} />
          <Route path="/*" element={<MainLayout />} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}
