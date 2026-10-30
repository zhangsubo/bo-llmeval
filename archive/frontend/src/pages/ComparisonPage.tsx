import { useMemo, useState } from 'react';
import { Alert, Button, Descriptions, Input, List, Space, Table, Tag, Typography } from 'antd';
import { BarChartOutlined } from '@ant-design/icons';
import { comparisonsApi } from '../api';
import { MetricBarChart, MetricLineChart } from '../components/ComparisonCharts';
import type { MetricSummary } from '../components/ComparisonCharts';

interface Difference { dimension: string; details: string; }
interface Run { id: string; status: string; seq?: number; model_deployment_id?: string; }
interface Comparison { comparability: string; differences: Difference[]; metrics: Record<string, { summary?: MetricSummary }>; runs: Run[]; }

const percentileKeys = ['p50', 'p90', 'p95', 'p99'] as const;

export default function ComparisonPage() {
  const [runIds, setRunIds] = useState('');
  const [result, setResult] = useState<Comparison | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const compare = async () => {
    const ids = runIds.split(/[\s,]+/).map(value => value.trim()).filter(Boolean);
    if (ids.length < 2) { setError('至少输入两个 Run ID'); return; }
    setLoading(true); setError('');
    try { setResult((await comparisonsApi.compare(ids)).data); }
    catch (e: any) { setError(e.response?.data?.error?.message || '对比失败'); setResult(null); }
    finally { setLoading(false); }
  };

  const rows = useMemo(() => {
    if (!result) return [];
    return result.runs.map(run => ({ run, summary: result.metrics[run.id]?.summary || {} }));
  }, [result]);

  const percentileData = rows.flatMap(({ run, summary }) => percentileKeys.map(key => ({
    label: `Run #${run.seq ?? '-'} ${key.toUpperCase()}`,
    value: summary.latency_ms?.[key],
  })));
  const throughputRequests = rows.map(({ run, summary }) => ({ label: `Run #${run.seq ?? '-'}`, value: summary.throughput_requests_per_second }));
  const throughputTokens = rows.map(({ run, summary }) => ({ label: `Run #${run.seq ?? '-'}`, value: summary.throughput_tokens_per_second }));
  const errorRate = rows.map(({ run, summary }) => ({ label: `Run #${run.seq ?? '-'}`, value: summary.error_rate == null ? null : summary.error_rate * 100 }));
  const qualityScores = rows.map(({ run, summary }) => ({ label: `Run #${run.seq ?? '-'}`, value: summary.quality?.score_mean ?? summary.quality?.accuracy }));
  const errorCategories = useMemo(() => {
    const counts = new Map<string, number>();
    rows.forEach(({ summary }) => Object.entries(summary.error_categories || {}).forEach(([category, count]) => counts.set(category, (counts.get(category) || 0) + Number(count || 0))));
    return [...counts.entries()].map(([label, value]) => ({ label, value }));
  }, [rows]);
  const latencySeries = rows.map(({ run, summary }) => ({
    label: `Run #${run.seq ?? '-'}`,
    values: (summary.request_series || []).map(point => point.latency_ms),
  }));

  return <div>
    <Typography.Title level={3}><BarChartOutlined /> 结果对比</Typography.Title>
    <Space.Compact style={{ width: '100%' }}>
      <Input value={runIds} onChange={event => setRunIds(event.target.value)} placeholder="输入两个或以上 Run UUID，用逗号或空格分隔" onPressEnter={() => void compare()} />
      <Button type="primary" loading={loading} onClick={() => void compare()}>开始对比</Button>
    </Space.Compact>
    {error && <Alert style={{ marginTop: 16 }} type="error" message={error} />}
    {result && <div style={{ marginTop: 16 }}>
      <Descriptions column={1} size="small">
        <Descriptions.Item label="可比性"><Tag color={result.comparability === 'comparable' ? 'green' : 'orange'}>{result.comparability}</Tag></Descriptions.Item>
        <Descriptions.Item label="Run 数量">{result.runs.length}</Descriptions.Item>
      </Descriptions>
      <List size="small" header="参与 Run" dataSource={result.runs} renderItem={run => <List.Item><Typography.Text code>{run.id}</Typography.Text><Tag>{run.status}</Tag>{run.model_deployment_id && <Typography.Text type="secondary">{run.model_deployment_id}</Typography.Text>}</List.Item>} />
      {result.differences.length > 0 && <List size="small" header="差异维度" dataSource={result.differences} renderItem={difference => <List.Item><Typography.Text strong>{difference.dimension}</Typography.Text><Typography.Text>{difference.details}</Typography.Text></List.Item>} />}

      <MetricBarChart title="Latency 分位数（ms）" data={percentileData} suffix=" ms" />
      <MetricLineChart title="请求 Latency 时间序列（按请求顺序）" series={latencySeries} />
      <MetricBarChart title="请求吞吐（RPS）" data={throughputRequests} suffix=" req/s" />
      <MetricBarChart title="Token 吞吐（Token/s）" data={throughputTokens} suffix=" token/s" />
      <MetricBarChart title="错误率" data={errorRate} suffix="%" />
      <MetricBarChart title="质量得分" data={qualityScores} />
      <MetricBarChart title="错误分类" data={errorCategories} />

      <Table rowKey="id" size="small" pagination={false} style={{ marginTop: 16 }} dataSource={rows.map(({ run, summary }) => ({ ...run, ...summary }))} columns={[
        { title: 'Run', dataIndex: 'id', render: (value: string) => <Typography.Text code>{value.slice(0, 8)}</Typography.Text> },
        { title: '成功率', dataIndex: 'success_rate', render: (value: number | null) => value == null ? '-' : `${Math.round(value * 10000) / 100}%` },
        { title: 'Latency P50', render: (_: unknown, row: MetricSummary) => row.latency_ms?.p50 ?? '-' },
        { title: 'Latency P95', render: (_: unknown, row: MetricSummary) => row.latency_ms?.p95 ?? '-' },
        { title: '实际 RPS', dataIndex: 'throughput_requests_per_second', render: (value: number | null) => value ?? '-' },
        { title: 'Token/s', dataIndex: 'throughput_tokens_per_second', render: (value: number | null) => value ?? '-' },
        { title: '质量得分', render: (_: unknown, row: MetricSummary) => row.quality?.score_mean ?? row.quality?.accuracy ?? '-' },
        { title: '成本', render: (_: unknown, row: MetricSummary) => row.cost?.amount == null ? '-' : `${row.cost.amount} ${row.cost.currency || ''}` },
      ]} />
    </div>}
  </div>;
}
