import { useCallback, useEffect, useRef, useState } from 'react';
import { App, Button, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import { PlusOutlined, ReloadOutlined, SendOutlined, StopOutlined } from '@ant-design/icons';
import { comparisonGroupsApi, datasetsApi, fetchAllPages, modelDeploymentsApi, reportsApi, suppliersApi, tasksApi, templatesApi, tokenizersApi } from '../api';

interface Task {
  id: string; name: string; run_mode: string; status: string; created_at: string; queued_at: string | null;
  config_snapshot?: Record<string, unknown> | null;
  precheck_result?: PrecheckResult | null;
}
interface Deployment { id: string; model_id: string; display_name?: string | null; status: string; }
interface ResourceItem { id: string; name: string; kind?: string; status?: string; is_builtin?: boolean; }
interface Supplier { id: string; name: string; short_name?: string; status: string; }
interface ComparisonGroup { id: string; name: string; }
interface VersionItem {
  id: string;
  version: number;
  dataset_id?: string;
  dataset_version_id?: string;
  tokenizer_id?: string;
  template_id?: string;
  status?: string;
  test_type?: string | null;
  scenario?: string | null;
  stream_mode?: string | null;
  request_count?: number | null;
  warmup_count?: number | null;
  generation_params?: Record<string, unknown> | null;
  evalscope_datasets?: string[] | null;
  load_profile?: Record<string, unknown> | null;
  retry_policy?: Record<string, unknown> | null;
  timeouts?: Record<string, unknown> | null;
  limits?: Record<string, unknown> | null;
  input_length_preset?: 'short' | 'medium' | 'long' | 'extra_long' | null;
}

interface PrecheckCheck {
  name?: string;
  status?: string;
  detail?: string;
  target?: string;
  current?: unknown;
  limit?: unknown;
}

interface PrecheckResult {
  status?: string;
  checks?: PrecheckCheck[];
}

const LENGTH_PRESETS = [
  { value: 'short', label: '短', inputTokens: 256, outputTokens: 128 },
  { value: 'medium', label: '中', inputTokens: 2048, outputTokens: 512 },
  { value: 'long', label: '长', inputTokens: 8192, outputTokens: 1024 },
  { value: 'extra_long', label: '超长', inputTokens: 32768, outputTokens: 2048 },
] as const;

export default function TasksPage() {
  const { message } = App.useApp();
  const [data, setData] = useState<Task[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [allDeployments, setAllDeployments] = useState<Deployment[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [comparisonGroups, setComparisonGroups] = useState<ComparisonGroup[]>([]);
  const [tokenizers, setTokenizers] = useState<ResourceItem[]>([]);
  const [datasets, setDatasets] = useState<ResourceItem[]>([]);
  const [datasetVersions, setDatasetVersions] = useState<VersionItem[]>([]);
  const [templates, setTemplates] = useState<ResourceItem[]>([]);
  const [templateVersions, setTemplateVersions] = useState<VersionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [statusFilter, setStatusFilter] = useState<string>();
  const [keyword, setKeyword] = useState('');
  const [supplierId, setSupplierId] = useState<string>();
  const [modelDeploymentId, setModelDeploymentId] = useState<string>();
  const [comparisonGroupId, setComparisonGroupId] = useState<string>();
  const [templateId, setTemplateId] = useState<string>();
  const [testType, setTestType] = useState<string>();
  const [createdFrom, setCreatedFrom] = useState('');
  const [createdTo, setCreatedTo] = useState('');
  const [resourceLoading, setResourceLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reportingTaskId, setReportingTaskId] = useState<string | null>(null);
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const reportKeys = useRef(new Map<string, string>());
  const resourceSearchTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const qualityMode = Form.useWatch('quality_mode', form);

  const load = useCallback(() => {
    setLoading(true);
    const toIso = (value: string) => value ? new Date(value).toISOString() : undefined;
    return tasksApi.list({
      skip: (page - 1) * pageSize,
      limit: pageSize,
      status: statusFilter,
      keyword: keyword.trim() || undefined,
      supplier_id: supplierId,
      model_deployment_id: modelDeploymentId,
      comparison_group_id: comparisonGroupId,
      template_id: templateId,
      test_type: testType,
      created_from: toIso(createdFrom),
      created_to: toIso(createdTo),
    })
      .then(r => { setData(r.data.items || []); setTotal(r.data.total || 0); })
      .catch((error: any) => message.error(error.response?.data?.error?.message || '任务列表读取失败'))
      .finally(() => setLoading(false));
  }, [comparisonGroupId, createdFrom, createdTo, keyword, message, modelDeploymentId, page, pageSize, statusFilter, supplierId, templateId, testType]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    let disposed = false;
    const loadResources = async () => {
      setResourceLoading(true);
      try {
        const [deploymentItems, allDeploymentItems, supplierItems, groupItems, tokenizerItems, datasetItems, templateItems, datasetVersionItems, templateVersionItems] = await Promise.all([
          fetchAllPages<Deployment>(params => modelDeploymentsApi.list({ ...params, status: 'active' })),
          fetchAllPages<Deployment>(params => modelDeploymentsApi.list(params)),
          fetchAllPages<Supplier>(params => suppliersApi.list(params)),
          fetchAllPages<ComparisonGroup>(params => comparisonGroupsApi.list(params)),
          fetchAllPages<ResourceItem>(params => tokenizersApi.list({ ...params, status: 'ready' })),
          fetchAllPages<ResourceItem>(params => datasetsApi.list(params)),
          fetchAllPages<ResourceItem>(params => templatesApi.list(params)),
          fetchAllPages<VersionItem>(params => datasetsApi.listVersions({ ...params, status: 'ready' })),
          fetchAllPages<VersionItem>(params => templatesApi.listVersions(params)),
        ]);
        if (disposed) return;
        setDeployments(deploymentItems);
        setAllDeployments(allDeploymentItems);
        setSuppliers(supplierItems);
        setComparisonGroups(groupItems);
        setTokenizers(tokenizerItems);
        setDatasets(datasetItems);
        setTemplates(templateItems);
        setDatasetVersions(datasetVersionItems.filter(item => item.status === 'ready'));
        setTemplateVersions(templateVersionItems);
      } catch (error: any) {
        if (!disposed) message.error(error.response?.data?.error?.message || '任务资源读取失败');
      } finally {
        if (!disposed) setResourceLoading(false);
      }
    };
    void loadResources();
    return () => { disposed = true; };
  }, [message]);

  useEffect(() => () => {
    resourceSearchTimers.current.forEach(timer => clearTimeout(timer));
    resourceSearchTimers.current.clear();
  }, []);

  const queueResourceSearch = (key: string, value: string, search: () => Promise<void>) => {
    const previous = resourceSearchTimers.current.get(key);
    if (previous) clearTimeout(previous);
    if (!value.trim()) return;
    const timer = setTimeout(() => {
      void search().catch((error: any) => message.error(error.response?.data?.error?.message || '资源搜索失败'));
    }, 250);
    resourceSearchTimers.current.set(key, timer);
  };

  const mergeResourceItems = <T extends { id: string }>(current: T[], incoming: T[]) => {
    const byId = new Map(current.map(item => [item.id, item]));
    incoming.forEach(item => byId.set(item.id, item));
    return [...byId.values()];
  };

  const searchDeployments = (value: string, activeOnly: boolean) => queueResourceSearch(
    activeOnly ? 'active-deployments' : 'all-deployments',
    value,
    async () => {
      const response = await modelDeploymentsApi.list({ skip: 0, limit: 100, status: activeOnly ? 'active' : undefined, keyword: value.trim() });
      if (activeOnly) setDeployments(current => mergeResourceItems(current, response.data.items || []));
      else setAllDeployments(current => mergeResourceItems(current, response.data.items || []));
    },
  );

  const searchSuppliers = (value: string) => queueResourceSearch('suppliers', value, async () => {
    const response = await suppliersApi.list({ skip: 0, limit: 100, keyword: value.trim() });
    setSuppliers(current => mergeResourceItems(current, response.data.items || []));
  });

  const searchGroups = (value: string) => queueResourceSearch('comparison-groups', value, async () => {
    const response = await comparisonGroupsApi.list({ skip: 0, limit: 100, keyword: value.trim() });
    setComparisonGroups(current => mergeResourceItems(current, response.data.items || []));
  });

  const searchTemplates = (value: string) => queueResourceSearch('templates', value, async () => {
    const response = await templatesApi.list({ skip: 0, limit: 100, keyword: value.trim() });
    setTemplates(current => mergeResourceItems(current, response.data.items || []));
    const versions = await templatesApi.listVersions({ skip: 0, limit: 100, keyword: value.trim() });
    setTemplateVersions(current => mergeResourceItems(current, versions.data.items || []));
  });

  const searchDatasetVersions = (value: string) => queueResourceSearch('dataset-versions', value, async () => {
    const response = await datasetsApi.listVersions({ skip: 0, limit: 100, status: 'ready', keyword: value.trim() });
    setDatasetVersions(current => mergeResourceItems(current, response.data.items || []));
    const datasetsResponse = await datasetsApi.list({ skip: 0, limit: 100, keyword: value.trim() });
    setDatasets(current => mergeResourceItems(current, datasetsResponse.data.items || []));
  });

  const searchTokenizers = (value: string) => queueResourceSearch('tokenizers', value, async () => {
    const response = await tokenizersApi.list({ skip: 0, limit: 100, status: 'ready', keyword: value.trim() });
    setTokenizers(current => mergeResourceItems(current, response.data.items || []));
  });

  const openCreate = () => {
    setEditingTaskId(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = async (task: Task) => {
    try {
      const response = await tasksApi.get(task.id);
      const config = response.data.config_snapshot || task.config_snapshot || {};
      const selectedDeploymentIds = Array.isArray(config.model_deployment_ids)
        ? config.model_deployment_ids as string[]
        : [];
      const [selectedDeployments, selectedDatasetVersions, selectedTokenizer] = await Promise.all([
        Promise.all(selectedDeploymentIds.map(id => modelDeploymentsApi.get(id).then(result => result.data).catch(() => null))),
        config.dataset_version_id
          ? datasetsApi.listVersions({ version_id: String(config.dataset_version_id), limit: 1 }).then(result => result.data.items || [])
          : Promise.resolve([]),
        config.tokenizer_id
          ? tokenizersApi.get(String(config.tokenizer_id)).then(result => result.data).catch(() => null)
          : Promise.resolve(null),
      ]);
      setDeployments(current => mergeResourceItems(current, selectedDeployments.filter(Boolean)));
      setAllDeployments(current => mergeResourceItems(current, selectedDeployments.filter(Boolean)));
      setDatasetVersions(current => mergeResourceItems(current, selectedDatasetVersions));
      if (selectedTokenizer) setTokenizers(current => mergeResourceItems(current, [selectedTokenizer]));
      const jsonFields = ['generation_params', 'load_profile', 'retry_policy', 'timeouts', 'limits', 'messages', 'quality_samples', 'evalscope_datasets', 'dataset_args'];
      const values: Record<string, unknown> = {
        name: response.data.name || task.name,
        run_mode: response.data.run_mode,
        model_deployment_ids: selectedDeploymentIds,
        dataset_version_id: config.dataset_version_id || undefined,
        tokenizer_id: config.tokenizer_id || undefined,
        input_length_preset: config.input_length_preset || undefined,
        evaluation_kind: config.evaluation_kind || 'performance',
        evaluation_engine: config.evaluation_engine || 'evalscope',
        quality_mode: config.quality_mode || 'text',
        test_type: config.test_type || undefined,
        scenario: config.scenario || undefined,
        request_count: config.request_count || 1,
        warmup_count: config.warmup_count ?? 0,
        stream: Boolean(config.stream),
        code_sandbox_image: config.code_sandbox_image || undefined,
        code_timeout_seconds: config.code_timeout_seconds || undefined,
        code_memory_mb: config.code_memory_mb || undefined,
        code_cpus: config.code_cpus || undefined,
        code_pids_limit: config.code_pids_limit || undefined,
      };
      for (const field of jsonFields) {
        if (config[field] !== undefined && config[field] !== null) values[field] = JSON.stringify(config[field]);
      }
      form.setFieldsValue(values);
      setEditingTaskId(task.id);
      setModalOpen(true);
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '草稿读取失败');
    }
  };

  const handleCreate = async () => {
    if (submitting) return;
    try {
      setSubmitting(true);
      const values = await form.validateFields();
      const payload: Record<string, unknown> = { ...values };
      for (const field of ['generation_params', 'load_profile', 'retry_policy', 'timeouts', 'limits', 'messages', 'quality_samples', 'evalscope_datasets', 'dataset_args']) {
        const raw = payload[field];
        if (typeof raw === 'string' && raw.trim()) {
          try { payload[field] = JSON.parse(raw); }
          catch { message.error(`${field} 必须是有效 JSON`); return; }
        } else if (raw === '') {
          delete payload[field];
        }
      }
      if (editingTaskId) {
        delete payload.template_version_id;
        await tasksApi.update(editingTaskId, payload);
      } else {
        await tasksApi.create(payload);
      }
      message.success(editingTaskId ? '草稿已保存' : '创建成功');
      setModalOpen(false);
      setEditingTaskId(null);
      form.resetFields();
      load();
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error.response?.data?.error?.message || error.message || '任务创建失败');
    }
    finally { setSubmitting(false); }
  };

  const handleRetest = async (id: string) => {
    try {
      const response = await tasksApi.retest(id);
      const differences = response.data?.differences || [];
      message.success(`已创建复测草稿${differences.length ? `，检测到 ${differences.length} 项当前差异` : ''}`);
      if (differences.length) {
        Modal.info({
          title: '复测差异',
          content: <pre style={{ maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{JSON.stringify(differences, null, 2)}</pre>,
        });
      }
      load();
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '复测创建失败');
    }
  };

  const handleSubmit = async (id: string) => {
    try {
      await tasksApi.submit(id);
      message.success('提交成功');
      load();
    } catch (e: any) {
      let task = data.find(item => item.id === id);
      let precheck = task?.precheck_result;
      try {
        const latest = await tasksApi.get(id);
        task = latest.data as Task;
        precheck = task.precheck_result;
      } catch {
        // Keep the original submit error when the task cannot be re-read.
      }
      if (precheck?.checks?.length) {
        const formatValue = (value: unknown) => {
          if (value === undefined || value === null) return '';
          if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
          try { return JSON.stringify(value); } catch { return String(value); }
        };
        const statusColor: Record<string, string> = { passed: 'green', warning: 'orange', failed: 'red' };
        Modal.confirm({
          title: '任务预检未通过',
          width: 760,
          okText: task ? '编辑任务' : '关闭',
          cancelText: task ? '关闭' : undefined,
          onOk: task ? () => void openEdit(task) : undefined,
          content: (
            <Space direction="vertical" style={{ width: '100%' }}>
              {precheck.checks.map((check, index) => {
                const values = [
                  check.target ? `目标: ${check.target}` : '',
                  check.current !== undefined ? `当前: ${formatValue(check.current)}` : '',
                  check.limit !== undefined ? `限制: ${formatValue(check.limit)}` : '',
                ].filter(Boolean).join('；');
                return (
                  <div key={`${check.name || 'check'}-${index}`}>
                    <Space wrap>
                      <Tag color={statusColor[check.status || '']}>{check.status || 'unknown'}</Tag>
                      <Typography.Text strong>{check.name || '检查项'}</Typography.Text>
                      {check.detail && <Typography.Text>{check.detail}</Typography.Text>}
                    </Space>
                    {values && <div><Typography.Text type="secondary">{values}</Typography.Text></div>}
                  </div>
                );
              })}
            </Space>
          ),
        });
      } else {
        message.error(e.response?.data?.error?.message || '提交失败');
      }
    }
  };

  const handleCancel = async (id: string) => {
    try {
      await tasksApi.cancel(id);
      message.success('已取消');
      load();
    } catch (e: any) {
      message.error(e.response?.data?.error?.message || '取消失败');
    }
  };

  const handleReport = async (id: string) => {
    if (reportingTaskId) return;
    const reportWindow = window.open('', '_blank', 'noopener,noreferrer');
    setReportingTaskId(id);
    try {
      let key = reportKeys.current.get(id);
      if (!key) {
        key = typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random()}`;
        reportKeys.current.set(id, key);
      }
      const response = await reportsApi.generate(id, key);
      reportKeys.current.delete(id);
      const downloadUrl = response.data.download_url as string | undefined;
      if (downloadUrl && reportWindow) {
        reportWindow.location.href = downloadUrl;
      } else if (reportWindow) {
        reportWindow.close();
      }
      message.success('报告已生成');
    } catch (e: any) {
      reportWindow?.close();
      message.error(e.response?.data?.error?.message || '报告生成失败');
    } finally { setReportingTaskId(null); }
  };

  const statusColorMap: Record<string, string> = {
    draft: 'default', queued: 'blue', running: 'processing', completed: 'success',
    awaiting_code: 'processing', partially_completed: 'warning', failed: 'error', cancelling: 'orange', cancelled: 'default',
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0 }}>评测任务</h3>
        <Space wrap>
          <Input.Search allowClear placeholder="搜索任务名称或 ID" onSearch={value => { setKeyword(value); setPage(1); }} onChange={event => { if (!event.target.value) { setKeyword(''); setPage(1); } }} />
          <Select
            allowClear
            placeholder="按状态筛选"
            value={statusFilter}
            onChange={value => { setStatusFilter(value); setPage(1); }}
            options={[
              { value: 'draft', label: '草稿' },
              { value: 'queued', label: '排队中' },
              { value: 'running', label: '运行中' },
              { value: 'cancelling', label: '取消中' },
              { value: 'completed', label: '已完成' },
              { value: 'partially_completed', label: '部分完成' },
              { value: 'failed', label: '失败' },
              { value: 'cancelled', label: '已取消' },
            ]}
          />
          <Select
            allowClear showSearch optionFilterProp="label" style={{ minWidth: 180 }}
            placeholder="按供应商筛选" value={supplierId}
            onSearch={searchSuppliers}
            onChange={value => { setSupplierId(value); setPage(1); }}
            options={suppliers.map(item => ({ value: item.id, label: item.name }))}
          />
          <Select
            allowClear showSearch optionFilterProp="label" style={{ minWidth: 220 }}
            placeholder="按模型筛选" value={modelDeploymentId}
            onSearch={value => searchDeployments(value, false)}
            onChange={value => { setModelDeploymentId(value); setPage(1); }}
            options={allDeployments.map(item => ({ value: item.id, label: `${item.display_name || item.model_id} (${item.status})` }))}
          />
          <Select
            allowClear showSearch optionFilterProp="label" style={{ minWidth: 180 }}
            placeholder="按对比组筛选" value={comparisonGroupId}
            onSearch={searchGroups}
            onChange={value => { setComparisonGroupId(value); setPage(1); }}
            options={comparisonGroups.map(item => ({ value: item.id, label: item.name }))}
          />
          <Select
            allowClear showSearch optionFilterProp="label" style={{ minWidth: 180 }}
            placeholder="按模板筛选" value={templateId}
            onSearch={searchTemplates}
            onChange={value => { setTemplateId(value); setPage(1); }}
            options={templates.map(item => ({ value: item.id, label: item.name }))}
          />
          <Select
            allowClear style={{ minWidth: 140 }} placeholder="按评测类型筛选" value={testType}
            onChange={value => { setTestType(value); setPage(1); }}
            options={[{ value: 'performance', label: '性能' }, { value: 'quality', label: '质量' }, { value: 'code', label: '代码' }]}
          />
          <Input type="datetime-local" value={createdFrom} onChange={event => { setCreatedFrom(event.target.value); setPage(1); }} aria-label="创建时间起点" />
          <Input type="datetime-local" value={createdTo} onChange={event => { setCreatedTo(event.target.value); setPage(1); }} aria-label="创建时间终点" />
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建任务</Button>
        </Space>
      </div>

      <Table
        dataSource={data} rowKey="id" loading={loading}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (nextPage, nextPageSize) => {
            setPage(nextPageSize !== pageSize ? 1 : nextPage);
            setPageSize(nextPageSize);
          },
        }}
        columns={[
          { title: '任务名称', dataIndex: 'name', render: (v: string) => v || '-' },
          { title: '运行模式', dataIndex: 'run_mode', render: (v: string) => <Tag>{v}</Tag> },
          { title: '状态', dataIndex: 'status', render: (v: string) => <Tag color={statusColorMap[v]}>{v}</Tag> },
          { title: '创建时间', dataIndex: 'created_at', render: (v: string) => new Date(v).toLocaleString('zh-CN') },
          { title: '操作', render: (_, record) => (
            <Space>
              {record.status === 'draft' && (
                <>
                  <Button size="small" onClick={() => void openEdit(record)}>编辑</Button>
                  <Popconfirm title="确认提交此任务？" onConfirm={() => handleSubmit(record.id)}>
                    <Button size="small" type="primary" icon={<SendOutlined />}>提交</Button>
                  </Popconfirm>
                </>
              )}
              {['draft', 'queued', 'running'].includes(record.status) && (
                <Popconfirm title="确认取消此任务？" onConfirm={() => handleCancel(record.id)}>
                  <Button size="small" danger icon={<StopOutlined />}>取消</Button>
                </Popconfirm>
              )}
              {['completed', 'partially_completed', 'failed', 'cancelled'].includes(record.status) && (
                <>
                  <Button size="small" onClick={() => void handleRetest(record.id)}>复测</Button>
                  <Button size="small" loading={reportingTaskId === record.id} onClick={() => void handleReport(record.id)}>生成报告</Button>
                </>
              )}
            </Space>
          )},
        ]}
      />

      <Modal title={editingTaskId ? '编辑草稿任务' : '新建任务'} open={modalOpen} onOk={handleCreate} onCancel={() => { setModalOpen(false); setEditingTaskId(null); }} confirmLoading={submitting}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="任务名称" rules={[{ required: true }]}>
            <Input placeholder="如：GPT-4o 性能基线测试" />
          </Form.Item>
          <Form.Item name="template_version_id" label="测试方案模板版本">
            <Select
              allowClear
              loading={resourceLoading}
              showSearch
              optionFilterProp="label"
              onSearch={searchTemplates}
              options={templateVersions.map(version => {
                const template = templates.find(item => item.id === version.template_id);
                return { value: version.id, label: `${template?.name || '模板'} · v${version.version}${version.scenario ? ` · ${version.scenario}` : ''}` };
              })}
              placeholder="可选，选择后自动带入模板参数"
              onChange={(value: string | undefined) => {
                const selected = templateVersions.find(item => item.id === value);
                if (!selected) return;
                form.setFieldsValue({
                  evaluation_kind: selected.test_type === 'quality' || selected.test_type === 'code' ? 'quality' : 'performance',
                  evaluation_engine: selected.test_type === 'code' ? 'native' : 'evalscope',
                  quality_mode: selected.test_type === 'code' ? 'code' : 'text',
                  request_count: selected.request_count,
                  warmup_count: selected.warmup_count,
                  dataset_version_id: selected.dataset_version_id || undefined,
                  tokenizer_id: selected.tokenizer_id || undefined,
                  input_length_preset: selected.input_length_preset || undefined,
                  stream: selected.stream_mode === 'stream' ? true : selected.stream_mode === 'nonstream' ? false : undefined,
                  generation_params: selected.generation_params ? JSON.stringify(selected.generation_params) : undefined,
                  evalscope_datasets: selected.evalscope_datasets ? JSON.stringify(selected.evalscope_datasets) : undefined,
                  load_profile: selected.load_profile ? JSON.stringify(selected.load_profile) : undefined,
                  retry_policy: selected.retry_policy ? JSON.stringify(selected.retry_policy) : undefined,
                  timeouts: selected.timeouts ? JSON.stringify(selected.timeouts) : undefined,
                  limits: selected.limits ? JSON.stringify(selected.limits) : undefined,
                });
              }}
            />
          </Form.Item>
          <Form.Item name="run_mode" label="运行模式" rules={[{ required: true }]}>
            <Select options={[{ value: 'serial', label: '串行' }, { value: 'parallel', label: '并行' }]} />
          </Form.Item>
          <Form.Item name="model_deployment_ids" label="目标模型部署" rules={[{ required: true, message: '至少选择一个模型部署' }]}>
            <Select loading={resourceLoading} mode="multiple" allowClear showSearch optionFilterProp="label" onSearch={value => searchDeployments(value, true)} options={deployments.map(deployment => ({ value: deployment.id, label: `${deployment.display_name || deployment.model_id} (${deployment.model_id})` }))} placeholder="可多选模型进行横向评测" />
          </Form.Item>
          <Space size="large" wrap style={{ width: '100%' }}>
            <Form.Item name="dataset_version_id" label="数据集版本">
              <Select allowClear loading={resourceLoading} showSearch optionFilterProp="label" onSearch={searchDatasetVersions} style={{ minWidth: 280 }} options={datasetVersions.map(version => {
                const dataset = datasets.find(item => item.id === version.dataset_id);
                return { value: version.id, label: `${dataset?.name || '数据集'} · v${version.version}` };
              })} placeholder="选择 ready 数据集版本" />
            </Form.Item>
            <Form.Item name="tokenizer_id" label="Tokenizer">
              <Select allowClear loading={resourceLoading} showSearch optionFilterProp="label" onSearch={searchTokenizers} style={{ minWidth: 240 }} options={tokenizers.map(item => ({ value: item.id, label: `${item.name} (${item.status})` }))} placeholder="选择 ready Tokenizer" />
            </Form.Item>
          </Space>
          <Form.Item name="input_length_preset" label="输入长度预设">
            <Select
              allowClear
              placeholder="可选，按 Tokenizer 生成固定长度输入"
              options={LENGTH_PRESETS.map(preset => ({
                value: preset.value,
                label: `${preset.label}（输入 ${preset.inputTokens.toLocaleString()} / 输出 ${preset.outputTokens.toLocaleString()} Token）`,
              }))}
            />
          </Form.Item>
          <Space size="large" wrap>
            <Form.Item name="evaluation_kind" label="评测类型" initialValue="performance">
              <Select style={{ width: 140 }} options={[{ value: 'performance', label: '性能' }, { value: 'quality', label: '质量' }]} />
            </Form.Item>
            <Form.Item name="evaluation_engine" label="评测引擎" initialValue="evalscope">
              <Select style={{ width: 140 }} options={qualityMode === 'code' ? [{ value: 'native', label: '代码沙箱' }] : [{ value: 'evalscope', label: 'EvalScope' }]} />
            </Form.Item>
            <Form.Item name="quality_mode" label="质量模式" initialValue="text">
              <Select
                style={{ width: 120 }}
                options={[{ value: 'text', label: '文本' }, { value: 'code', label: '代码' }]}
                onChange={(value: 'text' | 'code') => form.setFieldsValue({
                  ...(value === 'code' ? { evaluation_kind: 'quality' } : {}),
                  evaluation_engine: value === 'code' ? 'native' : 'evalscope',
                })}
              />
            </Form.Item>
          </Space>
          <Space size="large" wrap>
            <Form.Item name="request_count" label="请求数" initialValue={1}>
              <InputNumber min={1} max={1000000} />
            </Form.Item>
            <Form.Item name="warmup_count" label="预热数" initialValue={3}>
              <InputNumber min={0} max={1000} />
            </Form.Item>
            <Form.Item name="stream" label="流式请求" valuePropName="checked" initialValue={false}>
              <Switch />
            </Form.Item>
          </Space>
          <Form.Item name="messages" label="性能请求 messages（JSON）">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} placeholder='[{"role":"user","content":"ping"}]' />
          </Form.Item>
          <Form.Item name="generation_params" label="生成参数（JSON）">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} placeholder='{"temperature":0,"max_tokens":128}' />
          </Form.Item>
          <Form.Item name="load_profile" label="负载配置（JSON）">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} placeholder='{"mode":"fixed_concurrency","concurrency":1}' />
          </Form.Item>
          <Form.Item name="retry_policy" label="重试策略（JSON）">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} placeholder='{"max_retries":2}' />
          </Form.Item>
          <Form.Item name="timeouts" label="超时配置（JSON）">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} placeholder='{"connect":10,"read":60}' />
          </Form.Item>
          <Form.Item name="limits" label="限额配置（JSON）">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} placeholder='{"concurrency":4,"rps":2}' />
          </Form.Item>
          <Form.Item name="quality_samples" label="质量样本（JSON 数组）">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} placeholder='[{"messages":[{"role":"user","content":"2+2?"}],"expected":"4"}]' />
          </Form.Item>
          <Form.Item name="evalscope_datasets" label="EvalScope 数据集（JSON 数组）">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 3 }} placeholder='["mmlu"]' />
          </Form.Item>
          <Form.Item name="dataset_args" label="数据集参数（JSON）">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 3 }} placeholder='{}' />
          </Form.Item>
          <Space size="large" wrap>
            <Form.Item name="code_sandbox_image" label="代码沙箱镜像">
              <Input placeholder="python:3.11-slim" />
            </Form.Item>
            <Form.Item name="code_timeout_seconds" label="代码超时（秒）"><InputNumber min={1} max={300} /></Form.Item>
            <Form.Item name="code_memory_mb" label="代码内存（MB）"><InputNumber min={1} max={4096} /></Form.Item>
            <Form.Item name="code_cpus" label="代码 CPU"><InputNumber min={0.1} max={8} step={0.1} /></Form.Item>
            <Form.Item name="code_pids_limit" label="代码进程数"><InputNumber min={1} max={1024} /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
