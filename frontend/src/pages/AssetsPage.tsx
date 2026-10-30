import { useEffect, useState } from 'react';
import { isAxiosError } from 'axios';
import { Alert, Button, Card, Col, Collapse, Form, Input, InputNumber, Modal, Row, Select, Space, Steps, Switch, Tag, Typography } from 'antd';
import { runAssetRequest, type AssetRunRequest, type AssetRunResponse, type AppConfig } from '../api/client';

const { Text, Title, Paragraph } = Typography;
const operations = [
  ['ListAssetGroups', '查询素材组列表'], ['CreateAssetGroup', '创建虚拟人像素材组'],
  ['GetAssetGroup', '查询素材组详情'], ['UpdateAssetGroup', '更新素材组'], ['DeleteAssetGroup', '删除素材组'],
  ['ListAssets', '查询素材列表'], ['CreateAsset', '上传素材'], ['GetAsset', '查询素材状态'],
  ['UpdateAsset', '更新素材'], ['DeleteAsset', '删除素材'],
  ['CreateVisualValidateSession', '真人：生成 H5 认证链接'], ['GetVisualValidateResult', '真人：查询认证素材组'],
];
interface Values {
  endpoint: string; auth: 'aksk' | 'bearer'; region: string; project: string;
  access_key?: string; secret_key?: string; security_token?: string; api_key?: string; byted_token?: string;
  id?: string; group_id?: string; name?: string; description?: string; url?: string;
  asset_type?: string; callback_url?: string; advanced?: string;
  wait?: boolean; timeout: number; max_wait: number; poll_interval: number;
}
const required = [{ required: true, message: '请填写此项' }];
const rootRule = { validator: (_: unknown, value: string) => {
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.pathname !== '/' || url.search || url.hash || url.username || url.password) throw new Error();
    return Promise.resolve();
  } catch { return Promise.reject(new Error('请填写 HTTP(S) 根地址，不含路径前缀、参数或账号密码')); }
} };

