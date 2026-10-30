import { useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Card, Descriptions, Modal, Popconfirm, Select, Space, Table, Tag, Typography } from 'antd';
import { EyeOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons';
import { fetchAllPages, monitoringApi, runsApi, tasksApi } from '../api';

interface RunDetail { run_id: string; seq: number; status: string; queue_position: number | null; planned_requests: number; completed_requests: number; progress: number | null; current_concurrency: number; target_rps: number | null; actual_rps: number | null; error_categories: Record<string, number>; last_error_at: string | null; remaining_seconds: number | null; request_stats: Record<string, number>; }
interface Status { task_id: string; task_status: string; task_name?: string; total_runs: number; target_rps: number | null; actual_rps: number | null; error_categories: Record<string, number>; last_error_at: string | null; run_stats: Record<string, number>; runs: RunDetail[]; }
interface TaskOption { id: string; name?: string | null; status: string; }
interface RequestRow { id: number; sample_index?: number | null; kind: string; status: string; started_at?: string | null; finished_at?: string | null; vendor_total_tokens?: number | null; local_total_tokens?: number | null; }
interface Metrics { summary?: { request_count?: number; success_rate?: number | null; error_rate?: number | null; latency_ms?: { p50?: number | null; p95?: number | null }; throughput_requests_per_second?: number | null; throughput_tokens_per_second?: number | null; throughput_tokens_per_second_by_source?: { vendor?: number | null; local?: number | null }; quality?: { score_mean?: number | null; accuracy?: number | null }; error_categories?: Record<string, number> }; }

export default function MonitoringPage() {
  const { message } = App.useApp();
  const [taskId, setTaskId] = useState('');
  const [tasks, setTasks] = useState<TaskOption[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState('');
  const [requestsOpen, setRequestsOpen] = useState(false);
  const [requestRunId, setRequestRunId] = useState('');
  const [requests, setRequests] = useState<RequestRow[]>([]);
  const [requestsLoading, setRequestsLoading] = useState(false);
  const [requestDetail, setRequestDetail] = useState<Record<string, unknown> | null>(null);
  const [requestTotal, setRequestTotal] = useState(0);
  const [requestPage, setRequestPage] = useState(1);
  const [requestPageSize, setRequestPageSize] = useState(20);
  const [runMetrics, setRunMetrics] = useState<Metrics | null>(null);
  const taskSearchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    fetchAllPages<TaskOption>(params => tasksApi.list(params)).then(setTasks).catch(() => setError('任务列表读取失败'));
    return () => { if (taskSearchTimer.current) clearTimeout(taskSearchTimer.current); };
  }, []);
  const searchTasks = (value: string) => {
    if (taskSearchTimer.current) clearTimeout(taskSearchTimer.current);
    if (!value.trim()) return;
    taskSearchTimer.current = setTimeout(() => {
      void tasksApi.list({ skip: 0, limit: 100, keyword: value.trim() })
        .then(response => setTasks(current => {
          const byId = new Map(current.map(item => [item.id, item]));
          (response.data.items || []).forEach((item: TaskOption) => byId.set(item.id, item));
          return [...byId.values()];
        }))
        .catch(() => setError('任务搜索失败'));
    }, 250);
  };
  useEffect(() => {
    if (!taskId.trim()) { setStatus(null); return; }
    const refresh = () => monitoringApi.taskStatus(taskId.trim()).then(r => { setStatus(r.data); setError(''); }).catch(() => setError('任务不存在或状态读取失败'));
    refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => window.clearInterval(timer);
  }, [taskId]);
  const cancelRun = async (runId: string) => {
    try { await runsApi.cancel(runId); message.success('Run 取消请求已提交'); }
    catch (e: any) { message.error(e.response?.data?.error?.message || 'Run 取消失败'); }
  };
  const retryRun = async (runId: string) => {
    try { await runsApi.retry(runId); message.success('Run 已重新排队'); }
    catch (e: any) { message.error(e.response?.data?.error?.message || 'Run 重试失败'); }
  };
  const openRequests = async (runId: string, nextPage = 1, nextPageSize = requestPageSize) => {
    setRequestsOpen(true); setRequestRunId(runId); setRequestsLoading(true); setRequestDetail(null);
    if (nextPage === 1) setRunMetrics(null);
    try {
      const [requestResponse, metricsResponse] = await Promise.all([
        runsApi.listRequests(runId, { skip: (nextPage - 1) * nextPageSize, limit: nextPageSize }),
        nextPage === 1 ? runsApi.metrics(runId) : Promise.resolve(null),
      ]);
      setRequests(requestResponse.data.items || []);
      setRequestTotal(requestResponse.data.total || 0);
      if (metricsResponse) setRunMetrics(metricsResponse.data);
    }
    catch (e: any) { message.error(e.response?.data?.error?.message || '请求列表读取失败'); setRequests([]); }
    finally { setRequestsLoading(false); }
  };
  const openRunRequests = (runId: string) => {
    setRequestPage(1);
    void openRequests(runId, 1, requestPageSize);
  };
  const openRequestDetail = async (requestId: number) => {
    try { setRequestDetail((await runsApi.getRequest(requestRunId, requestId)).data); }
    catch (e: any) { message.error(e.response?.data?.error?.message || '请求详情读取失败'); }
  };
  return <div>
    <Typography.Title level={3}>任务监控</Typography.Title>
    <Card><Space direction="vertical" style={{ width: '100%' }}>
      <Select showSearch allowClear optionFilterProp="label" style={{ maxWidth: 640, width: '100%' }} placeholder="选择任务，自动刷新状态" value={taskId || undefined} onSearch={searchTasks} onChange={value => setTaskId(value || '')} options={tasks.map(task => ({ value: task.id, label: `${task.name || task.id} (${task.status})` }))} />
    </Space></Card>
    {error && <Alert style={{ marginTop: 16 }} type="error" message={error} />}
    {status && <Card style={{ marginTop: 16 }} title={status.task_name || status.task_id}>
      <p>任务状态：<Tag>{status.task_status}</Tag>　Run 总数：{status.total_runs}　目标 RPS：{status.target_rps ?? '-'}　实际 RPS：{status.actual_rps ?? '-'}　最近错误：{status.last_error_at ? new Date(status.last_error_at).toLocaleString('zh-CN') : '-'}</p>
      <Table rowKey="run_id" size="small" pagination={false} dataSource={status.runs} columns={[
        { title: 'Run', dataIndex: 'seq', render: (value: number) => `#${value}` },
        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
        { title: '队列位置', dataIndex: 'queue_position', render: (value: number | null) => value ?? '-' },
        { title: '进度', render: (_: unknown, run: RunDetail) => `${run.completed_requests}/${run.planned_requests} (${Math.round((run.progress || 0) * 100)}%)` },
        { title: '当前并发', dataIndex: 'current_concurrency' },
        { title: '目标 / 实际 RPS', render: (_: unknown, run: RunDetail) => `${run.target_rps ?? '-'} / ${run.actual_rps ?? '-'}` },
        { title: '错误', render: (_: unknown, run: RunDetail) => Object.entries(run.error_categories || {}).map(([key, count]) => `${key}: ${count}`).join('，') || '-' },
        { title: '预计剩余', dataIndex: 'remaining_seconds', render: (value: number | null) => value == null ? '-' : `${value}s` },
        { title: '操作', render: (_: unknown, run: RunDetail) => <Space>
          {['queued', 'running', 'awaiting_code', 'cancelling'].includes(run.status) && <Popconfirm title="确认取消此 Run？" onConfirm={() => cancelRun(run.run_id)}><Button size="small" danger icon={<StopOutlined />}>取消</Button></Popconfirm>}
          {['failed', 'partially_completed'].includes(run.status) && <Popconfirm title="确认重试此 Run？" onConfirm={() => retryRun(run.run_id)}><Button size="small" icon={<ReloadOutlined />}>重试</Button></Popconfirm>}
          {['completed', 'partially_completed', 'failed', 'cancelled', 'awaiting_code'].includes(run.status) && <Button size="small" icon={<EyeOutlined />} onClick={() => openRunRequests(run.run_id)}>请求</Button>}
        </Space> },
      ]} />
    </Card>}
    <Modal title={`Run 请求 · ${requestRunId.slice(0, 8)}`} open={requestsOpen} onCancel={() => setRequestsOpen(false)} footer={null} width={900}>
      {runMetrics && <Descriptions size="small" bordered column={{ xs: 1, sm: 2, md: 4 }} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="成功率">{runMetrics.summary?.success_rate == null ? '-' : `${(runMetrics.summary.success_rate * 100).toFixed(2)}%`}</Descriptions.Item>
        <Descriptions.Item label="错误率">{runMetrics.summary?.error_rate == null ? '-' : `${(runMetrics.summary.error_rate * 100).toFixed(2)}%`}</Descriptions.Item>
        <Descriptions.Item label="Latency P50">{runMetrics.summary?.latency_ms?.p50 ?? '-'} ms</Descriptions.Item>
        <Descriptions.Item label="Latency P95">{runMetrics.summary?.latency_ms?.p95 ?? '-'} ms</Descriptions.Item>
        <Descriptions.Item label="实际 RPS">{runMetrics.summary?.throughput_requests_per_second ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="Token/s">{runMetrics.summary?.throughput_tokens_per_second ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="Vendor / Local Token/s">{runMetrics.summary?.throughput_tokens_per_second_by_source ? `${runMetrics.summary.throughput_tokens_per_second_by_source.vendor ?? '-'} / ${runMetrics.summary.throughput_tokens_per_second_by_source.local ?? '-'}` : '-'}</Descriptions.Item>
        <Descriptions.Item label="质量得分">{runMetrics.summary?.quality?.score_mean ?? runMetrics.summary?.quality?.accuracy ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="错误分类">{Object.entries(runMetrics.summary?.error_categories || {}).map(([key, count]) => `${key}: ${count}`).join('，') || '-'}</Descriptions.Item>
      </Descriptions>}
      <Table rowKey="id" size="small" loading={requestsLoading} pagination={{ current: requestPage, pageSize: requestPageSize, total: requestTotal, showSizeChanger: true, onChange: (nextPage, nextPageSize) => { setRequestPage(nextPage); setRequestPageSize(nextPageSize); void openRequests(requestRunId, nextPage, nextPageSize); } }} dataSource={requests} onRow={record => ({ onClick: () => void openRequestDetail(record.id), style: { cursor: 'pointer' } })} columns={[
        { title: 'Request', dataIndex: 'id' },
        { title: '类型', dataIndex: 'kind' },
        { title: '样本', dataIndex: 'sample_index', render: (value: number | null) => value ?? '-' },
        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
        { title: 'Vendor Token', dataIndex: 'vendor_total_tokens', render: (value: number | null) => value ?? '-' },
        { title: 'Local Token', dataIndex: 'local_total_tokens', render: (value: number | null) => value ?? '-' },
      ]} />
    </Modal>
    <Modal title="Request 详情" open={Boolean(requestDetail)} onCancel={() => setRequestDetail(null)} footer={null} width={900}>
      {requestDetail && <pre style={{ maxHeight: 560, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{JSON.stringify(requestDetail, null, 2)}</pre>}
    </Modal>
  </div>;
}
