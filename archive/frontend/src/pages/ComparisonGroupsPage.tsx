import { useCallback, useEffect, useRef, useState } from 'react';
import { App, Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag } from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined, TeamOutlined } from '@ant-design/icons';
import { comparisonGroupsApi, fetchAllPages, modelDeploymentsApi, tokenizersApi } from '../api';

interface Group {
  id: string;
  name: string;
  default_tokenizer_id?: string | null;
  remark?: string | null;
  member_count: number;
  created_at: string;
}

interface Tokenizer { id: string; name: string; status: string; }
interface Deployment { id: string; model_id: string; display_name?: string | null; status: string; comparison_group_id?: string | null; }

export default function ComparisonGroupsPage() {
  const { message } = App.useApp();
  const [groups, setGroups] = useState<Group[]>([]);
  const [tokenizers, setTokenizers] = useState<Tokenizer[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [members, setMembers] = useState<Deployment[]>([]);
  const [selectedGroup, setSelectedGroup] = useState<Group | null>(null);
  const [open, setOpen] = useState(false);
  const [memberOpen, setMemberOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [memberLoading, setMemberLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [tokenizerKeyword, setTokenizerKeyword] = useState('');
  const [deploymentKeyword, setDeploymentKeyword] = useState('');
  const [form] = Form.useForm();
  const [memberForm] = Form.useForm();
  const resourceSearchTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [groupResponse, tokenizerResponse, deploymentResponse] = await Promise.all([
        comparisonGroupsApi.list({ skip: (page - 1) * pageSize, limit: pageSize, keyword: keyword.trim() || undefined }),
        tokenizersApi.list({ skip: 0, limit: 100, status: 'ready' }),
        modelDeploymentsApi.list({ skip: 0, limit: 100, status: 'active' }),
      ]);
      setGroups(groupResponse.data.items || []);
      setTotal(groupResponse.data.total || 0);
      setTokenizers(tokenizerResponse.data.items || []);
      setDeployments(deploymentResponse.data.items || []);
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '对比组资源读取失败');
    } finally { setLoading(false); }
  }, [keyword, message, page, pageSize]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => () => {
    resourceSearchTimers.current.forEach(timer => clearTimeout(timer));
    resourceSearchTimers.current.clear();
  }, []);

  const mergeById = <T extends { id: string }>(current: T[], incoming: T[]) => {
    const byId = new Map(current.map(item => [item.id, item]));
    incoming.forEach(item => byId.set(item.id, item));
    return [...byId.values()];
  };

  const queueResourceSearch = (key: string, value: string, search: () => Promise<void>) => {
    const previous = resourceSearchTimers.current.get(key);
    if (previous) clearTimeout(previous);
    if (!value.trim()) return;
    const timer = setTimeout(() => {
      void search().catch((error: any) => message.error(error.response?.data?.error?.message || '资源搜索失败'));
    }, 250);
    resourceSearchTimers.current.set(key, timer);
  };

  const searchTokenizers = (value: string) => {
    setTokenizerKeyword(value);
    queueResourceSearch('tokenizers', value, async () => {
      const response = await tokenizersApi.list({ skip: 0, limit: 100, status: 'ready', keyword: value.trim() });
      setTokenizers(current => mergeById(current, response.data.items || []));
    });
  };

  const searchDeployments = (value: string) => {
    setDeploymentKeyword(value);
    queueResourceSearch('deployments', value, async () => {
      const response = await modelDeploymentsApi.list({ skip: 0, limit: 100, status: 'active', keyword: value.trim() });
      setDeployments(current => mergeById(current, response.data.items || []));
    });
  };

  const openCreate = () => {
    setSelectedGroup(null);
    form.resetFields();
    setOpen(true);
  };

  const openEdit = async (group: Group) => {
    setSelectedGroup(group);
    if (group.default_tokenizer_id && !tokenizers.some(item => item.id === group.default_tokenizer_id)) {
      try {
        const response = await tokenizersApi.get(group.default_tokenizer_id);
        setTokenizers(current => mergeById(current, [response.data]));
      } catch (error: any) {
        message.warning(error.response?.data?.error?.message || '默认 Tokenizer 读取失败');
      }
    }
    form.setFieldsValue({
      name: group.name,
      default_tokenizer_id: group.default_tokenizer_id || undefined,
      remark: group.remark || undefined,
    });
    setOpen(true);
  };

  const saveGroup = async () => {
    if (submitting) return;
    try {
      setSubmitting(true);
      const values = await form.validateFields();
      if (selectedGroup) {
        await comparisonGroupsApi.update(selectedGroup.id, values);
      } else {
        await comparisonGroupsApi.create(values);
      }
      message.success(selectedGroup ? '对比组已更新' : '创建成功');
      setOpen(false);
      await load();
    } catch (error: any) {
      if (!error?.errorFields) message.error(error.response?.data?.error?.message || '对比组保存失败');
    } finally { setSubmitting(false); }
  };

  const openMembers = async (group: Group) => {
    setSelectedGroup(group);
    setMemberOpen(true);
    setMemberLoading(true);
    memberForm.resetFields();
    try {
      const items = await fetchAllPages<Deployment>(params => comparisonGroupsApi.listMembers(group.id, params));
      setMembers(items);
    } catch (error: any) {
      setMembers([]);
      message.error(error.response?.data?.error?.message || '对比组成员读取失败');
    } finally { setMemberLoading(false); }
  };

  const addMember = async () => {
    if (!selectedGroup || submitting) return;
    try {
      setSubmitting(true);
      const values = await memberForm.validateFields();
      await comparisonGroupsApi.addMember(selectedGroup.id, values.model_deployment_id);
      memberForm.resetFields();
      await openMembers(selectedGroup);
      await load();
      message.success('成员已添加');
    } catch (error: any) {
      if (!error?.errorFields) message.error(error.response?.data?.error?.message || '成员添加失败');
    } finally { setSubmitting(false); }
  };

  const removeMember = async (deploymentId: string) => {
    if (!selectedGroup) return;
    try {
      setMemberLoading(true);
      await comparisonGroupsApi.removeMember(selectedGroup.id, deploymentId);
      await openMembers(selectedGroup);
      await load();
      message.success('成员已移除');
    } catch (error: any) {
      message.error(error.response?.data?.error?.message || '成员移除失败');
    } finally { setMemberLoading(false); }
  };

  const memberIds = new Set(members.map(member => member.id));
  const availableDeployments = deployments.filter(item => !memberIds.has(item.id));

  return <div>
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
      <h3 style={{ margin: 0 }}>模型对比组</h3>
      <Space wrap>
        <Input.Search allowClear placeholder="搜索对比组" onSearch={value => { setKeyword(value); setPage(1); }} onChange={event => { if (!event.target.value) { setKeyword(''); setPage(1); } }} />
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建对比组</Button>
      </Space>
    </div>
    <Table
      rowKey="id"
      loading={loading}
      dataSource={groups}
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
        { title: '默认 Tokenizer', dataIndex: 'default_tokenizer_id', render: (value: string | null) => {
          const tokenizer = tokenizers.find(item => item.id === value);
          return tokenizer ? tokenizer.name : value ? <Tag>{value}</Tag> : '-';
        }},
        { title: '成员数', dataIndex: 'member_count' },
        { title: '创建时间', dataIndex: 'created_at', render: (value: string) => new Date(value).toLocaleString('zh-CN') },
        { title: '操作', render: (_: unknown, record: Group) => <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => void openEdit(record)}>编辑</Button>
          <Button size="small" icon={<TeamOutlined />} onClick={() => void openMembers(record)}>成员</Button>
        </Space> },
      ]}
    />

    <Modal title={selectedGroup ? '编辑对比组' : '新建对比组'} open={open} onOk={() => void saveGroup()} onCancel={() => setOpen(false)} confirmLoading={submitting} destroyOnHidden>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}><Input /></Form.Item>
        <Form.Item name="default_tokenizer_id" label="默认 Tokenizer">
          <Select allowClear showSearch optionFilterProp="label" searchValue={tokenizerKeyword} onSearch={searchTokenizers} onSelect={() => setTokenizerKeyword('')} onClear={() => setTokenizerKeyword('')} options={tokenizers.map(item => ({ value: item.id, label: `${item.name} (${item.status})` }))} placeholder="可选，作为对比组默认口径" />
        </Form.Item>
        <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
      </Form>
    </Modal>

    <Modal title={selectedGroup ? `${selectedGroup.name} · 成员` : '成员'} open={memberOpen} onCancel={() => setMemberOpen(false)} footer={null} destroyOnHidden>
      <Form form={memberForm} layout="inline" onFinish={() => void addMember()} style={{ marginBottom: 16 }}>
        <Form.Item name="model_deployment_id" rules={[{ required: true, message: '请选择模型' }]}>
          <Select showSearch optionFilterProp="label" searchValue={deploymentKeyword} onSearch={searchDeployments} onSelect={() => setDeploymentKeyword('')} onClear={() => setDeploymentKeyword('')} style={{ minWidth: 320 }} options={availableDeployments.map(item => ({ value: item.id, label: `${item.display_name || item.model_id} (${item.model_id})` }))} placeholder="选择模型部署" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={submitting}>添加</Button>
      </Form>
      <Table
        size="small"
        rowKey="id"
        loading={memberLoading}
        dataSource={members}
        pagination={false}
        columns={[
          { title: '模型', render: (_: unknown, item: Deployment) => item.display_name || item.model_id },
          { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
          { title: '操作', render: (_: unknown, item: Deployment) => <Popconfirm title="确认移除成员？" onConfirm={() => void removeMember(item.id)}><Button size="small" danger icon={<DeleteOutlined />}>移除</Button></Popconfirm> },
        ]}
      />
    </Modal>
  </div>;
}
