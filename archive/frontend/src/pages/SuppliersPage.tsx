import { useCallback, useEffect, useState } from 'react';
import { App, Button, Form, Input, Modal, Select, Space, Table, Tag } from 'antd';
import { EditOutlined, PlusOutlined } from '@ant-design/icons';
import { suppliersApi } from '../api';

interface Supplier {
  id: string; name: string; short_name: string; type: string; status: string; remark: string | null; created_at: string;
}

export default function SuppliersPage() {
  const { message } = App.useApp();
  const [data, setData] = useState<Supplier[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [selectedSupplier, setSelectedSupplier] = useState<Supplier | null>(null);
  const [keyword, setKeyword] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();

  const load = useCallback(() => {
    setLoading(true);
    return suppliersApi.list({
        skip: (page - 1) * pageSize,
        limit: pageSize,
        keyword: keyword.trim() || undefined,
        status: statusFilter,
      })
      .then(r => { setData(r.data.items || []); setTotal(r.data.total || 0); })
      .catch((error: any) => message.error(error.response?.data?.error?.message || '供应商列表读取失败'))
      .finally(() => setLoading(false));
  }, [keyword, message, page, pageSize, statusFilter]);
  useEffect(() => { void load(); }, [load]);

  const handleCreate = async () => {
    if (submitting) return;
    try {
      setSubmitting(true);
      const values = await form.validateFields();
      await suppliersApi.create(values);
      message.success('创建成功');
      setModalOpen(false);
      form.resetFields();
      load();
    } catch (error: any) {
      if (!error?.errorFields) message.error(error.response?.data?.error?.message || '供应商创建失败');
    }
    finally { setSubmitting(false); }
  };

  const openEdit = (supplier: Supplier) => {
    setSelectedSupplier(supplier);
    setEditModalOpen(true);
    setTimeout(() => {
      editForm.setFieldsValue({
        name: supplier.name,
        short_name: supplier.short_name,
        type: supplier.type,
        status: supplier.status,
        remark: supplier.remark || undefined,
      });
    }, 0);
  };

  const handleUpdate = async () => {
    if (!selectedSupplier || submitting) return;
    try {
      setSubmitting(true);
      await suppliersApi.update(selectedSupplier.id, await editForm.validateFields());
      message.success('供应商已更新');
      setEditModalOpen(false);
      await load();
    } catch (error: any) {
      if (!error?.errorFields) message.error(error.response?.data?.error?.message || '供应商更新失败');
    } finally { setSubmitting(false); }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h3 style={{ margin: 0 }}>供应商管理</h3>
        <Space wrap>
          <Input.Search allowClear placeholder="搜索名称或简称" onSearch={value => { setKeyword(value); setPage(1); }} onChange={event => { if (!event.target.value) { setKeyword(''); setPage(1); } }} />
          <Select allowClear placeholder="状态" style={{ width: 110 }} value={statusFilter} onChange={value => { setStatusFilter(value); setPage(1); }} options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }, { value: 'archived', label: '归档' }]} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>新建供应商</Button>
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
          { title: '名称', dataIndex: 'name' },
          { title: '简称', dataIndex: 'short_name' },
          { title: '类型', dataIndex: 'type', render: (v: string) => <Tag>{v}</Tag> },
          { title: '状态', dataIndex: 'status', render: (v: string) => {
            const colorMap: Record<string, string> = { active: 'green', disabled: 'orange', archived: 'default' };
            return <Tag color={colorMap[v]}>{v}</Tag>;
          }},
          { title: '备注', dataIndex: 'remark', render: (v: string) => v || '-' },
          { title: '创建时间', dataIndex: 'created_at', render: (v: string) => new Date(v).toLocaleString('zh-CN') },
          { title: '操作', render: (_: unknown, record: Supplier) => <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>编辑</Button> },
        ]}
      />

      <Modal title="新建供应商" open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)} confirmLoading={submitting}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：OpenAI" />
          </Form.Item>
          <Form.Item name="short_name" label="简称" rules={[{ required: true }]}>
            <Input placeholder="如：oai" />
          </Form.Item>
          <Form.Item name="type" label="类型" rules={[{ required: true }]}>
            <Select options={[{ value: '原厂' }, { value: '云厂商' }, { value: '其他' }]} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="编辑供应商" open={editModalOpen} onOk={() => void handleUpdate()} onCancel={() => setEditModalOpen(false)} confirmLoading={submitting} destroyOnHidden>
        <Form form={editForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="short_name" label="简称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="type" label="类型" rules={[{ required: true }]}><Select options={[{ value: '原厂' }, { value: '云厂商' }, { value: '其他' }]} /></Form.Item>
          <Form.Item name="status" label="状态"><Select options={[{ value: 'active', label: '启用' }, { value: 'disabled', label: '停用' }, { value: 'archived', label: '归档' }]} /></Form.Item>
          <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
