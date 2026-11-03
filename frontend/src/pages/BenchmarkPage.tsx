import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Alert, Button, Card, Col, Descriptions, Form, Input, InputNumber, Row, Select, Space, Statistic, Switch, Table, Tag, Typography, message } from 'antd';
import type { AppConfig } from '../api/client';
import { triggerResultAnalysis } from '../api/client';
import { MarkdownViewer } from '../components/MarkdownViewer';

type Distribution = { count: number; missing_count: number; mean: number | null; p95: number | null; p99: number | null; p99_low_sample: boolean };
type Summary = { requests: number; successes: number; successful_rps: number; completed_rps: number; output_tokens_per_s: number | null; usage_samples: number; skipped_slots: number; errors: Record<string, number>; metrics: Record<string, Distribution>; chunk_interval_ms: Distribution; failed_e2e_ms: Distribution; schedule_lag_ms: Distribution };
type Stage = { concurrency: number; requests: number; rps?: number; duration_s?: number; ramp_up_s?: number };
type Run = { run_id: string; status: string; model: string; started_at: string; active: number; summary?: Summary; checks: { name: string; status: string; evidence: string }[]; config: Record<string, unknown>; stage_reports?: { stage: number; config: Stage; summary: Summary }[]; samples?: { at_s: number; active: number; completed: number }[]; warnings?: string[] };
type CompareResult = { condition_differences: Record<string, unknown>; metric_compatible: boolean; baseline_samples: number; candidate_samples: number; metrics: Record<string, Record<string, { baseline: number | null; candidate: number | null; delta: number | null; change_pct: number | null }>> };
const terminal = new Set(['completed', 'stopped', 'failed', 'interrupted']);
const labels: Record<string, string> = { ttft_ms: 'TTFT 正文', first_reasoning_ms: '首推理事件', first_tool_ms: '首工具事件', e2e_ms: 'E2E', max_silence_ms: '最长正文静默', tpot_estimated_ms: 'TPOT（估算）', chunk_interval_ms: '片段间隔（非 ITL）', failed_e2e_ms: '失败请求耗时', schedule_lag_ms: '发起调度延迟' };
const number = (v: number | null | undefined) => v == null ? '不可用' : v.toFixed(2);

