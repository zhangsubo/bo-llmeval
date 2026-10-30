import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  App,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileAddOutlined,
  PlusOutlined,
  UploadOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { tokenizersApi, datasetsApi, templatesApi } from '../api';

const { Title, Text } = Typography;
type Item = Record<string, any>;

interface BuiltinDataset {
  key: string;
  name: string;
  description: string;
  source_id: string;
  split: string;
  subset: string;
  scoring_method: string;
  license: string;
}

const LENGTH_PRESETS = [
  { value: 'short', label: '短（256 / 128 Token）' },
  { value: 'medium', label: '中（2,048 / 512 Token）' },
  { value: 'long', label: '长（8,192 / 1,024 Token）' },
  { value: 'extra_long', label: '超长（32,768 / 2,048 Token）' },
];

export default function ResourcesPage() {
  const { message } = App.useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [tokenizers, setTokenizers] = useState<Item[]>([]);
  const [datasets, setDatasets] = useState<Item[]>([]);
  const [templates, setTemplates] = useState<Item[]>([]);
  const [builtins, setBuiltins] = useState<BuiltinDataset[]>([]);
  const [datasetVersions, setDatasetVersions] = useState<Item[]>([]);
  const [templateVersions, setTemplateVersions] = useState<Item[]>([]);
  const [datasetVersionPage, setDatasetVersionPage] = useState(1);
  const [templateVersionPage, setTemplateVersionPage] = useState(1);
  const [datasetVersionTotal, setDatasetVersionTotal] = useState(0);
  const [templateVersionTotal, setTemplateVersionTotal] = useState(0);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string>();
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [tokenizerPage, setTokenizerPage] = useState(1);
  const [datasetPage, setDatasetPage] = useState(1);
  const [templatePage, setTemplatePage] = useState(1);
  const [tokenizerTotal, setTokenizerTotal] = useState(0);
  const [datasetTotal, setDatasetTotal] = useState(0);
  const [templateTotal, setTemplateTotal] = useState(0);
  const resourcePageSize = 20;
  const [versionLoading, setVersionLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [tokenizerOpen, setTokenizerOpen] = useState(false);
  const [datasetOpen, setDatasetOpen] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [datasetVersionOpen, setDatasetVersionOpen] = useState(false);
  const [templateVersionOpen, setTemplateVersionOpen] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const [copySource, setCopySource] = useState<Item>();
  const [tokenizerForm] = Form.useForm();
  const [datasetForm] = Form.useForm();
  const [datasetVersionForm] = Form.useForm();
  const [templateForm] = Form.useForm();
  const [templateVersionForm] = Form.useForm();
  const [copyForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [tokenizerResponse, datasetResponse, templateResponse, builtinResponse] = await Promise.all([
        tokenizersApi.list({ skip: (tokenizerPage - 1) * resourcePageSize, limit: resourcePageSize }),
        datasetsApi.list({ skip: (datasetPage - 1) * resourcePageSize, limit: resourcePageSize }),
        templatesApi.list({ skip: (templatePage - 1) * resourcePageSize, limit: resourcePageSize }),
        datasetsApi.builtinCatalog(),
      ]);
      setTokenizers(tokenizerResponse.data.items || []);
      setTokenizerTotal(tokenizerResponse.data.total || 0);
      setDatasets(datasetResponse.data.items || []);
      setDatasetTotal(datasetResponse.data.total || 0);
      setTemplates(templateResponse.data.items || []);
      setTemplateTotal(templateResponse.data.total || 0);
      setBuiltins(builtinResponse.data.items || []);
    } catch {
      message.error('测试资源读取失败');
    } finally {
      setLoading(false);
    }
  }, [datasetPage, message, templatePage, tokenizerPage]);

  useEffect(() => { void load(); }, [load]);

  const createTokenizer = async () => {
    try {
      setSubmitting(true);
      await tokenizersApi.create(await tokenizerForm.validateFields());
      message.success('Tokenizer 已创建');
      setTokenizerOpen(false);
      tokenizerForm.resetFields();
      await load();
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error.response?.data?.error?.message || 'Tokenizer 创建失败');
    } finally { setSubmitting(false); }
  };

  const createDataset = async () => {
    try {
      setSubmitting(true);
      await datasetsApi.create(await datasetForm.validateFields());
      message.success('数据集已创建');
      setDatasetOpen(false);
      datasetForm.resetFields();
      await load();
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error.response?.data?.error?.message || '数据集创建失败');
    } finally { setSubmitting(false); }
  };

  const showDatasetVersions = async (datasetId: string, nextPage = datasetVersionPage) => {
    const page = selectedDatasetId === datasetId ? nextPage : 1;
    setSelectedDatasetId(datasetId);
    setDatasetVersionOpen(true);
    setVersionLoading(true);
    try {
      const response = await datasetsApi.versions(datasetId, {
        skip: (page - 1) * resourcePageSize,
        limit: resourcePageSize,
      });
      setDatasetVersionPage(page);
      setDatasetVersions(response.data.items || []);
      setDatasetVersionTotal(response.data.total || 0);
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '数据集版本读取失败');
      setDatasetVersions([]);
    } finally { setVersionLoading(false); }
  };

  const createDatasetVersion = async () => {
    if (!selectedDatasetId) return;
    try {
      setSubmitting(true);
      await datasetsApi.createVersion(selectedDatasetId, await datasetVersionForm.validateFields());
      message.success('数据集版本已创建');
      datasetVersionForm.resetFields();
      await showDatasetVersions(selectedDatasetId);
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error.response?.data?.error?.message || '数据集版本创建失败');
    } finally { setSubmitting(false); }
  };

  const uploadDataset = async (datasetId: string, file: File) => {
    try {
      await datasetsApi.uploadVersion(datasetId, file);
      message.success('数据集文件已上传并校验');
      if (selectedDatasetId === datasetId) await showDatasetVersions(datasetId);
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '数据集上传失败');
    }
    return false;
  };

  const downloadBuiltin = async (key: string) => {
    try {
      await datasetsApi.downloadBuiltin(key);
      message.success('内置数据集已下载');
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '内置数据集下载失败');
    }
  };

  const createTemplate = async () => {
    try {
      setSubmitting(true);
      await templatesApi.create(await templateForm.validateFields());
      message.success('模板已创建');
      setTemplateOpen(false);
      templateForm.resetFields();
      await load();
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error.response?.data?.error?.message || '模板创建失败');
    } finally { setSubmitting(false); }
  };

  const showTemplateVersions = async (templateId: string, nextPage = templateVersionPage) => {
    const page = selectedTemplateId === templateId ? nextPage : 1;
    setSelectedTemplateId(templateId);
    setTemplateVersionOpen(true);
    setVersionLoading(true);
    try {
      const response = await templatesApi.versions(templateId, {
        skip: (page - 1) * resourcePageSize,
        limit: resourcePageSize,
      });
      setTemplateVersionPage(page);
      setTemplateVersions(response.data.items || []);
      setTemplateVersionTotal(response.data.total || 0);
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '模板版本读取失败');
      setTemplateVersions([]);
    } finally { setVersionLoading(false); }
  };

  const parseJsonField = (payload: Item, field: string) => {
    const raw = payload[field];
    if (typeof raw !== 'string' || !raw.trim()) {
      delete payload[field];
      return;
    }
    try { payload[field] = JSON.parse(raw); }
    catch { throw new Error(`${field} 必须是有效 JSON`); }
  };

  const createTemplateVersion = async () => {
    if (!selectedTemplateId) return;
    try {
      setSubmitting(true);
      const payload = await templateVersionForm.validateFields();
      ['generation_params', 'load_profile', 'retry_policy', 'timeouts', 'limits'].forEach(field => parseJsonField(payload, field));
      await templatesApi.createVersion(selectedTemplateId, payload);
      message.success('模板新版本已创建');
      templateVersionForm.resetFields();
      await showTemplateVersions(selectedTemplateId);
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error.response?.data?.error?.message || error.message || '模板版本创建失败');
    } finally { setSubmitting(false); }
  };

  const copyTemplate = async () => {
    if (!copySource) return;
    try {
      setSubmitting(true);
      const values = await copyForm.validateFields();
      await templatesApi.copy(copySource.id, values.new_name);
      message.success('模板已复制');
      setCopyOpen(false);
      copyForm.resetFields();
      await load();
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error.response?.data?.error?.message || '模板复制失败');
    } finally { setSubmitting(false); }
  };

  const tokenizerPanel = <>
    <div className="resource-toolbar">
      <Text type="secondary">管理本地 Tokenizer 资源，任务提交时只能选择 ready 版本。</Text>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => setTokenizerOpen(true)}>新建 Tokenizer</Button>
    </div>
    <Table rowKey="id" loading={loading} dataSource={tokenizers} columns={[
      { title: '名称', dataIndex: 'name' },
      { title: '来源', dataIndex: 'source', render: (value: string) => <Tag>{value}</Tag> },
      { title: '资源标识', dataIndex: 'resource_id', render: (value: string | null) => value || '-' },
      { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'ready' ? 'green' : value === 'failed' ? 'red' : 'default'}>{value}</Tag> },
      { title: '操作', render: (_: unknown, item: Item) => <Space>
        {item.status !== 'ready' && item.source !== 'upload' ? <Button size="small" icon={<DownloadOutlined />} onClick={() => tokenizersApi.download(item.id).then(load).catch((error: any) => message.error(error.response?.data?.error?.message || 'Tokenizer 下载失败'))}>下载</Button> : item.status !== 'ready' ? <Upload showUploadList={false} beforeUpload={(file) => { void tokenizersApi.upload(item.id, file).then(load).catch((error: any) => message.error(error.response?.data?.error?.message || 'Tokenizer 上传失败')); return false; }}><Button size="small" icon={<UploadOutlined />}>上传 ZIP</Button></Upload> : <Text type="secondary">已就绪</Text>}
        <Popconfirm title="确认删除此 Tokenizer？" description="已就绪的本地文件将一并清理" onConfirm={() => tokenizersApi.delete(item.id).then(() => { message.success('已删除'); load(); }).catch((error: any) => message.error(error.response?.data?.error?.message || '删除失败'))} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space> },
    ]} pagination={{ current: tokenizerPage, pageSize: resourcePageSize, total: tokenizerTotal, onChange: (nextPage) => { setTokenizerPage(nextPage); } }} />
  </>;

  const datasetPanel = <>
    <div className="resource-toolbar">
      <Text type="secondary">内置 benchmark 和自定义 JSONL/CSV 都会生成不可变版本。</Text>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => setDatasetOpen(true)}>新建数据集</Button>
    </div>
    <Card size="small" title="内置质量数据集" style={{ marginBottom: 16 }}>
      <List loading={loading} dataSource={builtins} grid={{ gutter: 12, column: 2 }} renderItem={(item) => <List.Item>
        <Card size="small" title={item.name} extra={<Button size="small" icon={<DownloadOutlined />} onClick={() => downloadBuiltin(item.key)}>下载</Button>}>
          <Text type="secondary">{item.description}</Text>
          <div><Tag>{item.scoring_method}</Tag><Tag>{item.split}/{item.subset}</Tag></div>
        </Card>
      </List.Item>} />
    </Card>
    <Table rowKey="id" loading={loading} dataSource={datasets} columns={[
      { title: '名称', dataIndex: 'name' },
      { title: '用途', dataIndex: 'kind', render: (value: string) => <Tag>{value}</Tag> },
      { title: '类型', render: (_: unknown, item: Item) => item.is_builtin ? <Tag color="blue">内置</Tag> : <Tag>自定义</Tag> },
      { title: '操作', render: (_: unknown, item: Item) => <Space><Button size="small" icon={<UnorderedListOutlined />} onClick={() => showDatasetVersions(item.id)}>版本</Button>{!item.is_builtin && <Upload showUploadList={false} beforeUpload={(file) => uploadDataset(item.id, file)}><Button size="small" icon={<UploadOutlined />}>上传文件</Button></Upload>}</Space> },
    ]} pagination={{ current: datasetPage, pageSize: resourcePageSize, total: datasetTotal, onChange: (nextPage) => { setDatasetPage(nextPage); } }} />
  </>;

  const templatePanel = <>
    <div className="resource-toolbar">
      <Text type="secondary">模板版本不可变；内置模板需要先复制才能自定义。</Text>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => setTemplateOpen(true)}>新建模板</Button>
    </div>
    <Table rowKey="id" loading={loading} dataSource={templates} columns={[
      { title: '名称', dataIndex: 'name' },
      { title: '类型', render: (_: unknown, item: Item) => item.is_builtin ? <Tag color="blue">内置</Tag> : <Tag>自定义</Tag> },
      { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
      { title: '操作', render: (_: unknown, item: Item) => <Space><Button size="small" icon={<UnorderedListOutlined />} onClick={() => showTemplateVersions(item.id)}>版本</Button>{!item.is_builtin && <Button size="small" icon={<FileAddOutlined />} onClick={() => { setSelectedTemplateId(item.id); setTemplateVersionOpen(true); templateVersionForm.resetFields(); }}>新版本</Button>}<Button size="small" icon={<CopyOutlined />} onClick={() => { setCopySource(item); setCopyOpen(true); copyForm.setFieldsValue({ new_name: `${item.name}（副本）` }); }}>复制</Button></Space> },
    ]} pagination={{ current: templatePage, pageSize: resourcePageSize, total: templateTotal, onChange: (nextPage) => { setTemplatePage(nextPage); } }} />
  </>;

  const resourcePath = location.pathname.replace(/\/+$/, '');
  const resourceTab = resourcePath.endsWith('/datasets')
    ? 'datasets'
    : resourcePath.endsWith('/templates')
      ? 'templates'
      : 'tokenizers';

  return <div>
    <Title level={3}>测试资源管理</Title>
    <Tabs
      activeKey={resourceTab}
      onChange={(key) => navigate(`/${key}`)}
      items={[{ key: 'tokenizers', label: 'Tokenizer', children: tokenizerPanel }, { key: 'datasets', label: '数据集', children: datasetPanel }, { key: 'templates', label: '测试方案模板', children: templatePanel }]}
    />

    <Modal title="新建 Tokenizer" open={tokenizerOpen} onOk={createTokenizer} onCancel={() => setTokenizerOpen(false)} confirmLoading={submitting} destroyOnHidden>
      <Form form={tokenizerForm} layout="vertical"><Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="source" label="来源" rules={[{ required: true }]} initialValue="builtin"><Select options={[{ value: 'builtin', label: '内置 tiktoken' }, { value: 'huggingface', label: 'Hugging Face' }, { value: 'modelscope', label: 'ModelScope' }, { value: 'upload', label: 'ZIP 上传' }]} /></Form.Item><Form.Item name="resource_id" label="资源标识"><Input placeholder="内置 encoding 或 owner/name" /></Form.Item><Form.Item name="revision" label="版本/Revision"><Input /></Form.Item></Form>
    </Modal>

    <Modal title="新建数据集" open={datasetOpen} onOk={createDataset} onCancel={() => setDatasetOpen(false)} confirmLoading={submitting} destroyOnHidden>
      <Form form={datasetForm} layout="vertical"><Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="kind" label="用途" rules={[{ required: true }]} initialValue="quality"><Select options={[{ value: 'performance', label: '性能' }, { value: 'quality', label: '质量' }]} /></Form.Item><Form.Item name="remark" label="备注"><Input.TextArea rows={3} /></Form.Item></Form>
    </Modal>

    <Modal title="数据集版本" open={datasetVersionOpen} onCancel={() => setDatasetVersionOpen(false)} footer={null} width={820} destroyOnHidden>
      <Space style={{ marginBottom: 16 }}><Upload showUploadList={false} beforeUpload={(file) => selectedDatasetId ? uploadDataset(selectedDatasetId, file) : false}><Button icon={<UploadOutlined />}>上传 JSONL/CSV</Button></Upload></Space>
      <Form form={datasetVersionForm} layout="inline" onFinish={createDatasetVersion} style={{ marginBottom: 16 }}><Form.Item name="source" label="来源"><Input placeholder="manual" /></Form.Item><Form.Item name="sample_count" label="样本数"><InputNumber min={0} /></Form.Item><Form.Item><Button type="primary" htmlType="submit" loading={submitting}>创建 pending 版本</Button></Form.Item></Form>
      <Table rowKey="id" size="small" loading={versionLoading} dataSource={datasetVersions} pagination={{ current: datasetVersionPage, pageSize: resourcePageSize, total: datasetVersionTotal, showSizeChanger: false, onChange: (nextPage) => { void showDatasetVersions(selectedDatasetId!, nextPage); } }} columns={[{ title: '版本', dataIndex: 'version' }, { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> }, { title: '样本数', dataIndex: 'sample_count', render: (value: number | null) => value ?? '-' }, { title: '校验和', dataIndex: 'checksum', render: (value: string | null) => value ? `${value.slice(0, 12)}...` : '-' }]} />
    </Modal>

    <Modal title="新建模板" open={templateOpen} onOk={createTemplate} onCancel={() => setTemplateOpen(false)} confirmLoading={submitting} destroyOnHidden><Form form={templateForm} layout="vertical"><Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item></Form></Modal>

    <Modal title="模板版本" open={templateVersionOpen} onCancel={() => setTemplateVersionOpen(false)} footer={null} width={820} destroyOnHidden>
      <Form form={templateVersionForm} layout="vertical" onFinish={createTemplateVersion}><Space wrap><Form.Item name="test_type" label="评测类型"><Select style={{ width: 150 }} options={[{ value: 'performance', label: '性能' }, { value: 'quality', label: '质量' }, { value: 'code', label: '代码' }]} /></Form.Item><Form.Item name="scenario" label="场景"><Input /></Form.Item><Form.Item name="stream_mode" label="流式模式"><Select style={{ width: 120 }} allowClear options={[{ value: 'stream', label: '流式' }, { value: 'nonstream', label: '非流式' }]} /></Form.Item><Form.Item name="input_length_preset" label="输入长度"><Select style={{ width: 220 }} allowClear options={LENGTH_PRESETS} /></Form.Item><Form.Item name="request_count" label="请求数"><InputNumber min={1} /></Form.Item><Form.Item name="warmup_count" label="预热数"><InputNumber min={0} /></Form.Item></Space><Form.Item name="generation_params" label="生成参数 JSON"><Input.TextArea rows={2} placeholder='{"temperature":0,"max_tokens":128}' /></Form.Item><Form.Item name="load_profile" label="负载 JSON"><Input.TextArea rows={2} placeholder='{"mode":"fixed_concurrency","concurrency":1}' /></Form.Item><Form.Item name="retry_policy" label="重试策略 JSON"><Input.TextArea rows={2} placeholder='{"max_retries":2}' /></Form.Item><Button type="primary" htmlType="submit" loading={submitting}>创建新版本</Button></Form>
      <Table rowKey="id" size="small" loading={versionLoading} dataSource={templateVersions} pagination={{ current: templateVersionPage, pageSize: resourcePageSize, total: templateVersionTotal, showSizeChanger: false, onChange: (nextPage) => { void showTemplateVersions(selectedTemplateId!, nextPage); } }} style={{ marginTop: 16 }} columns={[{ title: '版本', dataIndex: 'version' }, { title: '类型', dataIndex: 'test_type', render: (value: string | null) => value || '-' }, { title: '场景', dataIndex: 'scenario', render: (value: string | null) => value || '-' }, { title: '输入长度', dataIndex: 'input_length_preset', render: (value: string | null) => LENGTH_PRESETS.find(item => item.value === value)?.label || '-' }, { title: '请求数', dataIndex: 'request_count', render: (value: number | null) => value ?? '-' }]} />
    </Modal>

    <Modal title="复制模板" open={copyOpen} onOk={copyTemplate} onCancel={() => setCopyOpen(false)} confirmLoading={submitting} destroyOnHidden><Form form={copyForm} layout="vertical"><Form.Item name="new_name" label="新模板名称" rules={[{ required: true }]}><Input /></Form.Item></Form></Modal>
  </div>;
}
