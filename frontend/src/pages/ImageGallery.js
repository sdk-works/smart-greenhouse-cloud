import React, { useState, useEffect } from 'react';
import {
  Select, Button, Upload, message, Spin, Popconfirm,
  Tag, Progress, Alert, Image,
} from 'antd';
import {
  UploadOutlined, DeleteOutlined, ReloadOutlined,
  InboxOutlined, CameraOutlined, EnvironmentOutlined,
} from '@ant-design/icons';
import { imageApi, deviceApi, greenhouseApi } from '../services/api';
import { formatUTCtoLocal } from '../services/api';

const C = {
  teal:'#00D9A5', tealDk:'#00A87A', yellow:'#F5E663',
  border:'rgba(0,217,165,0.18)', muted:'#8aab9e', text:'#2d4a3e',
};
const { Dragger } = Upload;

function GlassPanel({ children, style = {} }) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.68)',
      backdropFilter: 'blur(14px)',
      border: `1px solid ${C.border}`,
      borderRadius: 12,
      position: 'relative',
      overflow: 'hidden',
      boxShadow: '0 4px 20px rgba(0,100,70,0.06)',
      ...style,
    }}>
      <div style={{ position:'absolute', top:0, left:'10%', right:'10%', height:1, background:`linear-gradient(90deg,transparent,${C.teal}55,transparent)` }} />
      {children}
    </div>
  );
}