export const BenchmarkPage: React.FC<{ config: AppConfig | null }> = ({ config }) => {
  const [form] = Form.useForm();
  const [history, setHistory] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string>();
  const [run, setRun] = useState<Run>();
  const [busy, setBusy] = useState(false);
  const [comparison, setComparison] = useState<CompareResult>();
  const [pair, setPair] = useState<string[]>([]);
  const [analysis, setAnalysis] = useState<{ runId: string; content: string }>();
  const [analyzing, setAnalyzing] = useState(false);
  const mode = Form.useWatch('load_mode', form);
  const profile = Form.useWatch('profile_id', form);
  const refresh = async () => {
    const { data } = await axios.get<{ items: Run[] }>('/api/benchmarks');
    setHistory(data.items);
    return data.items;
  };
  useEffect(() => {
    let disposed = false;
    axios.get<{ items: Run[] }>('/api/benchmarks').then(({ data }) => {
      if (!disposed) {
        setHistory(data.items);
        setSelected(data.items.find(r => !terminal.has(r.status))?.run_id ?? data.items[0]?.run_id);
      }
    }).catch(() => message.error('读取压测历史失败'));
    return () => { disposed = true; };
  }, []);
  useEffect(() => {
    if (!selected) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const { data } = await axios.get<Run>(`/api/benchmarks/${selected}`);
        if (disposed) return;
        setRun(data);
        if (!terminal.has(data.status)) timer = setTimeout(poll, 1000);
        else await refresh();
      } catch { if (!disposed) message.error('读取任务失败'); }
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [selected]);
  const start = async () => {
    try {
      const values = await form.validateFields();
      let stages: Stage[];
      if (values.load_mode === 'stages') {
        stages = JSON.parse(values.stages);
        if (!Array.isArray(stages) || !stages.length) throw new Error('阶段必须是非空 JSON 数组');
      } else {
        stages = [{ concurrency: values.concurrency, requests: values.requests, ramp_up_s: values.ramp_up_s || 0,
          ...(values.duration_s ? { duration_s: values.duration_s } : {}),
          ...(values.load_mode === 'rps' ? { rps: values.rps } : {}) }];
      }
      setBusy(true);
      const { data } = await axios.post('/api/benchmarks', {
        profile_id: values.profile_id, custom_base_url: values.custom_base_url, custom_api_key: values.custom_api_key,
        config: { model: values.model, api_type: values.api_type, template: values.template, prompt: values.prompt || null,
          stages, max_tokens: values.max_tokens, stream: values.stream, warmup: values.warmup,
          timeout_s: values.timeout_s, silence_timeout_s: values.silence_timeout_s,
          total_timeout_s: values.total_timeout_s, strict_errors: values.strict_errors,
          suite: values.suite, tokenizer_encoding: values.tokenizer_encoding || null },
      });
      form.setFieldValue('custom_api_key', '');
      setSelected(data.run_id);
      await refresh();
    } catch (e) {
      message.error(axios.isAxiosError(e) ? e.response?.data?.detail || '任务创建失败' : '请检查必填项和阶段 JSON');
    } finally { setBusy(false); }
  };
  const stop = async (immediate: boolean) => {
    try {
      await axios.post(`/api/benchmarks/${selected}/stop`, null, { params: { immediate } });
      message.success(immediate ? '已请求取消在途请求' : '已停止发新请求，等待在途请求结束');
    } catch { message.error('停止失败'); }
  };
  const compare = async () => {
    try {
      const { data } = await axios.get('/api/benchmarks/compare', { params: { baseline: pair[0], candidate: pair[1] } });
      setComparison(data);
    } catch { message.error('对比失败'); }
  };
  const analyze = async () => {
    if (!run) return;
    setAnalyzing(true);
    try {
      const result = await triggerResultAnalysis('benchmark', run.run_id);
      setAnalysis({ runId: run.run_id, content: result.analysis.content });
    } catch { message.error('AI 解读失败，请检查解读模型配置'); }
    finally { setAnalyzing(false); }
  };
  const metrics: Record<string, Distribution> = run?.summary ? { ...run.summary.metrics, chunk_interval_ms: run.summary.chunk_interval_ms,
    failed_e2e_ms: run.summary.failed_e2e_ms, schedule_lag_ms: run.summary.schedule_lag_ms } : {};
  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Typography.Title level={3}>LLM 压测与自检</Typography.Title>
    <Alert type="info" showIcon message="建立基线，不预设性能合格线" description="并发数自由设置，500 不是配置上限。实际发压能力受本机资源影响。SSE 片段间隔不等于 ITL；精确 token 时间不可观测时不编造数值。" />
    <Row gutter={[20, 20]}>
      <Col xs={24} xl={10}>
        <Card title="新建压测">
          <Form form={form} layout="vertical" initialValues={{ profile_id: 'custom', api_type: 'chat/completions', template: 'short',
            load_mode: 'concurrency', concurrency: 10, requests: 100, rps: 5, stream: true, warmup: 0, ramp_up_s: 0,
            max_tokens: 256, timeout_s: 120, silence_timeout_s: 60, total_timeout_s: 3600, strict_errors: false, suite: 'performance',
            stages: '[{"concurrency":10,"requests":100},{"concurrency":50,"requests":200}]' }}>
            <Form.Item name="profile_id" label="接口配置" rules={[{ required: true }]}><Select options={[
              ...(config?.profiles || []).map(p => ({ label: p.name, value: p.id! })), { label: '临时自定义', value: 'custom' },
            ]} /></Form.Item>
            {profile === 'custom' && <>
              <Form.Item name="custom_base_url" label="Base URL" rules={[{ required: true }]}><Input placeholder="https://…/v1" /></Form.Item>
              <Form.Item name="custom_api_key" label="API Key（仅本次运行）" rules={[{ required: true }]}><Input.Password autoComplete="off" /></Form.Item>
            </>}
            <Form.Item name="model" label="Model ID" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="suite" label="测试类型"><Select options={[{ value: 'performance', label: '性能基线' }, { value: 'selfcheck', label: '错误码自检 + 性能观测（会发送缺少凭证、无效参数、未知模型请求）' }]} /></Form.Item>
            <Form.Item name="api_type" label="协议"><Select options={['chat/completions', 'responses'].map(value => ({ value }))} /></Form.Item>
            <Form.Item name="template" label="请求模板"><Select options={[
              ['short', '短问答'], ['long_input', '长输入'], ['long_output', '长输出'], ['reasoning', '推理'], ['tools', '工具调用'],
            ].map(([value, label]) => ({ value, label }))} /></Form.Item>
            <Form.Item name="prompt" label="自定义提示词（可选，不保存原文）"><Input.TextArea rows={3} /></Form.Item>
            <Form.Item name="tokenizer_encoding" label="正文 tokenizer（可选，用于估算 TPOT）" extra="需自行确认与模型匹配；不选则 TPOT 不可用，ITL 始终不以片段间隔代替。"><Select allowClear options={['cl100k_base', 'o200k_base'].map(value => ({ value }))} /></Form.Item>
            <Form.Item name="load_mode" label="负载方式"><Select options={[
              { value: 'concurrency', label: '固定并发' }, { value: 'rps', label: '固定 RPS' }, { value: 'stages', label: '阶梯加压' },
            ]} /></Form.Item>
            {mode === 'stages' ? <Form.Item name="stages" label="阶段 JSON（依次执行并排空）"><Input.TextArea rows={4} /></Form.Item> : <>
              <Form.Item name="concurrency" label={mode === 'rps' ? '最大在途并发' : '目标并发'} rules={[{ required: true }, { type: 'integer', min: 1 }]}><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
              {mode === 'rps' && <Form.Item name="rps" label="目标请求数/秒" rules={[{ required: true }, { type: 'number', min: 0.001 }]}><InputNumber min={0.001} /></Form.Item>}
              <Form.Item name="requests" label="请求数 / RPS 计划槽位上限" rules={[{ required: true }, { type: 'integer', min: 1 }]}><InputNumber min={1} /></Form.Item>
              <Form.Item name="duration_s" label="发压时长上限（秒，可选）"><InputNumber min={0.01} /></Form.Item>
              {mode === 'concurrency' && <Form.Item name="ramp_up_s" label="渐增至目标并发（秒，0 为直接发起）"><InputNumber min={0} /></Form.Item>}
            </>}
            <Space wrap>
              <Form.Item name="max_tokens" label="最大输出 token"><InputNumber min={1} /></Form.Item>
              <Form.Item name="warmup" label="预热请求数"><InputNumber min={0} max={1000} /></Form.Item>
              <Form.Item name="timeout_s" label="单请求总超时（秒）"><InputNumber min={0.01} /></Form.Item>
              <Form.Item name="silence_timeout_s" label="正文/工具静默超时（秒）"><InputNumber min={0.01} /></Form.Item>
              <Form.Item name="total_timeout_s" label="任务总超时（秒）"><InputNumber min={1} /></Form.Item>
              <Form.Item name="stream" label="流式" valuePropName="checked"><Switch /></Form.Item>
              <Form.Item name="strict_errors" label="严格要求 Retry-After" valuePropName="checked"><Switch /></Form.Item>
            </Space>
            <Button type="primary" loading={busy} onClick={start}>开始压测</Button>
          </Form>
        </Card>
      </Col>
      <Col xs={24} xl={14}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Select aria-label="压测历史" placeholder="选择任务" style={{ width: '100%' }} value={selected} onChange={setSelected}
            options={history.map(r => ({ value: r.run_id, label: `${r.model} · ${r.run_id} · ${r.status}` }))} />
          {run && <>
            <Card title={<Space>运行结果<Tag>{run.status}</Tag></Space>} extra={!terminal.has(run.status) && <Space>
              <Button onClick={() => stop(false)}>停止并排空</Button><Button danger onClick={() => stop(true)}>立即取消</Button>
            </Space>}>
              <Row gutter={[12, 12]}>
                <Col span={8}><Statistic title="已完成 / 成功" value={`${run.summary?.requests || 0} / ${run.summary?.successes || 0}`} /></Col>
                <Col span={8}><Statistic title="实际在途" value={run.active || 0} /></Col>
                <Col span={8}><Statistic title="成功 req/s" value={number(run.summary?.successful_rps)} /></Col>
                <Col span={8}><Statistic title="输出 token/s" value={number(run.summary?.output_tokens_per_s)} /></Col>
                <Col span={8}><Statistic title="usage 样本" value={run.summary?.usage_samples || 0} /></Col>
                <Col span={8}><Statistic title="跳过计划槽位" value={run.summary?.skipped_slots || 0} /></Col>
              </Row>
              <Typography.Paragraph type="secondary">吞吐窗口包含排空；输出 token 按完成请求的上报 usage 聚合。</Typography.Paragraph>
              {(run.samples?.length || 0) > 1 && <svg role="img" aria-label="实际在途并发曲线" viewBox="0 0 600 100" style={{ width: '100%', height: 100 }}>
                <title>实际在途并发，随时间变化</title>
                <polyline fill="none" stroke="#1677ff" strokeWidth="2" points={run.samples!.map((s, i, all) => `${i / Math.max(1, all.length - 1) * 590 + 5},${95 - s.active / Math.max(1, ...all.map(x => x.active)) * 90}`).join(' ')} />
              </svg>}
              <Table size="small" pagination={false} rowKey="key" dataSource={Object.entries(metrics).map(([key, value]) => ({ key, ...value }))}
                columns={[{ title: '指标（ms）', dataIndex: 'key', render: k => labels[k] || k },
                  { title: '样本', dataIndex: 'count' }, { title: 'mean', dataIndex: 'mean', render: number },
                  { title: 'p95', dataIndex: 'p95', render: number }, { title: 'p99', dataIndex: 'p99', render: (v, r) => `${number(v)}${r.p99_low_sample ? ' *' : ''}` }]} />
              <Typography.Paragraph type="secondary">* p99 样本不足 100；成功样本与失败耗时分开统计。ITL 不可得；TPOT 无匹配 token 证据时不可用。</Typography.Paragraph>
              <Space wrap>{Object.entries(run.summary?.errors || {}).map(([k, v]) => <Tag color="red" key={k}>{k}: {v}</Tag>)}</Space>
              <Space wrap style={{ marginTop: 12 }}>{['json', 'jsonl', 'csv', 'markdown'].map(kind => <Button key={kind} href={`/api/benchmarks/${run.run_id}/export/${kind}`}>导出 {kind}</Button>)}</Space>
              <Button style={{ marginTop: 12 }} loading={analyzing} disabled={!terminal.has(run.status)} onClick={analyze}>AI 解读 / 本地规则解读</Button>
              {analysis?.runId === run.run_id && <MarkdownViewer content={analysis.content} />}
            </Card>
            <Card title="协议与可靠性观测">
              <Table size="small" rowKey="name" pagination={false} dataSource={run.checks} columns={[
                { title: '检查', dataIndex: 'name' }, { title: '结果', dataIndex: 'status' }, { title: '证据边界', dataIndex: 'evidence' },
              ]} />
            </Card>
            <Card title="阶段结果"><Table size="small" rowKey="stage" pagination={false} dataSource={run.stage_reports} columns={[
              { title: '阶段', dataIndex: 'stage', render: v => v + 1 }, { title: '并发目标', render: (_, r) => r.config.concurrency },
              { title: '完成 / 成功', render: (_, r) => `${r.summary.requests} / ${r.summary.successes}` },
              { title: '成功 req/s', render: (_, r) => number(r.summary.successful_rps) },
            ]} /></Card>
          </>}
          <Card title="历史对比">
            <Select mode="multiple" maxCount={2} placeholder="依次选基线、对比轮次" style={{ width: '100%' }} value={pair} onChange={setPair}
              options={history.filter(r => terminal.has(r.status)).map(r => ({ value: r.run_id, label: `${r.model} · ${r.run_id}` }))} />
            <Button style={{ marginTop: 12 }} disabled={pair.length !== 2} onClick={compare}>对比两轮</Button>
            {comparison && <>
              <Alert style={{ marginTop: 12 }} type={Object.keys(comparison.condition_differences).length ? 'warning' : 'info'}
                message={`有效样本 ${comparison.baseline_samples} / ${comparison.candidate_samples}；口径${comparison.metric_compatible ? '一致' : '不兼容'}`}
                description={`条件差异：${Object.keys(comparison.condition_differences).join('、') || '无'}`} />
              <Table size="small" pagination={false} rowKey="name" dataSource={Object.entries(comparison.metrics).flatMap(([name, v]) => ['mean', 'p95', 'p99'].map(stat => ({ name: `${labels[name]} ${stat}`, ...v[stat] })))}
                columns={[{ title: '指标', dataIndex: 'name' }, { title: '基线', dataIndex: 'baseline', render: number },
                  { title: '本轮', dataIndex: 'candidate', render: number }, { title: '差值 ms', dataIndex: 'delta', render: number },
                  { title: '变化 %', dataIndex: 'change_pct', render: number }]} />
              <Descriptions size="small" column={1} items={Object.entries(comparison.condition_differences).map(([k, v]) => ({ key: k, label: k, children: JSON.stringify(v) }))} />
            </>}
          </Card>
        </Space>
      </Col>
    </Row>
  </Space>;
};
