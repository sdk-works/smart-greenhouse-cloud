import React, { useState } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleLogin = async () => {
    setLoading(true);
    try {
      const res = await axios.post('/api/v1/auth/login', {
        username,
        password
      });
      localStorage.setItem('token', res.data.token);
      axios.defaults.headers.common['Authorization'] = 'Bearer ' + res.data.token;
      navigate('/dashboard');
    } catch (err) {
      alert(err.response?.data?.detail || '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      width: '100vw',
      height: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(160deg,#e8f7f0 0%,#d4f0e8 100%)'
    }}>
      <div style={{
        width: 400,
        padding: '40px 32px',
        background: 'rgba(255,255,255,0.85)',
        borderRadius: 16,
        boxShadow: '0 10px 30px rgba(0,100,70,0.1)',
        backdropFilter: 'blur(10px)'
      }}>
        <h2 style={{ textAlign: 'center', color: '#00A87A', marginBottom: 24 }}>智慧温室平台登录</h2>

        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="用户名"
          style={{
            width: '100%',
            padding: 12,
            marginBottom: 12,
            border: '1px solid #ddd',
            borderRadius: 8,
            fontSize: 14
          }}
        />

        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="密码"
          style={{
            width: '100%',
            padding: 12,
            marginBottom: 20,
            border: '1px solid #ddd',
            borderRadius: 8,
            fontSize: 14
          }}
        />

        <button
          onClick={handleLogin}
          disabled={loading}
          style={{
            width: '100%',
            padding: 12,
            background: '#00D9A5',
            color: '#fff',
            border: 'none',
            borderRadius: 8,
            fontSize: 16,
            fontWeight: 600,
            cursor: 'pointer'
          }}
        >
          {loading ? '登录中...' : '登录'}
        </button>
      </div>
    </div>
  );
}