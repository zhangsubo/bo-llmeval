import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Input,
  InputNumber,
  List,
  Row,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd';
import { DeleteOutlined, EyeOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons';
import { historyCleanupApi, settingsApi } from '../api';

const { Title } = Typography;

export default function SettingsPage() {
  const { message } = App.useApp();
  const [whitelist, setWhitelist] = useState<{ domains: string[]; cidrs: string[] }>({ domains: [], cidrs: [] });
  const [domainInput, setDomainInput] = useState('');
  const [cidrInput, setCidrInput] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [concurrency, setConcurrency] = useState(4);
  const [baseCurrency, setBaseCurrency] = useState('CNY');
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [cutoffAt, setCutoffAt] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() - 30);
    date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
    return date.toISOString().slice(0, 16);
  });
  const [cleanupStatuses, setCleanupStatuses] = useState(['completed', 'partially_completed', 'failed', 'cancelled']);
  const [cleanupPreview, setCleanupPreview] = useState<CleanupPreview | null>(null);
  const [cleanupBatch, setCleanupBatch] = useState<CleanupBatch | null>(null);
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [cleanupConfirmation, setCleanupConfirmation] = useState('');

  interface CleanupPreview {
    cutoff_at: string;
    statuses: string[];
    task_ids: string[];
    tasks: Array<{ task_id: string; status: string; counts?: Record<string, number>; report_files?: string[] }>;
    planned_counts: Record<string, number>;
    report_files: string[];
    estimated_bytes: number;
  }

  interface CleanupBatch {
    id: string;
    cutoff_at: string;
    statuses: string[];
    status: 'pending' | 'running' | 'completed' | 'partial' | 'failed';
    planned_counts?: Record<string, number> | null;
    deleted_counts?: Record<string, number> | null;
    failed_items?: Array<{ kind?: string; path?: string; error?: string }> | null;
    error?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
  }

  const statusOptions = [
    { value: 'completed', label: '已完成' },
    { value: 'partially_completed', label: '部分完成' },
    { value: 'failed', label: '失败' },
    { value: 'cancelled', label: '已取消' },
  ];

  const formatBytes = (value: number | undefined) => {
    if (!value) return '0 B';
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
    return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
  };

  const cleanupPayload = useMemo(() => ({
    cutoff_at: cutoffAt,
    statuses: cleanupStatuses,
  }), [cutoffAt, cleanupStatuses]);

  const normalizedCleanupPayload = useMemo(() => {
    if (!cleanupPayload.cutoff_at) return null;
    const parsed = new Date(cleanupPayload.cutoff_at);
    if (Number.isNaN(parsed.getTime())) return null;
    return {
      cutoff_at: parsed.toISOString(),
      statuses: cleanupPayload.statuses,
    };
  }, [cleanupPayload]);

  const loadSettings = useCallback(() => {
    setSettingsLoading(true);
    Promise.all([settingsApi.getWhitelist(), settingsApi.list()])
      .then(([whitelistResponse, settingsResponse]) => {
        setWhitelist(whitelistResponse.data);
        const rows = settingsResponse.data?.items || [];
        const values = new Map(rows.map((row: { key: string; value: unknown }) => [row.key, row.value]));
        const concurrencyValue = values.get('global_max_parallel_runs');
        const currencyValue = values.get('base_currency');
        const unwrap = (value: unknown) => value && typeof value === 'object' && 'value' in value
          ? (value as { value?: unknown }).value
          : value;
        const parsedConcurrency = Number(unwrap(concurrencyValue));
        if (Number.isFinite(parsedConcurrency) && parsedConcurrency > 0) setConcurrency(parsedConcurrency);
        const parsedCurrency = String(unwrap(currencyValue) || '').trim().toUpperCase();
        if (parsedCurrency) setBaseCurrency(parsedCurrency);
      })
      .catch((error: any) => message.error(error.response?.data?.error?.message || '系统设置读取失败'))
      .finally(() => setSettingsLoading(false));
  }, [message]);

  useEffect(() => { loadSettings(); }, [loadSettings]);

  const saveSystemSettings = async () => {
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 1000) {
      message.error('全局并发上限必须是 1 到 1000 的整数');
      return;
    }
    const currency = baseCurrency.trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(currency)) {
      message.error('基准币种必须是 3 位 ISO 货币代码');
      return;
    }
    setSettingsLoading(true);
    try {
      await Promise.all([
        settingsApi.update('global_max_parallel_runs', { value: concurrency }),
        settingsApi.update('base_currency', { value: currency }),
      ]);
      message.success('系统参数已保存');
      loadSettings();
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '系统参数保存失败');
    } finally {
      setSettingsLoading(false);
    }
  };

  const handleAddDomain = () => {
    if (!domainInput.trim()) return;
    const updated = { ...whitelist, domains: [...whitelist.domains, domainInput.trim()], confirmation };
      settingsApi.updateWhitelist(updated).then(() => {
        message.success('已添加');
        setDomainInput('');
        setConfirmation('');
        loadSettings();
    }).catch((error: any) => message.error(error.response?.data?.error?.message || '白名单更新失败'));
  };

  const handleAddCidr = () => {
    if (!cidrInput.trim()) return;
    const updated = { ...whitelist, cidrs: [...whitelist.cidrs, cidrInput.trim()], confirmation };
      settingsApi.updateWhitelist(updated).then(() => {
        message.success('已添加');
        setCidrInput('');
        setConfirmation('');
        loadSettings();
    }).catch((error: any) => message.error(error.response?.data?.error?.message || '白名单更新失败'));
  };

  const previewCleanup = async () => {
    if (!normalizedCleanupPayload || cleanupStatuses.length === 0) {
      message.error('请输入有效的截止时间，并选择至少一个终态');
      return;
    }
    setCleanupLoading(true);
    try {
      const response = await historyCleanupApi.preview(normalizedCleanupPayload);
      setCleanupPreview(response.data);
      setCleanupBatch(null);
      message.success(`预览完成，共 ${response.data.task_ids?.length || 0} 个任务`);
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '历史清理预览失败');
    } finally {
      setCleanupLoading(false);
    }
  };

  const createCleanupBatch = async () => {
    if (!cleanupPreview || !normalizedCleanupPayload) {
      message.error('请先生成清理预览');
      return;
    }
    setCleanupLoading(true);
    try {
      // Use the exact input represented by the preview. The controls are
      // disabled from silently widening a batch after it was reviewed.
      const response = await historyCleanupApi.createBatch({
        cutoff_at: cleanupPreview.cutoff_at,
        statuses: cleanupPreview.statuses,
      });
      setCleanupBatch(response.data);
      setCleanupConfirmation('');
      message.success('清理批次已固化，执行范围不会自动扩大');
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '清理批次创建失败');
    } finally {
      setCleanupLoading(false);
    }
  };

  const refreshCleanupBatch = async () => {
    if (!cleanupBatch) return;
    setCleanupLoading(true);
    try {
      setCleanupBatch((await historyCleanupApi.getBatch(cleanupBatch.id)).data);
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '清理批次读取失败');
    } finally {
      setCleanupLoading(false);
    }
  };

  const executeCleanupBatch = async (retry = false) => {
    if (!cleanupBatch) return;
    if (cleanupConfirmation !== 'CONFIRM') {
      message.error('执行清理必须输入 CONFIRM');
      return;
    }
    setCleanupLoading(true);
    try {
      const response = retry
        ? await historyCleanupApi.retry(cleanupBatch.id, cleanupConfirmation)
        : await historyCleanupApi.execute(cleanupBatch.id, cleanupConfirmation);
      setCleanupBatch(response.data);
      setCleanupConfirmation('');
      message.success(retry ? '清理补偿重试已提交' : '清理批次已提交执行');
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '清理批次执行失败');
    } finally {
      setCleanupLoading(false);
    }
  };

  return (
    <div>
      <Title level={3}>系统设置</Title>

      <Card title="运行参数" extra={<Tag color="blue">影响后续 Run</Tag>} loading={settingsLoading}>
        <Row gutter={24} align="middle">
          <Col xs={24} md={8}>
            <Typography.Text strong>全局并发 Run 上限</Typography.Text>
            <InputNumber min={1} max={1000} precision={0} value={concurrency} onChange={value => setConcurrency(value || 1)} style={{ width: '100%', marginTop: 8 }} />
          </Col>
          <Col xs={24} md={8}>
            <Typography.Text strong>基准币种</Typography.Text>
            <Input value={baseCurrency} maxLength={3} onChange={event => setBaseCurrency(event.target.value.toUpperCase())} style={{ marginTop: 8 }} />
          </Col>
          <Col xs={24} md={8}>
            <Button type="primary" icon={<SaveOutlined />} onClick={() => void saveSystemSettings()} loading={settingsLoading} style={{ marginTop: 24 }}>保存运行参数</Button>
          </Col>
        </Row>
      </Card>

      <Card title="变更确认" style={{ marginBottom: 16 }}>
        <Input.Password
          value={confirmation}
          onChange={e => setConfirmation(e.target.value)}
          placeholder="输入 CONFIRM"
          onPressEnter={handleAddDomain}
        />
      </Card>

      <Row gutter={16}>
        <Col span={12}>
          <Card title="域名白名单" extra={<Tag>{whitelist.domains.length} 条</Tag>}>
            <Space style={{ marginBottom: 16 }}>
              <Input placeholder="api.openai.com" value={domainInput} onChange={e => setDomainInput(e.target.value)} onPressEnter={handleAddDomain} />
              <Button type="primary" onClick={handleAddDomain}>添加</Button>
            </Space>
            <List
              size="small"
              dataSource={whitelist.domains}
              locale={{ emptyText: '未配置（允许所有）' }}
              renderItem={(item: string) => <List.Item><Tag>{item}</Tag></List.Item>}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="CIDR 白名单" extra={<Tag>{whitelist.cidrs.length} 条</Tag>}>
            <Space style={{ marginBottom: 16 }}>
              <Input placeholder="10.0.0.0/8" value={cidrInput} onChange={e => setCidrInput(e.target.value)} onPressEnter={handleAddCidr} />
              <Button type="primary" onClick={handleAddCidr}>添加</Button>
            </Space>
            <List
              size="small"
              dataSource={whitelist.cidrs}
              locale={{ emptyText: '未配置（允许所有）' }}
              renderItem={(item: string) => <List.Item><Tag>{item}</Tag></List.Item>}
            />
          </Card>
        </Col>
      </Row>

      <Divider />

      <Card title="历史任务清理" extra={<Tag color="orange">不可逆操作</Tag>}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Alert type="warning" showIcon message="清理只允许选择终态任务；系统会先生成不可变批次清单，执行前请核对预览。" />
          <Row gutter={16} align="middle">
            <Col xs={24} md={8}>
              <Typography.Text strong>截止时间</Typography.Text>
              <Input type="datetime-local" value={cutoffAt} onChange={event => { setCutoffAt(event.target.value); setCleanupPreview(null); }} style={{ marginTop: 8 }} />
            </Col>
            <Col xs={24} md={12}>
              <Typography.Text strong>任务终态</Typography.Text>
              <Select mode="multiple" value={cleanupStatuses} onChange={value => { setCleanupStatuses(value); setCleanupPreview(null); }} options={statusOptions} style={{ width: '100%', marginTop: 8 }} />
            </Col>
            <Col xs={24} md={4}>
              <Button icon={<EyeOutlined />} onClick={() => void previewCleanup()} loading={cleanupLoading} style={{ marginTop: 24 }}>生成预览</Button>
            </Col>
          </Row>
          {cleanupPreview && <Descriptions size="small" bordered column={{ xs: 1, sm: 2, md: 4 }}>
            <Descriptions.Item label="可清理任务">{cleanupPreview.task_ids.length}</Descriptions.Item>
            <Descriptions.Item label="Run">{cleanupPreview.planned_counts.runs || 0}</Descriptions.Item>
            <Descriptions.Item label="Request">{cleanupPreview.planned_counts.requests || 0}</Descriptions.Item>
            <Descriptions.Item label="预计释放">{formatBytes(cleanupPreview.estimated_bytes)}</Descriptions.Item>
          </Descriptions>}
          {cleanupPreview && <Space wrap>
            <Button type="primary" icon={<DeleteOutlined />} onClick={() => void createCleanupBatch()} loading={cleanupLoading}>固化清理批次</Button>
            <Typography.Text type="secondary">报告文件 {cleanupPreview.report_files.length} 个</Typography.Text>
          </Space>}
          {cleanupPreview?.tasks.length ? <List size="small" bordered header="预览任务" dataSource={cleanupPreview.tasks.slice(0, 20)} renderItem={task => <List.Item><Typography.Text code>{task.task_id}</Typography.Text><Tag>{task.status}</Tag><Typography.Text type="secondary">{Object.entries(task.counts || {}).filter(([, value]) => value).map(([key, value]) => `${key}: ${value}`).join('，') || '无关联数据'}</Typography.Text></List.Item>} /> : null}
          {cleanupBatch && <div style={{ border: '1px solid #f0f0f0', padding: 16 }}>
            <Space style={{ marginBottom: 12 }}>
              <Typography.Text strong>清理批次</Typography.Text>
              <Tag color={cleanupBatch.status === 'completed' ? 'green' : cleanupBatch.status === 'failed' ? 'red' : 'orange'}>{cleanupBatch.status}</Tag>
              <Button size="small" icon={<ReloadOutlined />} onClick={() => void refreshCleanupBatch()} loading={cleanupLoading}>刷新</Button>
            </Space>
            <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }}>
              <Descriptions.Item label="批次 ID"><Typography.Text copyable={{ text: cleanupBatch.id }}>{cleanupBatch.id.slice(0, 8)}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="任务">{Number(cleanupBatch.deleted_counts?.tasks || 0)}</Descriptions.Item>
              <Descriptions.Item label="Run">{Number(cleanupBatch.deleted_counts?.runs || 0)}</Descriptions.Item>
              <Descriptions.Item label="Request">{Number(cleanupBatch.deleted_counts?.requests || 0)}</Descriptions.Item>
              <Descriptions.Item label="文件">{Number(cleanupBatch.deleted_counts?.files || 0)}</Descriptions.Item>
              <Descriptions.Item label="释放空间">{formatBytes(Number(cleanupBatch.deleted_counts?.file_bytes || 0))}</Descriptions.Item>
              <Descriptions.Item label="失败项">{cleanupBatch.failed_items?.length || 0}</Descriptions.Item>
            </Descriptions>
            <Space.Compact style={{ width: '100%', marginTop: 12 }}>
              <Input.Password value={cleanupConfirmation} onChange={event => setCleanupConfirmation(event.target.value)} placeholder="输入 CONFIRM 后执行" />
              <Button type="primary" danger icon={<DeleteOutlined />} disabled={!['pending', 'partial', 'failed'].includes(cleanupBatch.status)} loading={cleanupLoading} onClick={() => void executeCleanupBatch()}>执行清理</Button>
              <Button icon={<ReloadOutlined />} disabled={!['partial', 'failed'].includes(cleanupBatch.status)} loading={cleanupLoading} onClick={() => void executeCleanupBatch(true)}>补偿重试</Button>
            </Space.Compact>
            {cleanupBatch.error && <Alert style={{ marginTop: 12 }} type="error" message={cleanupBatch.error} />}
            {!!cleanupBatch.failed_items?.length && <List size="small" style={{ marginTop: 12 }} header="失败项" dataSource={cleanupBatch.failed_items} renderItem={item => <List.Item><Typography.Text code>{item.path || item.kind}</Typography.Text><Typography.Text type="danger">{item.error}</Typography.Text></List.Item>} />}
          </div>}
        </Space>
      </Card>

      <Card title="说明" style={{ marginTop: 16 }}>
        <ul>
          <li>域名白名单支持通配符（如 <code>*.anthropic.com</code>）</li>
          <li>未配置白名单时允许所有域名（SSRF 防护仍生效）</li>
          <li>永久禁止地址（loopback、cloud metadata 等）不可通过白名单放行</li>
        </ul>
      </Card>
    </div>
  );
}
