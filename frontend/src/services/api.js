/**
 * api.js — Smart Greenhouse Platform v6
 * 
 * 时间处理规范：
 *   - 所有 API 返回的 timestamp 均为 UTC ISO 8601 (含 Z 或 +00:00)
 *   - 前端显示时使用 formatUTCtoLocal() 转换为本地时间
 *   - 禁止 new Date() 直接解析不带时区的字符串
 */
import axios from 'axios';

const BASE_URL = process.env.REACT_APP_API_URL || '/api/v1';

const api = axios.create({
  baseURL: BASE_URL,
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
});

// 自动带上 Token
api.interceptors.request.use(config => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  r => r,
  err => {
    console.error('API Error:', err.response?.data?.detail || err.message);
    if (err.response?.status === 401 && !err.config?.url?.includes('/auth/login')) {
      localStorage.removeItem('token');
      if (window.location.pathname !== '/login') window.location.replace('/login');
    }
    return Promise.reject(err);
  }
);

// ── 时间工具函数 ────────────────────────────────────────────────
export function formatUTCtoLocal(utcStr, fmt = 'datetime') {
  if (!utcStr) return '-';
  try {
    const date = new Date(utcStr);
    if (isNaN(date.getTime())) return utcStr;
    const opts = {
      datetime: { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false },
      time:     { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false },
      date:     { year: 'numeric', month: '2-digit', day: '2-digit' },
      short:    { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false },
    };
    return date.toLocaleString('zh-CN', opts[fmt] || opts.datetime);
  } catch {
    return utcStr;
  }
}

export function toChartLabel(utcStr) {
  return formatUTCtoLocal(utcStr, 'short');
}

// ── API ────────────────────────────────────────────────────────
export const greenhouseApi = {
  list:   ()           => api.get('/greenhouses/'),
  get:    (id)         => api.get(`/greenhouses/${id}`),
  create: (data)       => api.post('/greenhouses/', data),
  update: (id, data)   => api.put(`/greenhouses/${id}`, data),
  delete: (id)         => api.delete(`/greenhouses/${id}`),
  stats:  (id)         => api.get(`/greenhouses/${id}/stats`),
};

export const deviceApi = {
  list:        (params) => api.get('/devices/', { params }),
  get:         (id)     => api.get(`/devices/${id}`),
  register:    (data)   => api.post('/devices/', data),
  update:      (id, d)  => api.put(`/devices/${id}`, d),
  delete:      (id)     => api.delete(`/devices/${id}`),
  sendCommand: (id, cmd)=> api.post(`/devices/${id}/command`, cmd),
  getLogs:     (id, p)  => api.get(`/devices/${id}/logs`, { params: p }),
  getStatus:   (id)     => api.get(`/devices/${id}/status`),
  getMqttTopics:(id)    => api.get(`/devices/${id}/mqtt-topics`),
};

export const telemetryApi = {
  list:   (params)   => api.get('/telemetry/', { params }),
  latest: (deviceId) => api.get(`/telemetry/latest/${deviceId}`),
};

export const taskApi = {
  list:     (params) => api.get('/tasks/', { params }),
  get:      (id)     => api.get(`/tasks/${id}`),
  create:   (data)   => api.post('/tasks/', data),
  update:   (id, d)  => api.put(`/tasks/${id}`, d),
  dispatch: (id)     => api.post(`/tasks/${id}/dispatch`),
  cancel:   (id)     => api.post(`/tasks/${id}/cancel`),
};

export const imageApi = {
  list:   (params) => api.get('/images/', { params }),
  upload: (deviceId, file, extra = {}) => {
    const form = new FormData();
    form.append('file', file);
    const params = {};
    if (extra.task_id) params.task_id = extra.task_id;
    if (extra.position_x !== undefined) params.position_x = extra.position_x;
    if (extra.position_y !== undefined) params.position_y = extra.position_y;
    return api.post(`/images/upload/${deviceId}`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      params, timeout: 60000,
    });
  },
  delete: (id) => api.delete(`/images/${id}`),
};

export const dashboardApi = {
  stats: () => api.get('/dashboard/stats'),
  overview: () => api.get('/dashboard/greenhouses/overview'),
  realtime: () => api.get('/dashboard/realtime'),
};

export const settingsApi = {
  getGreenhouseMap: (id) => api.get(`/settings/greenhouse/${id}/map`),
  updateGreenhouseMap: (id, data) => api.put(`/settings/greenhouse/${id}/map`, data),
  addWaypoint: (id, data) => api.post(`/settings/greenhouse/${id}/map/waypoints`, data),
  deleteWaypoint: (id, wpId) => api.delete(`/settings/greenhouse/${id}/map/waypoints/${wpId}`),
  getRobotConfig: (devId) => api.get(`/settings/robot/${devId}/config`),
  updateRobotConfig: (devId, data) => api.put(`/settings/robot/${devId}/config`, data),
  assignRobot: (devId, ghId) => api.post(`/settings/robot/${devId}/assign-greenhouse/${ghId}`),
  getMqttConfig: () => api.get('/settings/mqtt'),
  updateMqttConfig: (data) => api.put('/settings/mqtt', data),
  getSystemConfig: () => api.get('/settings/system'),
  updateSystemConfig: (data) => api.put('/settings/system', data),
  getOverview: () => api.get('/settings/overview'),
};

// ==============================================
// 🔥 修复：用户管理 + 退出登录
// ==============================================
export const userApi = {
  list: () => api.get('/auth/users'),
  getProfile: () => api.get('/auth/me'),
  changePassword: (data) => api.post('/auth/change-password', data),
};

export const authApi = {
  login: (data) => api.post('/auth/login', data),
  logout: () => api.post('/auth/logout'),
};

// WebSocket
export function createWebSocket(onMessage, onStatus = () => {}) {
  const host = window.location.host;
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl = `${protocol}://${host}/api/v1/ws/realtime`;
  let ws, reconnectTimer, pingTimer;
  let manuallyClosed = false;

  function connect() {
    ws = new WebSocket(wsUrl);
    ws.onopen = () => {
      onStatus('connected');
      console.log('[WS] 已连接');
      pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 30000);
    };
    ws.onmessage = e => {
      try {
        const data = JSON.parse(e.data);
        if (data !== 'pong') onMessage(data);
      } catch {}
    };
    ws.onclose = () => {
      onStatus('connecting');
      clearInterval(pingTimer);
      if (!manuallyClosed) reconnectTimer = setTimeout(connect, 3000);
    };
    ws.onerror = () => ws.close();
  }

  connect();
  return {
    close: () => {
      manuallyClosed = true;
      clearTimeout(reconnectTimer);
      clearInterval(pingTimer);
      ws?.close();
    },
  };
}

export default api;