export default function ImageGallery() {
  const [images,       setImages]       = useState([]);
  const [devices,      setDevices]      = useState([]);
  const [greenhouses,  setGreenhouses]  = useState([]);
  const [loading,      setLoading]      = useState(false);
  const [uploading,    setUploading]    = useState(false);
  const [uploadPct,    setUploadPct]    = useState(0);
  const [uploadErr,    setUploadErr]    = useState('');
  const [filterGh,     setFilterGh]     = useState(null);
  const [uploadDevice, setUploadDevice] = useState(null);
  const [networkType,  setNetworkType]  = useState('wifi');
  const [preview,      setPreview]      = useState(null);

  useEffect(() => { loadMeta(); }, []);
  useEffect(() => { loadImages(); }, [filterGh]);

  const loadMeta = async () => {
    const [gr, dr] = await Promise.all([
      greenhouseApi.list(),
      deviceApi.list({ device_type:'robot' }),
    ]);
    setGreenhouses(gr.data); setDevices(dr.data);
  };

  const loadImages = async () => {
    setLoading(true);
    try {
      const params = {};
      if (filterGh) params.greenhouse_id = filterGh;
      const r = await imageApi.list(params);
      setImages(r.data);
    } finally { setLoading(false); }
  };

  const handleUpload = async (file) => {
    if (!uploadDevice) { message.error('请先选择上传机器人'); return false; }
    const device = devices.find(d => d.id === uploadDevice);
    if (!device) return false;

    setUploading(true); setUploadPct(0); setUploadErr('');
    const timer = setInterval(() => setUploadPct(p => Math.min(p + 7, 90)), 120);
    try {
      await imageApi.upload(device.device_id, file, { network_type: networkType });
      clearInterval(timer); setUploadPct(100);
      message.success('✅ 图片上传成功');
      setTimeout(() => { setUploading(false); setUploadPct(0); }, 700);
      loadImages();
    } catch (e) {
      clearInterval(timer);
      const msg = e.response?.data?.detail || '上传失败，请检查文件格式和大小';
      setUploadErr(msg); message.error(msg);
      setUploading(false); setUploadPct(0);
    }
    return false;
  };

  const handleDelete = async (id) => {
    try { await imageApi.delete(id); message.success('已删除'); loadImages(); }
    catch { message.error('删除失败'); }
  };

  return (
    <div className="fade-in">
      {/* 顶部标题 */}
      <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:16 }}>
        <div style={{ width:3, height:18, background:C.teal, borderRadius:2 }} />
        <span style={{ color:C.text, fontSize:15, fontWeight:700 }}>图片管理</span>
        <Tag style={{ background:'rgba(0,217,165,0.08)', border:`1px solid rgba(0,217,165,0.2)`, color:C.tealDk, borderRadius:20 }}>
          {images.length} 张
        </Tag>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'250px 1fr', gap:14 }}>
        {/* 左侧：上传面板 */}
        <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
          {/* 上传区域 */}
          <GlassPanel>
            <div style={{ padding:14 }}>
              <div style={{ fontSize:12, color:C.tealDk, fontWeight:700, marginBottom:12, display:'flex', alignItems:'center', gap:6 }}>
                <UploadOutlined />上传图片
              </div>

              {uploadErr && (
                <Alert type="error" message={uploadErr} closable showIcon
                  onClose={() => setUploadErr('')} style={{ marginBottom:10, borderRadius:8 }} />
              )}

              {/* 选择机器人 */}
              <div style={{ marginBottom:10 }}>
                <div style={{ fontSize:11, color:C.muted, marginBottom:4 }}>
                  选择机器人 <span style={{ color:'#FF7043' }}>*</span>
                </div>
                <Select style={{ width:'100%' }} placeholder="选择上传机器人"
                  value={uploadDevice} onChange={setUploadDevice}>
                  {devices.map(d => (
                    <Select.Option key={d.id} value={d.id}>
                      <span style={{ color: d.is_online ? C.teal : '#ccc' }}>● </span>
                      {d.name}
                    </Select.Option>
                  ))}
                </Select>
              </div>

              {/* 网络类型 */}
              <div style={{ marginBottom:10 }}>
                <div style={{ fontSize:11, color:C.muted, marginBottom:4 }}>网络环境</div>
                <Select style={{ width:'100%' }} value={networkType} onChange={setNetworkType}>
                  <Select.Option value="wifi">📶 WiFi / 有线（立即上传）</Select.Option>
                  <Select.Option value="4g">📱 4G（标记，建议本地缓存）</Select.Option>
                </Select>
                {networkType === '4g' && (
                  <div style={{ fontSize:10, color:'#FF8A65', marginTop:4, lineHeight:1.5 }}>
                    ⚠️ 4G 环境下机器人端应本地缓存图片，WiFi 联网后批量上传
                  </div>
                )}
              </div>

              {/* 拖拽上传区 */}
              <Dragger
                beforeUpload={handleUpload}
                accept="image/jpeg,image/png,image/gif,image/webp"
                showUploadList={false}
                disabled={!uploadDevice || uploading}
                style={{ background: uploadDevice ? 'rgba(0,217,165,0.03)' : 'rgba(0,0,0,0.02)', borderRadius:8, borderColor: uploadDevice ? 'rgba(0,217,165,0.3)' : '#ddd' }}
              >
                <div style={{ padding:'14px 8px' }}>
                  <InboxOutlined style={{ fontSize:26, color: uploadDevice ? C.teal : '#ccc' }} />
                  <div style={{ fontSize:13, color: uploadDevice ? C.text : '#ccc', marginTop:6 }}>
                    {uploadDevice ? '点击或拖拽图片到此上传' : '请先选择机器人'}
                  </div>
                  <div style={{ fontSize:11, color:C.muted, marginTop:3 }}>
                    JPG / PNG / WebP · 最大20MB
                  </div>
                </div>
              </Dragger>

              {uploading && (
                <div style={{ marginTop:10 }}>
                  <div style={{ fontSize:11, color:C.muted, marginBottom:4 }}>上传中...</div>
                  <Progress percent={uploadPct} strokeColor={C.teal} trailColor='rgba(0,0,0,0.06)' size="small" />
                </div>
              )}
            </div>
          </GlassPanel>

          {/* 筛选面板 */}
          <GlassPanel>
            <div style={{ padding:14 }}>
              <div style={{ fontSize:12, color:C.tealDk, fontWeight:700, marginBottom:10 }}>🔍 筛选</div>
              <div style={{ fontSize:11, color:C.muted, marginBottom:4 }}>按大棚</div>
              <Select allowClear style={{ width:'100%' }} placeholder="全部大棚" onChange={setFilterGh}>
                {greenhouses.map(g => <Select.Option key={g.id} value={g.id}>{g.name}</Select.Option>)}
              </Select>
              <Button style={{ width:'100%', marginTop:8, borderColor:C.border, color:C.muted }}
                icon={<ReloadOutlined />} onClick={loadImages}>刷新列表</Button>
            </div>
          </GlassPanel>
        </div>

        {/* 右侧：图片网格 */}
        <GlassPanel>
          <div style={{ padding:14 }}>
            <Spin spinning={loading}>
              {images.length > 0 ? (
                <Image.PreviewGroup>
                  <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(150px,1fr))', gap:10 }}>
                    {images.map(img => (
                      <div key={img.id} style={{
                        background:'rgba(255,255,255,0.6)',
                        border:'1px solid rgba(0,0,0,0.06)',
                        borderRadius:10, overflow:'hidden',
                        transition:'border-color 0.2s, box-shadow 0.2s',
                        cursor:'pointer',
                      }}
                        onMouseEnter={e => { e.currentTarget.style.borderColor=C.border; e.currentTarget.style.boxShadow='0 4px 16px rgba(0,100,70,0.1)'; }}
                        onMouseLeave={e => { e.currentTarget.style.borderColor='rgba(0,0,0,0.06)'; e.currentTarget.style.boxShadow='none'; }}
                      >
                        {/* 图片预览区 */}
                        <div style={{ height:110, background:'rgba(0,0,0,0.03)', overflow:'hidden', position:'relative' }}>
                          <Image
                            src={img.storage_url}
                            alt={img.filename}
                            style={{ width:'100%', height:110, objectFit:'cover', display:'block' }}
                            preview={{ mask: <div style={{ fontSize:11 }}>预览</div> }}
                            fallback="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='110'%3E%3Crect fill='%23f5f5f5' width='100' height='110'/%3E%3Ctext y='60' x='50' text-anchor='middle' fill='%23ccc' font-size='12'%3E图片%3C/text%3E%3C/svg%3E"
                          />
                          {/* 位置标签 */}
                          {img.position_x != null && (
                            <div style={{ position:'absolute', bottom:3, left:4, background:'rgba(0,0,0,0.5)', color:'#fff', fontSize:9, padding:'1px 5px', borderRadius:3, display:'flex', alignItems:'center', gap:2 }}>
                              <EnvironmentOutlined style={{ fontSize:8 }} />
                              ({img.position_x?.toFixed(0)},{img.position_y?.toFixed(0)})
                            </div>
                          )}
                          {/* 4G 标签 */}
                          {(img.tags || []).some(t => t.includes('4g')) && (
                            <div style={{ position:'absolute', top:4, right:4, background:'rgba(255,138,101,0.85)', color:'#fff', fontSize:9, padding:'1px 5px', borderRadius:3 }}>4G</div>
                          )}
                        </div>

                        {/* 底部信息 */}
                        <div style={{ padding:'6px 8px', display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                          <span style={{ fontSize:10, color:C.muted }}>{formatUTCtoLocal(img.created_at,'short')}</span>
                          <Popconfirm title="确认删除？" onConfirm={() => handleDelete(img.id)} okText="删除" cancelText="取消">
                            <DeleteOutlined style={{ fontSize:12, color:'#FF7043', cursor:'pointer' }} />
                          </Popconfirm>
                        </div>
                        {img.file_size && (
                          <div style={{ padding:'0 8px 5px', fontSize:10, color:'#bbb' }}>
                            {(img.file_size / 1024).toFixed(0)} KB
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </Image.PreviewGroup>
              ) : (
                <div style={{ textAlign:'center', padding:'50px 0', color:C.muted }}>
                  <CameraOutlined style={{ fontSize:42, marginBottom:12, color:'#ccc' }} />
                  <div style={{ fontSize:13 }}>暂无图片</div>
                  <div style={{ fontSize:11, marginTop:4, color:'#bbb' }}>通过左侧面板上传，或等待机器人自动上报</div>
                </div>
              )}
            </Spin>
          </div>
        </GlassPanel>
      </div>
    </div>
  );
}
