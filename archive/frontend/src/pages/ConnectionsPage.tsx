import { useCallback, useEffect, useState } from 'react';
import { App, Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Switch } from 'antd';
import { PlusOutlined, ApiOutlined, SyncOutlined, DeleteOutlined, CloudDownloadOutlined, EditOutlined, KeyOutlined } from '@ant-design/icons';
import { connectionsApi, fetchAllPages, suppliersApi } from '../api';

interface Connection {
  id: string; supplier_id: string; name: string; base_url: string; api_key_masked: string;
  status: string; region: string | null; service_tier?: string | null; credential_version: number; created_at: string;
  tls_skip_verify?: boolean; timeouts?: Record<string, number> | null; limits?: Record<string, number> | null;
  remark?: string | null; supplier_name?: string; last_test_result?: { status?: string; capabilities_status?: string }; last_test_at?: string | null;
}

function parseJsonObject(value: unknown, label: string): Record<string, unknown> | undefined {
  if (typeof value !== 'string' || !value.trim()) return undefined;
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error(`${label} 必须是有效 JSON`); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label} 必须是 JSON 对象`);
  }
  return parsed as Record<string, unknown>;
}

export default function ConnectionsPage() {
  const { message } = App.useApp();
  const [data, setData] = useState<Connection[]>([]);
  const [suppliers, setSuppliers] = useState<Array<{ id: string; name: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>();
  const [supplierFilter, setSupplierFilter] = useState<string>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [selectedConnection, setSelectedConnection] = useState<Connection | null>(null);
  const [testModalOpen, setTestModalOpen] = useState(false);
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [keyForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [supplierItems, connectionResponse] = await Promise.all([
        fetchAllPages<{ id: string; name: string }>(params => suppliersApi.list(params)),
        connectionsApi.listAll({
          skip: (page - 1) * pageSize,
          limit: pageSize,
          supplier_id: supplierFilter,
          keyword: keyword.trim() || undefined,
          status: statusFilter,
        }),
      ]);
      const sups = supplierItems;
      setSuppliers(sups);
      setTotal(connectionResponse.data.total || 0);
      const supplierNames = new Map(sups.map((supplier: { id: string; name: string }) => [supplier.id, supplier.name]));
      const connections = (connectionResponse.data.items || [])
        .map((connection: Connection) => ({ ...connection, supplier_name: supplierNames.get(connection.supplier_id) || '-' }));
      setData(connections);
    } catch (e: any) {
      message.error(e.response?.data?.error?.message || '连接列表加载失败');
      setData([]);
    } finally { setLoading(false); }
  }, [message, keyword, page, pageSize, statusFilter, supplierFilter]);
  useEffect(() => { void load(); }, [load]);

  const handleCreate = async () => {
    if (submitting) return;
    try {
      setSubmitting(true);
      const values = await form.validateFields();
      await connectionsApi.create(values.supplier_id, {
        name: values.name,
        base_url: values.base_url,
        api_key: values.api_key,
        region: values.region,
        service_tier: values.service_tier,
        tls_skip_verify: Boolean(values.tls_skip_verify),
        tls_skip_verify_confirm: Boolean(values.tls_skip_verify_confirm),
        timeouts: parseJsonObject(values.timeouts, '超时配置'),
        limits: parseJsonObject(values.limits, '限额配置'),
        remark: values.remark,
      });
      message.success('创建成功');
      setModalOpen(false);
      form.resetFields();
      load();
    } catch (error: any) {
      if (!error?.errorFields) message.error(error.response?.data?.error?.message || '连接创建失败');
    }
    finally { setSubmitting(false); }
  };

  const handleTest = async (id: string) => {
    setTestResult(null);
    setTestModalOpen(true);
    try {
      const result = await connectionsApi.test(id);
      setTestResult(result.data);
      await load();
    } catch (e: any) {
      setTestResult({ error: e.response?.data?.error?.message || '测试失败' });
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await connectionsApi.delete(id);
      message.success('已删除');
      load();
    } catch (e: any) {
      message.error(e.response?.data?.error?.message || '删除失败');
    }
  };

  const handleEdit = (connection: Connection) => {
    setSelectedConnection(connection);
    editForm.setFieldsValue({
      name: connection.name,
      region: connection.region,
      service_tier: connection.service_tier,
      status: connection.status,
      tls_skip_verify: connection.tls_skip_verify,
      // Existing unsafe connections must remain editable without forcing the
      // operator to re-confirm an already accepted setting.
      tls_skip_verify_confirm: Boolean(connection.tls_skip_verify),
      timeouts: connection.timeouts ? JSON.stringify(connection.timeouts) : '',
      limits: connection.limits ? JSON.stringify(connection.limits) : '',
      remark: connection.remark,
    });
    setEditModalOpen(true);
  };

  const handleSaveEdit = async () => {
    if (!selectedConnection || submitting) return;
    try {
      setSubmitting(true);
      const values = await editForm.validateFields();
      const tlsSkipVerify = Boolean(values.tls_skip_verify);
      await connectionsApi.update(selectedConnection.id, {
        name: values.name,
        region: values.region,
        service_tier: values.service_tier,
        status: values.status,
        tls_skip_verify: tlsSkipVerify,
        tls_skip_verify_confirm: tlsSkipVerify && (
          Boolean(values.tls_skip_verify_confirm) || Boolean(selectedConnection.tls_skip_verify)
        ),
        timeouts: parseJsonObject(values.timeouts, '超时配置'),
        limits: parseJsonObject(values.limits, '限额配置'),
        remark: values.remark,
      });
      message.success('连接配置已更新');
      setEditModalOpen(false);
      await load();
    } catch (e: any) {
      if (e?.errorFields) return;
      message.error(e.response?.data?.error?.message || e.message || '更新失败');
    } finally { setSubmitting(false); }
  };

  const handleReplaceKey = (connection: Connection) => {
    setSelectedConnection(connection);
    keyForm.resetFields();
    setKeyModalOpen(true);
  };

  const handleSaveKey = async () => {
    if (!selectedConnection || submitting) return;
    try {
      setSubmitting(true);
      const values = await keyForm.validateFields();
      await connectionsApi.replaceKey(selectedConnection.id, values.new_api_key);
      message.success('API Key 已替换');
      setKeyModalOpen(false);
      await load();
    } catch (e: any) {
      if (e?.errorFields) return;
      message.error(e.response?.data?.error?.message || 'API Key 替换失败');
    } finally { setSubmitting(false); }
  };

  const handleSync = async (id: string) => {
    try {
      const result = await connectionsApi.syncModels(id);
      const { total, added, skipped, error } = result.data;
      if (error) {
        message.error(`同步失败: ${error}`);
      } else {
        message.success(`同步完成: 共 ${total} 个模型，新增 ${added}，跳过 ${skipped}`);
      }
    } catch (e: any) {
      message.error(e.response?.data?.error?.message || '同步失败');
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0 }}>连接管理</h3>
        <Space wrap>
          <Input.Search allowClear placeholder="搜索连接名称、URL 或区域" onSearch={value => { setKeyword(value); setPage(1); }} onChange={event => { if (!event.target.value) { setKeyword(''); setPage(1); } }} />
          <Select allowClear placeholder="供应商" style={{ width: 140 }} value={supplierFilter} onChange={value => { setSupplierFilter(value); setPage(1); }} options={suppliers.map(supplier => ({ value: supplier.id, label: supplier.name }))} />
          <Select allowClear placeholder="状态" style={{ width: 110 }} value={statusFilter} onChange={value => { setStatusFilter(value); setPage(1); }} options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }, { value: 'archived', label: '归档' }]} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>新建连接</Button>
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
          { title: '连接名称', dataIndex: 'name' },
          { title: '供应商', dataIndex: 'supplier_name' },
          { title: 'Base URL', dataIndex: 'base_url', ellipsis: true },
          { title: 'API Key', dataIndex: 'api_key_masked', render: (v: string) => <code>{v}</code> },
          { title: '区域', dataIndex: 'region', render: (v: string) => v || '-' },
          { title: '凭证版本', dataIndex: 'credential_version' },
          { title: '最近测试', render: (_: unknown, record: Connection) => record.last_test_result ? <Tag color={record.last_test_result.status === 'success' ? 'green' : 'orange'}>{record.last_test_result.capabilities_status || record.last_test_result.status || '已测试'}</Tag> : '-' },
          { title: '状态', dataIndex: 'status', render: (v: string) => {
            const colorMap: Record<string, string> = { active: 'green', disabled: 'orange', archived: 'default' };
            return <Tag color={colorMap[v]}>{v}</Tag>;
          }},
          { title: '操作', render: (_, record) => (
            <Space>
              <Button size="small" icon={<ApiOutlined />} onClick={() => handleTest(record.id)}>测试</Button>
              <Button size="small" icon={<CloudDownloadOutlined />} onClick={() => handleSync(record.id)}>同步模型</Button>
              <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
              <Button size="small" icon={<KeyOutlined />} onClick={() => handleReplaceKey(record)}>换 Key</Button>
              <Popconfirm title="确认删除此连接？" onConfirm={() => handleDelete(record.id)} okText="删除" cancelText="取消">
                <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
              </Popconfirm>
            </Space>
          )},
        ]}
      />

      <Modal title="新建连接" open={modalOpen} onOk={handleCreate} onCancel={() => { setModalOpen(false); form.resetFields(); }} width={650} confirmLoading={submitting}>
        <Form form={form} layout="vertical">
          <Form.Item name="supplier_id" label="供应商" rules={[{ required: true }]}>
            <Select options={suppliers.map(s => ({ value: s.id, label: s.name }))} />
          </Form.Item>
          <Form.Item name="name" label="连接名称" rules={[{ required: true }]}>
            <Input placeholder="如：OpenAI 主账号" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL" rules={[{ required: true }]}>
            <Input placeholder="https://api.openai.com" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key" rules={[{ required: true }]}>
            <Input.Password placeholder="sk-..." />
          </Form.Item>
          <Form.Item name="region" label="区域">
            <Input placeholder="如：us-east-1" />
          </Form.Item>
          <Form.Item name="service_tier" label="服务层级">
            <Input placeholder="如：enterprise" />
          </Form.Item>
          <Space style={{ width: '100%' }} wrap>
            <Form.Item name="tls_skip_verify" label="忽略 TLS 校验" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="tls_skip_verify_confirm" label="确认 TLS 风险" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Form.Item name="timeouts" label="超时配置 JSON">
            <Input.TextArea rows={2} placeholder='{"connect":15,"read":120,"total":1800}' />
          </Form.Item>
          <Form.Item name="limits" label="限额配置 JSON">
            <Input.TextArea rows={2} placeholder='{"concurrency":4,"rps":2}' />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="编辑连接" open={editModalOpen} onOk={handleSaveEdit} onCancel={() => setEditModalOpen(false)} width={650} confirmLoading={submitting}>
        <Form form={editForm} layout="vertical">
          <Space style={{ width: '100%' }} wrap>
            <Form.Item name="name" label="连接名称" rules={[{ required: true }]}><Input style={{ width: 260 }} /></Form.Item>
            <Form.Item name="status" label="状态"><Select style={{ width: 150 }} options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }, { value: 'archived', label: '归档' }]} /></Form.Item>
            <Form.Item name="region" label="区域"><Input style={{ width: 180 }} /></Form.Item>
            <Form.Item name="service_tier" label="服务层级"><Input style={{ width: 180 }} /></Form.Item>
          </Space>
          <Form.Item name="tls_skip_verify" label="忽略 TLS 校验" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="tls_skip_verify_confirm" label="确认 TLS 风险" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="timeouts" label="超时配置 JSON"><Input.TextArea rows={2} placeholder='{"connect":15,"read":120,"total":1800}' /></Form.Item>
          <Form.Item name="limits" label="限额配置 JSON"><Input.TextArea rows={2} placeholder='{"concurrency":4,"rps":2}' /></Form.Item>
          <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="替换 API Key" open={keyModalOpen} onOk={handleSaveKey} onCancel={() => setKeyModalOpen(false)} confirmLoading={submitting}>
        <Form form={keyForm} layout="vertical">
          <Form.Item name="new_api_key" label="新 API Key" rules={[{ required: true, min: 1 }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="连接测试结果" open={testModalOpen} onCancel={() => setTestModalOpen(false)} footer={null}>
        {testResult ? (
          <pre style={{ background: '#f5f5f5', padding: 16, borderRadius: 8, overflow: 'auto' }}>
            {JSON.stringify(testResult, null, 2)}
          </pre>
        ) : (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <SyncOutlined spin style={{ fontSize: 24 }} />
            <p>测试中...</p>
          </div>
        )}
      </Modal>
    </div>
  );
}
