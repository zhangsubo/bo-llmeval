import React, { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  ApiOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  PlusOutlined,
  RobotOutlined,
  SaveOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  type AnalyzerConfig,
  type AssetConfig,
  type AppConfig,
  type ProfileConfig,
  fetchConfig,
  testAnalyzerConnection,
  updateConfig,
} from '../api/client';

const { Title, Text } = Typography;

interface ConfigPageProps {
  onConfigSaved?: (cfg: AppConfig) => void;
  defaultOpenAnalyzerModal?: boolean;
  onAnalyzerModalClosed?: () => void;
}

export const ConfigPage: React.FC<ConfigPageProps> = ({
  onConfigSaved,
  defaultOpenAnalyzerModal,
  onAnalyzerModalClosed,
}) => {
  const [onepassword, setOnepassword] = useState({ account: '', vault: '' });
  const [configPath, setConfigPath] = useState<string>('');
  const [assetProfiles, setAssetProfiles] = useState<AssetConfig[]>([]);
  const updateAsset = (index: number, patch: Partial<AssetConfig>) => {
    setAssetProfiles((rows) => rows.map((row, i) => i === index ? { ...row, ...patch } : row));
  };
  const [profiles, setProfiles] = useState<ProfileConfig[]>([]);
  const [analyzer, setAnalyzer] = useState<AnalyzerConfig>({
    name: 'AI 结果解读模型',
    base_url: '',
    model: '',
    api_key: '',
    api_key_masked: '',
    has_key: false,
    configured: false,
  });
  const [analyzerModalOpen, setAnalyzerModalOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testingAnalyzer, setTestingAnalyzer] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    msg: string;
  } | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchConfig();
      setConfigPath(data.config_path);
      setOnepassword(data.onepassword || { account: '', vault: '' });
      setAssetProfiles((data.asset_profiles || []).map(p => ({ ...p, access_key: '', secret_key: '' })));
      setProfiles(
        data.profiles.map((p) => ({
          ...p,
          api_key: '',
        }))
      );
      setAnalyzer({
        ...data.analyzer,
        api_key: '',
      });
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '读取配置文件失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    if (defaultOpenAnalyzerModal) {
      setAnalyzerModalOpen(true);
      onAnalyzerModalClosed?.();
    }
  }, [defaultOpenAnalyzerModal]);

  const handleAddProfile = (preset?: 'openai' | 'ark') => {
    const nextIdx = profiles.length + 1;
    if (preset === 'ark') {
      setProfiles([
        ...profiles,
        {
          name: `火山方舟配置 ${nextIdx}`,
          base_url: 'https://ark.cn-beijing.volces.com/api/v3',
          api_key: '',
          has_key: false,
        },
      ]);
    } else {
      setProfiles([
        ...profiles,
        {
          name: `常用配置 ${nextIdx}`,
          base_url: 'https://api.openai.com/v1',
          api_key: '',
          has_key: false,
        },
      ]);
    }
  };

  const handleProfileChange = (index: number, field: keyof ProfileConfig, val: string) => {
    const next = [...profiles];
    next[index] = { ...next[index], [field]: val };
    setProfiles(next);
  };

  const handleRemoveProfile = (index: number) => {
    setProfiles(profiles.filter((_, i) => i !== index));
  };

  const handleSaveAll = async (closeModal = false) => {
    setSaving(true);
    try {
      const updated = await updateConfig({
        profiles,
        analyzer,
        asset_profiles: assetProfiles,
        onepassword,
      });
      setConfigPath(updated.config_path);
      setOnepassword(updated.onepassword || { account: '', vault: '' });
      updated.credential_warnings?.forEach(warning => message.warning(warning));
      setAssetProfiles((updated.asset_profiles || []).map(p => ({ ...p, access_key: '', secret_key: '' })));
      setProfiles(updated.profiles.map((p) => ({ ...p, api_key: '' })));
      setAnalyzer({ ...updated.analyzer, api_key: '' });
      message.success('配置已保存，所有持久化密钥由 1Password 管理');
      onConfigSaved?.(updated);
      if (closeModal) {
        setAnalyzerModalOpen(false);
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '保存配置失败');
    } finally {
      setSaving(false);
    }
  };

  const handleTestAnalyzer = async () => {
    setTestingAnalyzer(true);
    setTestResult(null);
    try {
      const res = await testAnalyzerConnection({
        base_url: analyzer.base_url,
        api_key: analyzer.api_key || undefined,
        model: analyzer.model,
      });
      setTestResult({
        ok: true,
        msg: `连通成功！模型 ${res.model} 响应耗时 ${res.duration_ms} ms，回复：「${res.reply}」`,
      });
    } catch (err: any) {
      setTestResult({
        ok: false,
        msg: err?.response?.data?.detail || '解读模型连通性测试失败',
      });
    } finally {
      setTestingAnalyzer(false);
    }
  };

  const copyFromProfileToAnalyzer = (p: ProfileConfig) => {
    setAnalyzer((prev) => ({
      ...prev,
      base_url: p.base_url,
      name: `${p.name} - 解读模型`,
    }));
    setAnalyzerModalOpen(true);
    message.info('已带入该配置组的 Base URL，请确认解读模型 ID 后保存');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Card loading={loading} style={{ borderRadius: 12 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: 12,
            marginBottom: 16,
          }}
        >
          <div>
            <Space align="center">
              <ApiOutlined style={{ fontSize: 20, color: '#0f172a' }} />
              <Title level={4} style={{ margin: 0 }}>
                被测常用接口配置组（NAME1..N / BASE_URL1..N / API_KEY1..N）
              </Title>
            </Space>
            <div style={{ marginTop: 4 }}>
              <Text type="secondary">
                配置文件路径：<code>{configPath || 'scripts/openai-api-config'}</code>
                （与命令行脚本共享同一文件，权限 0600，API Key 脱敏保护）
              </Text>
            </div>
          </div>
          <Space wrap>
            <Button
              icon={<RobotOutlined />}
              onClick={() => {
                setTestResult(null);
                setAnalyzerModalOpen(true);
              }}
            >
              AI 解读模型设置
              {analyzer.configured ? (
                <Tag
                  color="success"
                  style={{ marginLeft: 6, marginRight: 0 }}
                  icon={<CheckCircleOutlined />}
                >
                  {analyzer.model}
                </Tag>
              ) : (
                <Tag color="warning" style={{ marginLeft: 6, marginRight: 0 }}>
                  待配置
                </Tag>
              )}
            </Button>
            <Button icon={<PlusOutlined />} onClick={() => handleAddProfile('openai')}>
              新增通用配置
            </Button>
            <Button icon={<PlusOutlined />} onClick={() => handleAddProfile('ark')}>
              新增火山方舟 (Seedance) 配置
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saving}
              onClick={() => handleSaveAll(false)}
            >
              保存全部配置
            </Button>
          </Space>
        </div>

        {profiles.length === 0 ? (
          <Alert
            type="info"
            showIcon
            message="当前暂无保存的常用配置组，请点击右上角「新增通用配置」添加。"
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {profiles.map((prof, idx) => (
              <Card
                key={prof.id ?? `new-${idx}`}
                size="small"
                style={{ background: '#fafafa', borderRadius: 8 }}
                title={
                  <Space>
                    <Tag color="geekblue">配置 {idx + 1}</Tag>
                    <Text strong>{prof.name || `未命名配置 ${idx + 1}`}</Text>
                    {prof.has_key ? (
                      <Tag color="green">Key 已配置 ({prof.api_key_masked})</Tag>
                    ) : (
                      <Tag color="orange">需输入新 Key</Tag>
                    )}
                  </Space>
                }
                extra={
                  <Space>
                    <Button
                      size="small"
                      type="link"
                      icon={<SettingOutlined />}
                      onClick={() => copyFromProfileToAnalyzer(prof)}
                    >
                      复用此地址到解读模型
                    </Button>
                    <Popconfirm
                      title="确定删除这组配置吗？删除后需点击「保存全部配置」生效。"
                      onConfirm={() => handleRemoveProfile(idx)}
                    >
                      <Button size="small" danger icon={<DeleteOutlined />}>
                        删除
                      </Button>
                    </Popconfirm>
                  </Space>
                }
              >
                <Row gutter={[12, 12]}>
                  <Col xs={24} md={6}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      配置名称 (NAME{idx + 1})
                    </Text>
                    <Input
                      style={{ marginTop: 4 }}
                      value={prof.name}
                      onChange={(e) => handleProfileChange(idx, 'name', e.target.value)}
                      placeholder="例如：生产网关 / 火山方舟"
                    />
                  </Col>
                  <Col xs={24} md={10}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      Base URL (BASE_URL{idx + 1})
                    </Text>
                    <Input
                      style={{ marginTop: 4 }}
                      value={prof.base_url}
                      onChange={(e) => handleProfileChange(idx, 'base_url', e.target.value)}
                      placeholder="https://example.com/v1 或 https://ark.cn-beijing.volces.com/api/v3"
                    />
                  </Col>
                  <Col xs={24} md={8}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      API Key (API_KEY{idx + 1})
                    </Text>
                    <Input.Password
                      style={{ marginTop: 4 }}
                      value={prof.api_key}
                      onChange={(e) => handleProfileChange(idx, 'api_key', e.target.value)}
                      placeholder={
                        prof.has_key
                          ? `留空保留原 Key (${prof.api_key_masked})，输入则更新`
                          : '请输入该配置的 API Key'
                      }
                    />
                  </Col>
                </Row>
              </Card>
            ))}
          </div>
        )}
      </Card>

      <Card title="1Password 密钥存储" loading={loading}>
        <Alert type="info" showIcon message="常用接口、AI 解读模型与素材库凭证统一保存到 1Password。本地仅保留引用；保存配置时会迁移已有明文或钥匙串凭证。" />
        <Form layout="vertical" style={{ marginTop: 16 }}>
          <Row gutter={16}>
            <Col xs={24} md={12}><Form.Item label="1Password 账号（邮箱或账号 ID）"><Input value={onepassword.account} onChange={e => setOnepassword({ ...onepassword, account: e.target.value })} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item label="保险库（名称或 ID）"><Input value={onepassword.vault} onChange={e => setOnepassword({ ...onepassword, vault: e.target.value })} /></Form.Item></Col>
          </Row>
          <Text type="secondary">请安装 op CLI，并在 1Password 桌面客户端启用 CLI 集成。删除配置只解除引用，保险库中的历史条目保留，便于恢复。</Text>
        </Form>
      </Card>

      <Card title="素材库连接配置组" loading={loading} extra={<Space>
        <Button icon={<PlusOutlined />} onClick={() => setAssetProfiles([...assetProfiles, { name: `素材库配置 ${assetProfiles.length + 1}`, endpoint: 'https://token.peopleopen.cn/' }])}>添加素材库配置</Button>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => handleSaveAll()}>保存配置</Button>
      </Space>}>
        <Text type="secondary">虚拟人像与真人人像可分别选择配置组。AK/SK 保存在 1Password，均留空保留原值。</Text>
        {!assetProfiles.length && <Alert style={{ marginTop: 16 }} type="info" message="尚无素材库配置，点击“添加素材库配置”开始。" />}
        {assetProfiles.map((asset, index) => <Card key={asset.id || `new-${index}`} size="small" style={{ marginTop: 16 }}
          title={<Space>{asset.name || `素材库配置 ${index + 1}`}{asset.has_credentials && <Tag color="success">AK/SK 已保存</Tag>}</Space>}
          extra={<Popconfirm title="移除此配置组？保存后将解除该组凭证引用，1Password 条目保留。" onConfirm={() => setAssetProfiles(assetProfiles.filter((_, i) => i !== index))}>
            <Button danger type="text" icon={<DeleteOutlined />} />
          </Popconfirm>}>
          <Form layout="vertical">
            <Row gutter={16}>
              <Col xs={24} md={8}><Form.Item label="配置名称"><Input value={asset.name} onChange={(e) => updateAsset(index, { name: e.target.value })} placeholder="例如：生产素材库 / 测试素材库" /></Form.Item></Col>
              <Col xs={24} md={16}><Form.Item label="素材库根地址" extra="不附加 /api/v3、/v1 或 /business-api。"><Input value={asset.endpoint} onChange={(e) => updateAsset(index, { endpoint: e.target.value })} placeholder="https://你的素材库域名" /></Form.Item></Col>
              <Col xs={24} md={12}><Form.Item label="Access Key（AK）"><Input.Password value={asset.access_key} autoComplete="off" onChange={(e) => updateAsset(index, { access_key: e.target.value, clear_credentials: false })} placeholder={asset.has_credentials ? '留空保留；更新时同时填写 AK 和 SK' : '请输入 AK'} /></Form.Item></Col>
              <Col xs={24} md={12}><Form.Item label="Secret Key（SK）"><Input.Password value={asset.secret_key} autoComplete="new-password" onChange={(e) => updateAsset(index, { secret_key: e.target.value, clear_credentials: false })} placeholder={asset.has_credentials ? '留空保留；更新时同时填写 AK 和 SK' : '请输入 SK'} /></Form.Item></Col>
            </Row>
            {asset.clear_credentials && <Alert type="warning" message="保存时将解除本组 AK/SK 引用，保留名称及根地址。" />}
            {asset.has_credentials && <Button danger onClick={() => updateAsset(index, { access_key: '', secret_key: '', clear_credentials: true })}>标记清除本组 AK/SK</Button>}
          </Form>
        </Card>)}
      </Card>

      {/* AI 解读专用大模型配置弹窗 */}
      <Modal
        title={
          <Space>
            <RobotOutlined style={{ color: '#1677ff' }} />
            <span>AI 结果解读专用大模型配置（独立于被测接口）</span>
            {analyzer.configured ? (
              <Tag color="success" icon={<CheckCircleOutlined />}>
                已配置就绪
              </Tag>
            ) : (
              <Tag color="warning">待配置</Tag>
            )}
          </Space>
        }
        open={analyzerModalOpen}
        onCancel={() => setAnalyzerModalOpen(false)}
        width={620}
        footer={[
          <Button
            key="test"
            icon={<ThunderboltOutlined />}
            loading={testingAnalyzer}
            onClick={handleTestAnalyzer}
          >
            测试解读模型连通性
          </Button>,
          <Button key="cancel" onClick={() => setAnalyzerModalOpen(false)}>
            取消
          </Button>,
          <Button
            key="save"
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            onClick={() => handleSaveAll(true)}
          >
            保存配置
          </Button>,
        ]}
      >
        <div style={{ marginBottom: 14 }}>
          <Text type="secondary" style={{ fontSize: 13 }}>
            用于对 <code>scripts/test-results</code> 的结果进行深度诊断解读，支持任意 OpenAI 兼容{' '}
            <code>/chat/completions</code> 接口。保存在配置文件的 <code>ANALYZER_*</code> 字段中。
          </Text>
        </div>

        <Form layout="vertical">
          <Form.Item label="配置名称 (ANALYZER_NAME)">
            <Input
              value={analyzer.name}
              onChange={(e) => setAnalyzer({ ...analyzer, name: e.target.value })}
              placeholder="AI 结果解读模型"
            />
          </Form.Item>

          <Form.Item label="Base URL (ANALYZER_BASE_URL)" required>
            <Input
              value={analyzer.base_url}
              onChange={(e) => setAnalyzer({ ...analyzer, base_url: e.target.value })}
              placeholder="https://api.openai.com/v1"
            />
          </Form.Item>

          <Form.Item
            label={
              <Space size={6}>
                <span>API Key (ANALYZER_API_KEY)</span>
                {analyzer.has_key && (
                  <Tag color="blue" style={{ marginRight: 0 }}>
                    已存: {analyzer.api_key_masked}
                  </Tag>
                )}
              </Space>
            }
            required
          >
            <Input.Password
              value={analyzer.api_key}
              onChange={(e) => setAnalyzer({ ...analyzer, api_key: e.target.value })}
              placeholder={
                analyzer.has_key ? '留空则保留现有 API Key，输入新值则覆盖' : '请输入 API Key'
              }
            />
          </Form.Item>

          <Form.Item label="解读模型 ID (ANALYZER_MODEL)" required style={{ marginBottom: 0 }}>
            <Input
              value={analyzer.model}
              onChange={(e) => setAnalyzer({ ...analyzer, model: e.target.value })}
              placeholder="例如 mimo-v2-pro / gpt-4o-mini / deepseek-v3"
            />
          </Form.Item>
        </Form>

        {testResult && (
          <Alert
            style={{ marginTop: 16 }}
            type={testResult.ok ? 'success' : 'error'}
            showIcon
            message={testResult.msg}
            closable
            onClose={() => setTestResult(null)}
          />
        )}
      </Modal>
    </div>
  );
};
