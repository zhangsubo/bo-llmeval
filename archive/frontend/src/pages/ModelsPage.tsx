import { useCallback, useEffect, useState } from 'react';
import { App, Button, Form, Input, InputNumber, Modal, Select, Space, Table, Tag } from 'antd';
import { DollarOutlined, EditOutlined, PlusOutlined, SyncOutlined } from '@ant-design/icons';
import { connectionsApi, fetchAllPages, modelDeploymentsApi, pricesApi, suppliersApi } from '../api';

interface Deployment {
  id: string; connection_id: string; model_id: string; display_name: string | null;
  source: string; status: string; context_window: number | null; created_at: string;
}
interface ConnectionOption { id: string; supplier_id?: string; name: string; supplier_name?: string; status: string; }
interface Price { id: string; price_version: number; input_token_price: string; output_token_price: string; cached_token_price?: string | null; currency: string; exchange_rate: string; unit: string; effective_from?: string | null; created_at: string; }

export default function ModelsPage() {
  const { message } = App.useApp();
  const [data, setData] = useState<Deployment[]>([]);
  const [connections, setConnections] = useState<ConnectionOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [connectionFilter, setConnectionFilter] = useState<string>();
  const [statusFilter, setStatusFilter] = useState<string>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [resourceLoading, setResourceLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [priceModalOpen, setPriceModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [priceLoading, setPriceLoading] = useState(false);
  const [prices, setPrices] = useState<Price[]>([]);
  const [selectedDeployment, setSelectedDeployment] = useState<Deployment | null>(null);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [priceForm] = Form.useForm();

  const load = useCallback(() => {
    setLoading(true);
    modelDeploymentsApi.list({
      skip: (page - 1) * pageSize,
      limit: pageSize,
      keyword: keyword.trim() || undefined,
      connection_id: connectionFilter,
      status: statusFilter,
    })
      .then(r => { setData(r.data.items || []); setTotal(r.data.total || 0); })
      .catch((error: any) => message.error(error.response?.data?.error?.message || '模型列表读取失败'))
      .finally(() => setLoading(false));
  }, [connectionFilter, keyword, message, page, pageSize, statusFilter]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    let disposed = false;
    setResourceLoading(true);
    Promise.all([
      fetchAllPages<{ id: string; name: string }>(params => suppliersApi.list({ ...params, status: 'active' })),
      fetchAllPages<ConnectionOption>(params => connectionsApi.listAll({ ...params, status: 'active' })),
    ])
      .then(([supplierItems, connectionItems]) => {
        const supplierNames = new Map(supplierItems.map(supplier => [supplier.id, supplier.name]));
        const nextConnections = connectionItems.map((item: ConnectionOption) => ({
          ...item,
          supplier_name: item.supplier_id ? supplierNames.get(item.supplier_id) : undefined,
        }));
        if (!disposed) setConnections(nextConnections);
      })
      .catch((error: any) => { if (!disposed) message.error(error.response?.data?.error?.message || '连接列表读取失败'); })
      .finally(() => { if (!disposed) setResourceLoading(false); });
    return () => { disposed = true; };
  }, [message]);

  const handleCreate = async () => {
    if (submitting) return;
    try {
      setSubmitting(true);
      const values = await form.validateFields();
      await modelDeploymentsApi.createModel(values.connection_id, { model_id: values.model_id, display_name: values.display_name });
      message.success('创建成功');
      setModalOpen(false);
      form.resetFields();
      load();
    } catch (error: any) {
      if (!error?.errorFields) message.error(error.response?.data?.error?.message || '模型创建失败');
    }
    finally { setSubmitting(false); }
  };

  const openEdit = (deployment: Deployment) => {
    setSelectedDeployment(deployment);
    editForm.setFieldsValue({
      display_name: deployment.display_name || undefined,
      context_window: deployment.context_window || undefined,
      status: deployment.status,
    });
    setEditModalOpen(true);
  };

  const handleUpdate = async () => {
    if (!selectedDeployment || submitting) return;
    try {
      setSubmitting(true);
      await modelDeploymentsApi.update(selectedDeployment.id, await editForm.validateFields());
      message.success('模型部署已更新');
      setEditModalOpen(false);
      await load();
    } catch (error: any) {
      if (!error?.errorFields) message.error(error.response?.data?.error?.message || '模型部署更新失败');
    } finally { setSubmitting(false); }
  };

  const loadPrices = async (deployment: Deployment) => {
    setSelectedDeployment(deployment);
    setPriceModalOpen(true);
    setPriceLoading(true);
    try {
      setPrices(await fetchAllPages<Price>(params => pricesApi.list(deployment.id, params)));
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '价格列表读取失败');
      setPrices([]);
    } finally { setPriceLoading(false); }
  };

  const handleCreatePrice = async () => {
    if (!selectedDeployment || submitting) return;
    try {
      setSubmitting(true);
      const values = await priceForm.validateFields();
      await pricesApi.create(selectedDeployment.id, values);
      message.success('价格版本已追加');
      priceForm.resetFields();
      await loadPrices(selectedDeployment);
    } catch (error: any) {
      if (!error?.errorFields) message.error(error.response?.data?.error?.message || '价格保存失败');
    } finally { setSubmitting(false); }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h3 style={{ margin: 0 }}>模型部署</h3>
        <Space wrap>
          <Input.Search allowClear placeholder="搜索模型 ID 或展示名称" onSearch={value => { setKeyword(value); setPage(1); }} onChange={event => { if (!event.target.value) { setKeyword(''); setPage(1); } }} />
          <Select allowClear showSearch optionFilterProp="label" placeholder="按连接筛选" style={{ minWidth: 200 }} value={connectionFilter} onChange={value => { setConnectionFilter(value); setPage(1); }} options={connections.map(connection => ({ value: connection.id, label: `${connection.supplier_name || '供应商'} · ${connection.name}` }))} />
          <Select allowClear placeholder="状态" style={{ width: 110 }} value={statusFilter} onChange={value => { setStatusFilter(value); setPage(1); }} options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }, { value: 'archived', label: '归档' }]} />
          <Button icon={<SyncOutlined />} onClick={load}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>手工录入</Button>
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
          { title: '模型 ID', dataIndex: 'model_id' },
          { title: '展示名称', dataIndex: 'display_name', render: (v: string) => v || '-' },
          { title: '来源', dataIndex: 'source', render: (v: string) => <Tag color={v === 'synced' ? 'blue' : 'green'}>{v}</Tag> },
          { title: '上下文窗口', dataIndex: 'context_window', render: (v: number) => v ? v.toLocaleString() : '-' },
          { title: '状态', dataIndex: 'status', render: (v: string) => {
            const colorMap: Record<string, string> = { active: 'green', disabled: 'orange', archived: 'default' };
            return <Tag color={colorMap[v]}>{v}</Tag>;
          }},
          { title: '创建时间', dataIndex: 'created_at', render: (v: string) => new Date(v).toLocaleString('zh-CN') },
          { title: '操作', render: (_: unknown, record: Deployment) => <Space>
            <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>编辑</Button>
            <Button size="small" icon={<DollarOutlined />} onClick={() => void loadPrices(record)}>价格</Button>
          </Space> },
        ]}
      />

      <Modal title="手工录入模型" open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)} confirmLoading={submitting}>
        <Form form={form} layout="vertical">
          <Form.Item name="connection_id" label="服务连接" rules={[{ required: true }]}>
            <Select
              showSearch loading={resourceLoading} optionFilterProp="label"
              options={connections.map(connection => ({ value: connection.id, label: `${connection.supplier_name || '供应商'} · ${connection.name}` }))}
              placeholder="选择已启用连接"
            />
          </Form.Item>
          <Form.Item name="model_id" label="Model ID" rules={[{ required: true }]}>
            <Input placeholder="如：gpt-4o" />
          </Form.Item>
          <Form.Item name="display_name" label="展示名称">
            <Input placeholder="如：GPT-4o" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="编辑模型部署" open={editModalOpen} onOk={() => void handleUpdate()} onCancel={() => setEditModalOpen(false)} confirmLoading={submitting} destroyOnHidden>
        <Form form={editForm} layout="vertical">
          <Form.Item name="display_name" label="展示名称"><Input /></Form.Item>
          <Form.Item name="context_window" label="上下文窗口"><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="status" label="状态"><Select options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }, { value: 'archived', label: '归档' }]} /></Form.Item>
        </Form>
      </Modal>

      <Modal
        title={selectedDeployment ? `${selectedDeployment.model_id} 价格版本` : '价格版本'}
        open={priceModalOpen}
        onCancel={() => setPriceModalOpen(false)}
        footer={null}
        width={820}
      >
        <Table
          size="small"
          loading={priceLoading}
          rowKey="id"
          pagination={{ pageSize: 5 }}
          dataSource={prices}
          columns={[
            { title: '版本', dataIndex: 'price_version' },
            { title: '输入 / 1K', dataIndex: 'input_token_price' },
            { title: '输出 / 1K', dataIndex: 'output_token_price' },
            { title: '缓存 / 1K', dataIndex: 'cached_token_price', render: (value: string | null) => value || '-' },
            { title: '币种', dataIndex: 'currency' },
            { title: '汇率', dataIndex: 'exchange_rate' },
            { title: '生效时间', dataIndex: 'effective_from', render: (value: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '-' },
          ]}
        />
        <Form form={priceForm} layout="vertical" style={{ marginTop: 16 }}>
          <Space style={{ width: '100%' }} wrap>
            <Form.Item name="input_token_price" label="输入 Token 单价" rules={[{ required: true, type: 'number', min: 0 }]}><InputNumber min={0} precision={6} /></Form.Item>
            <Form.Item name="output_token_price" label="输出 Token 单价" rules={[{ required: true, type: 'number', min: 0 }]}><InputNumber min={0} precision={6} /></Form.Item>
            <Form.Item name="cached_token_price" label="缓存 Token 单价"><InputNumber min={0} precision={6} /></Form.Item>
            <Form.Item name="exchange_rate" label="汇率" initialValue={1} rules={[{ required: true, type: 'number', min: 0.000001 }]}><InputNumber min={0.000001} precision={6} /></Form.Item>
            <Form.Item name="currency" label="币种" initialValue="CNY"><Input style={{ width: 90 }} /></Form.Item>
          </Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => void handleCreatePrice()} loading={submitting}>追加价格版本</Button>
        </Form>
      </Modal>
    </div>
  );
}
