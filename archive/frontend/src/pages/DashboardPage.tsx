import { useEffect, useState } from 'react';
import { App, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { adminApi, tasksApi } from '../api';

interface DashboardData {
  tasks: {
    queued: number; running: number; completed: number; failed: number;
    partially_completed: number; cancelling: number; cancelled: number; total: number;
  };
  recent_errors: string[];
  recent_reports: Array<{ id: string; task_id: string; version: number; created_at?: string }>;
}

interface HealthData {
  status: string;
  components: Array<{ name: string; status: string; detail?: string }>;
}

export default function DashboardPage() {
  const { message } = App.useApp();
  const [data, setData] = useState<DashboardData | null>(null);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [recentTasks, setRecentTasks] = useState<Array<Record<string, unknown>>>([]);

  useEffect(() => {
    adminApi.dashboard()
      .then(r => setData(r.data))
      .catch((error: any) => message.error(error.response?.data?.error?.message || '仪表盘读取失败'));
    tasksApi.list({ limit: 5 })
      .then(r => setRecentTasks(r.data.items || []))
      .catch((error: any) => message.error(error.response?.data?.error?.message || '最近任务读取失败'));
    adminApi.health()
      .then(r => setHealth(r.data))
      .catch((error: any) => message.error(error.response?.data?.error?.message || '健康状态读取失败'));
  }, [message]);

  const stats = data?.tasks || {
    queued: 0, running: 0, completed: 0, failed: 0,
    partially_completed: 0, cancelling: 0, cancelled: 0, total: 0,
  };

  return (
    <div>
      <h3>系统概览</h3>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic title="总任务数" value={stats.total} prefix={<ClockCircleOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="运行中" value={stats.running} styles={{ content: { color: '#1890ff' } }} prefix={<SyncOutlined spin={stats.running > 0} />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已完成" value={stats.completed} styles={{ content: { color: '#52c41a' } }} prefix={<CheckCircleOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="失败" value={stats.failed} styles={{ content: { color: '#ff4d4f' } }} prefix={<CloseCircleOutlined />} />
          </Card>
        </Col>
      </Row>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}><Card><Statistic title="部分完成" value={stats.partially_completed} /></Card></Col>
        <Col span={8}><Card><Statistic title="取消中" value={stats.cancelling} styles={{ content: { color: '#fa8c16' } }} /></Card></Col>
        <Col span={8}><Card><Statistic title="已取消" value={stats.cancelled} styles={{ content: { color: '#faad14' } }} /></Card></Col>
      </Row>
      <Card title="组件健康" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Tag color={health?.status === 'healthy' ? 'success' : health ? 'warning' : 'default'}>
            总体：{health?.status || '加载中'}
          </Tag>
          {(health?.components || []).map(component => (
            <Tag key={component.name} color={component.status === 'healthy' ? 'success' : component.status === 'degraded' ? 'warning' : 'error'}>
              {component.name}: {component.status}{component.detail ? ` (${component.detail})` : ''}
            </Tag>
          ))}
        </Space>
      </Card>

      <Card title="最近任务">
        <Table
          dataSource={recentTasks}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: '任务名称', dataIndex: 'name', render: (v: string) => v || '-' },
            { title: '运行模式', dataIndex: 'run_mode', render: (v: string) => <Tag>{v}</Tag> },
            { title: '状态', dataIndex: 'status', render: (v: string) => {
              const colorMap: Record<string, string> = { draft: 'default', queued: 'blue', running: 'processing', completed: 'success', partially_completed: 'warning', cancelling: 'warning', failed: 'error', cancelled: 'warning' };
              return <Tag color={colorMap[v] || 'default'}>{v}</Tag>;
            }},
            { title: '创建时间', dataIndex: 'created_at', render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-' },
          ]}
        />
      </Card>
      <Card title="最近异常" style={{ marginTop: 16 }}>
        {(data?.recent_errors || []).length === 0
          ? <Typography.Text type="secondary">暂无异常</Typography.Text>
          : (data?.recent_errors || []).map((error, i) => (
              <div key={i} style={{ padding: '4px 0' }}>
                <Typography.Text type="danger">{error}</Typography.Text>
              </div>
            ))}
      </Card>
      <Card title="最近报告" style={{ marginTop: 16 }}>
        {(data?.recent_reports || []).length === 0
          ? <Typography.Text type="secondary">暂无报告</Typography.Text>
          : (data?.recent_reports || []).map(report => (
              <div key={report.id} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                <Typography.Text>任务 {report.task_id} · v{report.version}</Typography.Text>
                <Typography.Text type="secondary">{report.created_at ? new Date(report.created_at).toLocaleString('zh-CN') : '-'}</Typography.Text>
              </div>
            ))}
      </Card>
    </div>
  );
}
