import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import axios from 'axios';

// 全局自动带上登录 Token（解决 401 未授权）
const token = localStorage.getItem('token');
if (token) {
  axios.defaults.headers.common['Authorization'] = 'Bearer ' + token;
}

// V8：农业清新风格全局样式
const style = document.createElement('style');
style.textContent = `
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: "PingFang SC","Source Han Sans CN","Microsoft YaHei",sans-serif;
    -webkit-font-smoothing: antialiased;
    background: #f0f7f0;
    color: #2d4a3e;
  }

  /* 滚动条 */
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: rgba(0,0,0,0.04); }
  ::-webkit-scrollbar-thumb { background: rgba(0,217,165,0.3); border-radius: 2px; }

  /* Ant Design 深色主题覆盖 → 清新农业风 */
  .ant-table { background: transparent !important; }
  .ant-table-thead > tr > th {
    background: rgba(0,217,165,0.08) !important;
    color: #00A87A !important;
    border-bottom: 1px solid rgba(0,217,165,0.15) !important;
    font-size: 12px; font-weight: 600;
  }
  .ant-table-tbody > tr > td {
    border-bottom: 1px solid rgba(0,0,0,0.04) !important;
    color: #3d5a4f !important;
    font-size: 13px;
  }
  .ant-table-tbody > tr:hover > td { background: rgba(0,217,165,0.04) !important; }
  .ant-table-row { transition: background 0.15s; }

  .ant-modal-content {
    background: rgba(255,255,255,0.95) !important;
    backdrop-filter: blur(20px);
    border: 1px solid rgba(0,217,165,0.2) !important;
    border-radius: 16px !important;
    box-shadow: 0 20px 60px rgba(0,100,70,0.12) !important;
  }
  .ant-modal-header { background: transparent !important; border-bottom: 1px solid rgba(0,217,165,0.1) !important; }
  .ant-modal-title { color: #00A87A !important; font-weight: 700; }
  .ant-modal-close { color: #888 !important; }

  .ant-form-item-label > label { color: #5a7a6d !important; font-size: 13px; }
  .ant-input, .ant-select-selector, .ant-input-number-input {
    background: rgba(255,255,255,0.8) !important;
    border-color: rgba(0,217,165,0.25) !important;
    color: #2d4a3e !important;
    border-radius: 8px !important;
  }
  .ant-input:focus, .ant-select-focused .ant-select-selector {
    border-color: #00D9A5 !important;
    box-shadow: 0 0 0 2px rgba(0,217,165,0.15) !important;
  }
  .ant-select-dropdown {
    background: rgba(255,255,255,0.97) !important;
    border: 1px solid rgba(0,217,165,0.2) !important;
    border-radius: 10px !important;
    backdrop-filter: blur(10px);
  }
  .ant-select-item { color: #3d5a4f !important; }
  .ant-select-item-option-selected { background: rgba(0,217,165,0.08) !important; color: #00A87A !important; }
  .ant-select-item-option:hover { background: rgba(0,217,165,0.05) !important; }

  .ant-btn-primary {
    background: linear-gradient(135deg, #00D9A5, #00A87A) !important;
    border: none !important;
    color: #fff !important;
    font-weight: 600;
    border-radius: 8px !important;
    box-shadow: 0 4px 12px rgba(0,168,122,0.3) !important;
  }
  .ant-btn-primary:hover {
    background: linear-gradient(135deg, #00E8B3, #00C090) !important;
    box-shadow: 0 6px 20px rgba(0,168,122,0.4) !important;
    transform: translateY(-1px);
  }
  .ant-btn-default {
    background: rgba(255,255,255,0.8) !important;
    border-color: rgba(0,217,165,0.3) !important;
    color: #00A87A !important;
    border-radius: 8px !important;
  }
  .ant-btn-dangerous {
    border-color: #ff7875 !important;
    color: #ff4d4f !important;
    background: rgba(255,255,255,0.8) !important;
    border-radius: 8px !important;
  }

  .ant-tabs-tab { color: #8aab9e !important; }
  .ant-tabs-tab-active .ant-tabs-tab-btn { color: #00A87A !important; font-weight: 600; }
  .ant-tabs-ink-bar { background: #00D9A5 !important; height: 3px !important; border-radius: 2px; }
  .ant-tabs-nav { border-bottom: 1px solid rgba(0,217,165,0.12) !important; }

  .ant-message-notice-content {
    background: rgba(255,255,255,0.95) !important;
    border: 1px solid rgba(0,217,165,0.2) !important;
    border-radius: 10px !important;
    color: #2d4a3e !important;
    backdrop-filter: blur(10px);
  }
  .ant-popover-inner {
    background: rgba(255,255,255,0.97) !important;
    border: 1px solid rgba(0,217,165,0.15) !important;
    border-radius: 12px !important;
  }
  .ant-drawer-content {
    background: rgba(245,252,248,0.98) !important;
  }
  .ant-drawer-header {
    background: transparent !important;
    border-bottom: 1px solid rgba(0,217,165,0.12) !important;
  }
  .ant-drawer-title { color: #00A87A !important; font-weight: 700; }
  .ant-descriptions-item-label { color: #8aab9e !important; font-size: 12px; }
  .ant-descriptions-item-content { color: #2d4a3e !important; font-size: 13px; }

  /* 动画 */
  @keyframes breathGreen { 0%,100%{opacity:0.5;} 50%{opacity:1;} }
  @keyframes float { 0%,100%{transform:translateY(0);} 50%{transform:translateY(-6px);} }
  @keyframes fadeInUp { from{opacity:0;transform:translateY(16px);} to{opacity:1;transform:translateY(0);} }
  @keyframes spin { to{transform:rotate(360deg);} }
  @keyframes pulse { 0%,100%{box-shadow:0 0 0 0 rgba(0,217,165,0.4);} 50%{box-shadow:0 0 0 8px rgba(0,217,165,0);} }
  .fade-in { animation: fadeInUp 0.35s ease both; }
  .float-anim { animation: float 3s ease-in-out infinite; }
`;
document.head.appendChild(style);

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);