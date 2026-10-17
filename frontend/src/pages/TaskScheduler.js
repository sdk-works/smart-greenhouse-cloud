import React, { useState, useEffect } from 'react';
import {
  Table, Card, Button, Modal, Form, Input, Select, Tag, Space,
  Typography, message, Badge, Progress, Popconfirm, Row, Col, InputNumber,
} from 'antd';
import { PlusOutlined, PlayCircleOutlined, StopOutlined, ReloadOutlined } from '@ant-design/icons';
import { taskApi, deviceApi, greenhouseApi } from '../services/api';
import dayjs from 'dayjs';

const { Title, Text } = Typography;
const { Option } = Select;
const { TextArea } = Input;

const STATUS = {
  pending:   { color: 'default',    text: '等待中' },
  running:   { color: 'processing', text: '执行中' },
  completed: { color: 'success',    text: '已完成' },
  failed:    { color: 'error',      text: '失败'   },
  cancelled: { color: 'warning',    text: '已取消' },
};

const TASK_TYPE_LABELS = {
  inspection:     '🔍 巡检',
  watering:       '💧 灌溉',
  fertilizing:    '🌱 施肥',
  pest_detection: '🦗 病虫检测',
};

export default function TaskScheduler() {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [greenhouses, setGreenhouses] = useState([]);
  const [devices, setDevices] = useState([]);
  const [form] = Form.useForm();

  useEffect(() => { loadAll(); }, []);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [tr, gr, dr] = await Promise.all([
        taskApi.list(),
        greenhouseApi.list(),
        deviceApi.list({ device_type: 'robot' }),
      ]);
      setTasks(tr.data);
      setGreenhouses(gr.data);
      setDevices(dr.data);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async (values) => {
    try {
      const waypoints = (values.waypoints_text || '').split('\n')
        .map(line => { const [x, y] = line.trim().split(',').map(Number); return { x, y }; })
        .filter(p => !isNaN(p.x) && !isNaN(p.y));
      await taskApi.create({
        task_name: values.task_name,
        task_type: values.task_type || 'inspection',
        greenhouse_id: values.greenhouse_id,
        device_id: values.device_id,
        priority: values.priority || 5,
        waypoints,
        actions: [{ type: 'capture_image' }, { type: 'measure_env' }],
      });
      message.success('任务创建成功');
      setModalOpen(false);
      form.resetFields();
      loadAll();
    } catch (e) {
      message.error(e.response?.data?.detail || '创建失败');
    }
  };

  const handleDispatch = async (task) => {
    try {
      await taskApi.dispatch(task.id);
      message.success('任务已下发');
      loadAll();
    } catch (e) {
      message.error(e.response?.data?.detail || '下发失败');
    }
  };

  const columns = [
    { title: '任务名称', dataIndex: 'task_name', render: v => <Text strong>{v}</Text> },
    { title: '类型', dataIndex: 'task_type', render: v => <Tag>{TASK_TYPE_LABELS[v] || v}</Tag> },
    { title: '状态', dataIndex: 'status', render: v => { const s = STATUS[v] || {}; return <Badge status={s.color} text={s.text} />; } },
    { title: '优先级', dataIndex: 'priority', render: v => <Tag color={v >= 8 ? 'red' : v >= 5 ? 'orange' : 'green'}>P{v}</Tag> },
    { title: '进度', dataIndex: 'progress', render: v => <Progress percent={v} size="small" style={{ width: 80 }} /> },
    { title: '创建时间', dataIndex: 'created_at', render: v => dayjs(v).format('MM-DD HH:mm') },
    {
      title: '操作', key: 'actions',
      render: (_, r) => (
        <Space size={4}>
          {r.status === 'pending' && (
            <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => handleDispatch(r)}>下发</Button>
          )}
          {['pending', 'running'].includes(r.status) && (
            <Popconfirm title="确认取消？" onConfirm={async () => { await taskApi.cancel(r.id); loadAll(); }}>
              <Button size="small" danger icon={<StopOutlined />}>取消</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Row align="middle" justify="space-between" style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>📅 任务调度</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadAll}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>创建任务</Button>
        </Space>
      </Row>

      <Card bodyStyle={{ padding: 0 }} style={{ borderRadius: 10 }}>
        <Table dataSource={tasks} columns={columns} rowKey="id" loading={loading} pagination={{ pageSize: 10 }} />
      </Card>

      <Modal title="创建巡检任务" open={modalOpen} onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()} okText="创建" width={520}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="task_name" label="任务名称" rules={[{ required: true }]}>
            <Input placeholder="如: 1号棚早间巡检" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={14}>
              <Form.Item name="task_type" label="任务类型" initialValue="inspection">
                <Select>
                  {Object.entries(TASK_TYPE_LABELS).map(([v, l]) => <Option key={v} value={v}>{l}</Option>)}
                </Select>
              </Form.Item>
            </Col>
            <Col span={10}>
              <Form.Item name="priority" label="优先级(1-10)" initialValue={5}>
                <InputNumber min={1} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="greenhouse_id" label="目标大棚" rules={[{ required: true }]}>
            <Select placeholder="选择大棚">
              {greenhouses.map(g => <Option key={g.id} value={g.id}>{g.name}</Option>)}
            </Select>
          </Form.Item>
          <Form.Item name="device_id" label="指定机器人（可选）">
            <Select allowClear placeholder="自动分配">
              {devices.map(d => (
                <Option key={d.id} value={d.id}>
                  <Badge status={d.is_online ? 'success' : 'default'} /> {d.name}
                </Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="waypoints_text" label="路径点（每行: x,y）">
            <TextArea rows={4} placeholder={'10,10\n30,10\n50,10\n70,10'} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