export function AssetsPage({ config, groupType, onNavigateToResult }: {
  config?: AppConfig | null;
  groupType: 'AIGC' | 'LivenessFace';
  onNavigateToResult: (category: string, folder: string) => void;
}) {
  const isReal = groupType === 'LivenessFace';
  const availableOperations = operations.filter(([value]) => isReal
    ? value !== 'CreateAssetGroup'
    : !['CreateVisualValidateSession', 'GetVisualValidateResult'].includes(value));
  const [form] = Form.useForm<Values>();
  const [selectedProfileId, setSelectedProfileId] = useState('');
  const useSaved = !!selectedProfileId && selectedProfileId !== 'custom';
  useEffect(() => {
    const groups = config?.asset_profiles || [];
    if (!selectedProfileId && config) {
      setSelectedProfileId(groups.find(p => p.configured)?.id || 'custom');
      return;
    }
    if (selectedProfileId && selectedProfileId !== 'custom') {
      const selected = groups.find(p => p.id === selectedProfileId);
      if (selected) form.setFieldsValue({ endpoint: selected.endpoint, auth: 'aksk' });
      else if (config) setSelectedProfileId('custom');
    }
  }, [config, selectedProfileId, form]);
  const [action, setAction] = useState('ListAssetGroups');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<AssetRunResponse | null>(null);
  const [session, setSession] = useState<AssetRunResponse['validation_session']>();
  const auth = Form.useWatch('auth', form) ?? 'aksk';
  const isSession = action === 'CreateVisualValidateSession';
  const isValidation = action === 'GetVisualValidateResult';
  const isCreate = action === 'CreateAsset';
  const isList = action === 'ListAssetGroups' || action === 'ListAssets';
  const isDelete = action.startsWith('Delete');
  const needsId = /^(Get|Update|Delete)Asset/.test(action);
  const canWait = isCreate || action === 'GetAsset';
  const hasName = isCreate || action === 'CreateAssetGroup' || action.startsWith('Update');
  const switchAction = (value: string) => {
    if (!availableOperations.some(([key]) => key === value)) return;
    setAction(value); setError('');
  };

  async function send(payload: AssetRunRequest) {
    setBusy(true); setError(''); setResult(null);
    if (payload.action === 'CreateVisualValidateSession') {
      setSession(undefined); form.setFieldValue('byted_token', '');
    }
    try {
      const response = await runAssetRequest(payload);
      // 会话只保留于页面内存，不混入通用 JSON 展示或浏览器持久存储。
      setResult({ ...response, validation_session: undefined });
      if (response.validation_session) {
        setSession(response.validation_session);
        form.setFieldValue('byted_token', response.validation_session.byted_token);
      }
      if (response.report.group_id) form.setFieldValue('group_id', response.report.group_id);
    } catch (exc) {
      const detail = isAxiosError(exc) ? exc.response?.data?.detail : undefined;
      setError(typeof detail === 'string' ? detail : '请求未完成，请检查输入、后端连接及超时设置。写操作可能已生效，请先查询再重试。');
    } finally { setBusy(false); }
  }

  function submit(submitted: Values) {
    // 折叠区尚未展开时字段未挂载，仍需读取初始超时值与保留的会话值。
    const values: Values = { ...form.getFieldsValue(true), ...submitted };
    let extra: Record<string, unknown> = {};
    try {
      extra = JSON.parse(values.advanced || '{}');
      if (!extra || typeof extra !== 'object' || Array.isArray(extra)) throw new Error();
    } catch { setError('高级请求参数必须是 JSON 对象'); return; }
    if (Object.keys(extra).some(key => /token|secret|key|authorization/i.test(key))) {
      setError('凭证请填写在专用密码输入框，不要放入高级请求参数'); return;
    }
    const payload: Record<string, unknown> = { ...extra, ProjectName: values.project };
    if (isList) {
      if (extra.Filter !== undefined && (!extra.Filter || typeof extra.Filter !== 'object' || Array.isArray(extra.Filter))) {
        setError('Filter 必须是 JSON 对象'); return;
      }
      payload.Filter = { ...(extra.Filter as Record<string, unknown> | undefined), GroupType: groupType };
    }
    if (action === 'CreateAssetGroup') payload.GroupType = 'AIGC';
    if (needsId) payload.Id = values.id;
    if (hasName && values.name) payload.Name = values.name;
    if ((action === 'CreateAssetGroup' || action === 'UpdateAssetGroup') && values.description !== undefined) payload.Description = values.description;
    if (isCreate) Object.assign(payload, { GroupId: values.group_id, URL: values.url, AssetType: values.asset_type });
    if (isSession) payload.CallbackURL = values.callback_url;
    const request: AssetRunRequest = {
      use_saved_credentials: useSaved,
      asset_profile_id: useSaved ? selectedProfileId : undefined,
      endpoint: values.endpoint, auth: useSaved ? 'aksk' : values.auth, region: values.region,
      access_key: !useSaved && values.auth === 'aksk' ? values.access_key : undefined,
      secret_key: !useSaved && values.auth === 'aksk' ? values.secret_key : undefined,
      security_token: values.auth === 'aksk' ? values.security_token : undefined,
      api_key: values.auth === 'bearer' ? values.api_key : undefined,
      byted_token: isValidation ? values.byted_token : undefined,
      action, payload, wait: canWait && !!values.wait,
      timeout: values.timeout, max_wait: values.max_wait, poll_interval: values.poll_interval,
    };
    if (isDelete) {
      Modal.confirm({ title: '确认删除远端资源？', content: `将执行 ${action}，资源 Id：${values.id}。删除后无法通过本工具恢复。`,
        okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true },
        onOk: () => send({ ...request, confirm_delete: true }) });
    } else void send(request);
  }

  return <Space orientation="vertical" size={20} style={{ width: '100%' }}>
    <div><Space wrap><Title level={3} style={{ margin: 0 }}>{isReal ? '真人人像素材测试' : '虚拟人像素材测试'}</Title><Tag>{groupType}</Tag></Space>
      <Paragraph type="secondary" style={{ margin: '8px 0 0' }}>{isReal
        ? '完成本人认证，获取专属素材组，再上传同人素材并检查入库状态。'
        : '创建虚拟人像素材组，上传图片、视频或音频，检查入库状态后用于视频生成。'}</Paragraph></div>
    <Row gutter={[20, 20]}>
      <Col xs={24} xl={13}>
        <Form form={form} layout="vertical" onFinish={submit} disabled={busy}
          initialValues={{ endpoint: 'https://token.peopleopen.cn/', auth: 'aksk', region: 'cn-beijing', project: 'default', asset_type: 'Image', wait: true, timeout: 60, max_wait: 300, poll_interval: 3 }}
          onValuesChange={(changed) => {
            if (['endpoint', 'auth', 'project', 'access_key', 'secret_key', 'api_key'].some(key => key in changed)) {
              setSession(undefined); form.setFieldValue('byted_token', '');
            }
          }}>
          <Card title="连接与鉴权" style={{ marginBottom: 20 }}>
            <Form.Item label="素材库连接配置组">
              <Select value={selectedProfileId || 'custom'} onChange={(value) => {
                setSelectedProfileId(value); setSession(undefined); setResult(null); setError('');
                form.setFieldsValue({ byted_token: '', id: '', group_id: '', access_key: '', secret_key: '', security_token: '', api_key: '', advanced: '', project: 'default' });
              }} options={[
                ...(config?.asset_profiles || []).map(p => ({ value: p.id!, label: `${p.name} | ${p.endpoint}${p.configured ? '' : '（未配齐 AK/SK）'}`, disabled: !p.configured })),
                { value: 'custom', label: '临时自定义连接（不保存）' },
              ]} />
            </Form.Item>
            <Form.Item name="endpoint" label="Endpoint 根地址" rules={[...required, rootRule]} extra="仅请求根路径，不自动追加 /api/v3、/v1 或 /business-api。">
              <Input disabled={useSaved} placeholder="https://你的域名" autoComplete="off" /></Form.Item>
            <Row gutter={16}><Col span={12}><Form.Item name="auth" label="鉴权方式"><Select disabled={useSaved} options={[{ value: 'aksk', label: 'AK/SK（官方）' }, { value: 'bearer', label: 'Bearer（兼容网关）' }]} /></Form.Item></Col>
              <Col span={12}><Form.Item name="region" label="Region" rules={required}><Input /></Form.Item></Col></Row>
            {useSaved ? <Alert type="info" showIcon message="使用已保存的 AK/SK，由服务端从 1Password 读取，无需重新填写。" style={{ marginBottom: 16 }} /> : auth === 'aksk' ? <>
              <Form.Item name="access_key" label="Access Key" rules={required}><Input.Password autoComplete="off" /></Form.Item>
              <Form.Item name="secret_key" label="Secret Key" rules={required}><Input.Password autoComplete="new-password" /></Form.Item>
              <Form.Item name="security_token" label="STS Token（可选）"><Input.Password autoComplete="off" /></Form.Item>
            </> : <Form.Item name="api_key" label="API Key" rules={required}><Input.Password autoComplete="off" /></Form.Item>}
            <Form.Item name="project" label="ProjectName" rules={required} extra="认证、素材组、素材和视频推理应使用同一项目。"><Input /></Form.Item>
            <Text type="secondary">临时填写的凭证不保存；如需复用，请在配置管理中保存素材库根地址及 AK/SK。</Text>
          </Card>
          <Card title="素材库操作">
            <Form.Item label="选择操作"><Select value={action} onChange={switchAction} options={availableOperations.map(([value, label]) => ({ value, label: `${label} · ${value}` }))} /></Form.Item>
            {isList && <Paragraph type="secondary">当前仅查询{isReal ? '真人人像' : '虚拟人像'}素材。其他类型请从左侧导航进入。</Paragraph>}
            {isSession && <Alert style={{ marginBottom: 16 }} type="info" showIcon title="由本人打开 H5 页面完成认证；通过后查询 GroupId，再上传同人素材。" />}
            {needsId && <Form.Item name="id" label={action.endsWith('Group') ? '素材组 Id' : '素材 Id'} rules={required}><Input /></Form.Item>}
            {hasName && <Form.Item name="name" label="名称" rules={action === 'CreateAssetGroup' ? required : []}><Input maxLength={64} /></Form.Item>}
            {(action === 'CreateAssetGroup' || action === 'UpdateAssetGroup') && <Form.Item name="description" label="描述"><Input.TextArea rows={2} /></Form.Item>}
            {isCreate && <>
              <Form.Item name="group_id" label="所属素材组 GroupId" rules={required}><Input placeholder="group-…" /></Form.Item>
              <Form.Item name="asset_type" label="素材类型"><Select options={['Image', 'Video', 'Audio'].map(value => ({ value, label: value }))} /></Form.Item>
              <Form.Item name="url" label="素材公网 URL" rules={required}><Input placeholder="https://…" /></Form.Item>
            </>}
            {isSession && <Form.Item name="callback_url" label="认证完成跳转地址 CallbackURL" rules={required}><Input placeholder="https://你的域名/callback" /></Form.Item>}
            {isValidation && <Form.Item name="byted_token" label="BytedToken" rules={required} extra="本页生成后自动填入，也可粘贴已有凭证；有效期 30 分钟。"><Input.Password autoComplete="off" /></Form.Item>}
            {canWait && <Form.Item name="wait" label="等待素材入库完成" valuePropName="checked"><Switch /></Form.Item>}
            <Collapse style={{ marginBottom: 20 }} items={[{ key: 'advanced', label: '高级参数与超时', children: <>
              <Form.Item name="advanced" label="附加 JSON 参数（筛选、分页等）" extra="操作字段和 ProjectName 以表单为准，不填写凭证。"><Input.TextArea rows={4} placeholder={'{"PageNumber": 1, "PageSize": 10}'} /></Form.Item>
              <Space wrap><Form.Item name="timeout" label="单次超时（秒）"><InputNumber min={1} max={120} /></Form.Item>
                <Form.Item name="max_wait" label="最长轮询（秒）"><InputNumber min={1} max={600} /></Form.Item>
                <Form.Item name="poll_interval" label="轮询间隔（秒）"><InputNumber min={1} max={60} /></Form.Item></Space>
            </> }]} />
            <Button type="primary" danger={isDelete} htmlType="submit" loading={busy} block>{isDelete ? '删除指定资源' : busy ? '执行中，请等待…' : '执行测试'}</Button>
          </Card>
        </Form>
      </Col>
      <Col xs={24} xl={11}>
        <Space orientation="vertical" size={20} style={{ width: '100%' }}>
          {isReal ? <Card title="真人人像流程">
            <Steps size="small" current={result?.report.group_id ? 2 : session ? 1 : 0} items={[{ title: '生成认证链接' }, { title: '本人完成认证' }, { title: '查询素材组' }]} />
            <Space wrap style={{ marginTop: 20 }}><Button disabled={busy} onClick={() => switchAction('CreateVisualValidateSession')}>1. 生成链接</Button>
              <Button disabled={busy} onClick={() => switchAction('GetVisualValidateResult')}>2. 查询认证结果</Button>
              <Button disabled={busy} onClick={() => switchAction('CreateAsset')}>3. 上传素材</Button></Space>
            {session && <Alert style={{ marginTop: 20 }} type="info" showIcon title="认证链接已生成" description={<>
              <Paragraph>请本人打开链接完成授权和活体认证。回调 resultCode=10000 后，点击“查询认证结果”。</Paragraph>
              <a href={session.h5_link} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">打开 H5 真人认证页面 ↗</a>
              <Paragraph style={{ marginTop: 12 }}>会话仅保留于当前页面，刷新或离开后清除。</Paragraph>
              <Button size="small" onClick={() => { setSession(undefined); form.setFieldValue('byted_token', ''); }}>清除认证会话</Button>
            </>} />}
          </Card> : <Card title="虚拟人像流程">
            <Steps size="small" items={[{ title: '创建素材组' }, { title: '上传素材' }, { title: '查询入库状态' }]} />
            <Paragraph type="secondary" style={{ marginTop: 20 }}>虚拟人像无需真人认证。先创建 AIGC 素材组，将返回的 GroupId 用于上传，入库状态为 Active 后即可引用。</Paragraph>
            <Space wrap>
              <Button disabled={busy} onClick={() => switchAction('CreateAssetGroup')}>1. 创建素材组</Button>
              <Button disabled={busy} onClick={() => switchAction('CreateAsset')}>2. 上传素材</Button>
              <Button disabled={busy} onClick={() => switchAction('GetAsset')}>3. 查询状态</Button>
            </Space>
          </Card>}
          {error && <Alert type="error" showIcon title={error} />}
          {busy && <Alert type="info" showIcon title="正在请求素材库" description="上传并等待入库可能耗时数分钟。请保持当前页面，避免重复提交。" />}
          {result && <Card title={<Space>测试结果<Tag color={result.report.success ? 'success' : 'error'}>{result.report.success ? '成功' : '失败'}</Tag></Space>}
            extra={<Button onClick={() => onNavigateToResult(result.category, result.folder)}>查看历史报告</Button>}>
            {result.report.error && <Alert type="error" title={result.report.error} style={{ marginBottom: 16 }} />}
            {result.report.group_id && <Paragraph copyable>GroupId：{result.report.group_id}</Paragraph>}
            {typeof result.report.result?.Id === 'string' && <Space wrap style={{ marginBottom: 16 }}>
              <Text copyable>{result.report.result.Id}</Text>
              <Button size="small" onClick={() => { const id = String(result.report.result?.Id); form.setFieldValue('id', id); switchAction(id.startsWith('group-') ? 'GetAssetGroup' : 'GetAsset'); }}>填入查询</Button>
              {result.report.action.endsWith('AssetGroup') && <Button size="small" onClick={() => { form.setFieldValue('group_id', String(result.report.result?.Id)); switchAction('CreateAsset'); }}>作为 GroupId 上传</Button>}
            </Space>}
            {result.report.asset_uri && <Paragraph copyable>{result.report.asset_uri}</Paragraph>}
            <Text type="secondary">耗时 {(result.report.elapsed_ms / 1000).toFixed(2)} 秒 · 结果已脱敏保存</Text>
            <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 560, overflow: 'auto', background: '#f5f7fa', padding: 16, borderRadius: 8 }}>{JSON.stringify(result.report, null, 2)}</pre>
          </Card>}
        </Space>
      </Col>
    </Row>
  </Space>;
}
