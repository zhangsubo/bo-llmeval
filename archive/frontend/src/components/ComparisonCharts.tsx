import { Typography } from 'antd';

export interface MetricSummary {
  latency_ms?: Percentiles;
  ttft_ms?: Percentiles;
  tpot_ms?: Percentiles;
  success_rate?: number | null;
  error_rate?: number | null;
  throughput_requests_per_second?: number | null;
  throughput_tokens_per_second?: number | null;
  throughput_tokens_per_second_by_source?: { vendor?: number | null; local?: number | null };
  tokens_by_source?: {
    vendor?: { prompt?: number | null; completion?: number | null; total?: number | null };
    local?: { prompt?: number | null; completion?: number | null; total?: number | null };
    difference_rate?: { prompt?: number | null; completion?: number | null; total?: number | null };
  };
  tokens?: { total?: number | null };
  cost?: { amount?: number | null; currency?: string } | null;
  quality?: { score_mean?: number | null; accuracy?: number | null };
  request_series?: RequestPoint[];
  error_categories?: Record<string, number>;
}

export interface Percentiles {
  mean?: number | null;
  p50?: number | null;
  p90?: number | null;
  p95?: number | null;
  p99?: number | null;
}

export interface RequestPoint {
  request_id: number;
  latency_ms?: number | null;
  status?: string;
  phase?: string | null;
}

interface BarDatum {
  label: string;
  value: number | null | undefined;
  color?: string;
}

interface LineSeries {
  label: string;
  values: Array<number | null | undefined>;
  color?: string;
}

const COLORS = ['#1677ff', '#13a8a8', '#fa8c16', '#722ed1', '#eb2f96', '#52c41a'];

const finite = (value: number | null | undefined): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const formatValue = (value: number | null | undefined, suffix = '') => {
  const normalized = finite(value);
  return normalized == null ? '-' : `${normalized.toFixed(3)}${suffix}`;
};

export function MetricBarChart({ title, data, suffix = '' }: { title: string; data: BarDatum[]; suffix?: string }) {
  const values = data.map(item => ({ ...item, value: finite(item.value) })).filter(item => item.value != null);
  if (values.length === 0) return <section className="comparison-chart"><Typography.Title level={5}>{title}</Typography.Title><Typography.Text type="secondary">暂无数据</Typography.Text></section>;
  const max = Math.max(...values.map(item => Math.max(item.value || 0, 0)), 1);
  return <section className="comparison-chart" aria-label={title}>
    <Typography.Title level={5}>{title}</Typography.Title>
    <div className="comparison-bar-list">
      {values.map((item, index) => {
        const width = Math.max(0, ((item.value || 0) / max) * 100);
        return <div className="comparison-bar-row" key={`${item.label}-${index}`}>
          <Typography.Text className="comparison-bar-label" ellipsis={{ tooltip: item.label }}>{item.label}</Typography.Text>
          <div className="comparison-bar-track"><div className="comparison-bar-fill" style={{ width: `${width}%`, background: item.color || COLORS[index % COLORS.length] }} /></div>
          <Typography.Text className="comparison-bar-value">{formatValue(item.value, suffix)}</Typography.Text>
        </div>;
      })}
    </div>
  </section>;
}

export function MetricLineChart({ title, series }: { title: string; series: LineSeries[] }) {
  const normalized = series
    .map((item, index) => ({ ...item, color: item.color || COLORS[index % COLORS.length], values: item.values.map(finite) }))
    .filter(item => item.values.some(value => value != null));
  if (normalized.length === 0) return <section className="comparison-chart"><Typography.Title level={5}>{title}</Typography.Title><Typography.Text type="secondary">暂无数据</Typography.Text></section>;

  const width = 720;
  const height = 280;
  const left = 46;
  const right = 18;
  const top = 18;
  const bottom = 34;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const maximum = Math.max(...normalized.flatMap(item => item.values.filter((value): value is number => value != null)), 1);
  const pointCount = Math.max(...normalized.map(item => item.values.length), 1);
  const xStep = chartWidth / Math.max(pointCount - 1, 1);
  const y = (value: number) => top + chartHeight - (value / maximum) * chartHeight;
  return <section className="comparison-chart" aria-label={title}>
    <Typography.Title level={5}>{title}</Typography.Title>
    <svg className="comparison-line-chart" role="img" aria-label={title} viewBox={`0 0 ${width} ${height}`}>
      {[0, 1, 2, 3, 4].map(index => {
        const ratio = index / 4;
        const yPos = top + chartHeight * ratio;
        return <g key={index}><line x1={left} x2={width - right} y1={yPos} y2={yPos} stroke="#e5e7eb" /><text x={left - 8} y={yPos + 4} textAnchor="end">{(maximum * (1 - ratio)).toFixed(1)}</text></g>;
      })}
      <line x1={left} x2={width - right} y1={top + chartHeight} y2={top + chartHeight} stroke="#9ca3af" />
      {normalized.map(item => {
        const points = item.values.flatMap((value, index) => value == null ? [] : [`${left + xStep * index},${y(value)}`]);
        return <polyline key={item.label} fill="none" stroke={item.color} strokeWidth="2.5" points={points.join(' ')} />;
      })}
    </svg>
    <div className="comparison-chart-legend">
      {normalized.map(item => <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>)}
    </div>
  </section>;
}
