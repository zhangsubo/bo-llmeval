import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  Popconfirm,
  Row,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  ApiOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  KeyOutlined,
  LinkOutlined,
  PlusOutlined,
  RobotOutlined,
  SaveOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  type AnalyzerConfig,
  type AppConfig,
  type AssetConfig,
  type ProfileConfig,
  fetchConfig,
  testAnalyzerConnection,
  updateConfig,
} from '../api/client';

const { Title, Text } = Typography;

export type ConfigSection = 'general' | 'models' | 'assets';

export interface AnalyzerDraft {
  base_url: string;
  name: string;
}

interface ConfigPageProps {
  section?: ConfigSection;
  onConfigSaved?: (cfg: AppConfig) => void;
  analyzerDraft?: AnalyzerDraft | null;
  onClearAnalyzerDraft?: () => void;
  onJumpToGeneralWithAnalyzer?: (draft: AnalyzerDraft) => void;
}

export const ConfigPage: React.FC<ConfigPageProps> = ({
  section = 'general',
  onConfigSaved,
  analyzerDraft,
  onClearAnalyzerDraft,
  onJumpToGeneralWithAnalyzer,
}) => {
  const [onepassword, setOnepassword] = useState({ account: '', vault: '' });
  const [configPath, setConfigPath] = useState<string>('');
  const [assetProfiles, setAssetProfiles] = useState<AssetConfig[]>([]);
  const [profiles, setProfiles] = useState<ProfileConfig[]>([]);
  const [analyzer, setAnalyzer] = useState<AnalyzerConfig>({
    name: 'AI 结果解读模型',
    base_url: '',
    model: '',
    api_key: '',
    api_key_masked: '',
    has_key: false,
    auto_analyze: false,
    configured: false,
    clear_key: false,
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testingAnalyzer, setTestingAnalyzer] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    msg: string;
  } | null>(null);

  const pendingDraftRef = useRef<AnalyzerDraft | null>(analyzerDraft || null);
  if (analyzerDraft) {
    pendingDraftRef.current = analyzerDraft;
  }

  const updateAsset = (index: number, patch: Partial<AssetConfig>) => {
    setAssetProfiles((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  const applyConfigResponse = (data: AppConfig) => {
    setConfigPath(data.config_path);
    setOnepassword(data.onepassword || { account: '', vault: '' });
    setAssetProfiles(
      (data.asset_profiles || []).map((p) => ({ ...p, access_key: '', secret_key: '' }))
    );
    setProfiles(
      data.profiles.map((p) => ({
        ...p,
        api_key: '',
      }))
    );
    const draft = pendingDraftRef.current;
    setAnalyzer({
      ...data.analyzer,
      base_url: draft ? draft.base_url : data.analyzer.base_url,
      name: draft ? draft.name : data.analyzer.name,
      auto_analyze: Boolean(data.analyzer.auto_analyze),
      api_key: '',
      clear_key: false,
    });
    if (draft) {
      pendingDraftRef.current = null;
      onClearAnalyzerDraft?.();
    }
  };

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchConfig();
      applyConfigResponse(data);
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
    if (analyzerDraft && !loading && configPath) {
      setAnalyzer((prev) => ({
        ...prev,
        base_url: analyzerDraft.base_url,
        name: analyzerDraft.name,
      }));
      pendingDraftRef.current = null;
      onClearAnalyzerDraft?.();
    }
  }, [analyzerDraft]);

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

  const handleSaveSection = async () => {
    setSaving(true);
    try {
      const payload =
        section === 'general'
          ? { onepassword, analyzer }
          : section === 'models'
          ? { profiles }
          : { asset_profiles: assetProfiles };

      const updated = await updateConfig(payload);
      updated.credential_warnings?.forEach((warning) => message.warning(warning));
      applyConfigResponse(updated);
      message.success('配置已保存，所有持久化密钥由 1Password 管理');
      onConfigSaved?.(updated);
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
    const draft: AnalyzerDraft = {
      base_url: p.base_url,
      name: `${p.name} - 解读模型`,
    };
    if (onJumpToGeneralWithAnalyzer) {
      onJumpToGeneralWithAnalyzer(draft);
      message.info('已带入该配置组的 Base URL，请在通用配置中确认解读模型 ID 后保存');
    }
  };

  const configFileHint = (
    <Text type="secondary">
      配置文件路径：<code>{configPath || 'scripts/openai-api-config'}</code>
      （与命令行脚本共享同一文件，权限 0600，持久化密钥统一存为 1Password 引用）
    </Text>
  );

  if (section === 'general') {
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
            }}
          >
            <div>
              <Space align="center">
                <SettingOutlined style={{ fontSize: 20, color: '#0f172a' }} />
                <Title level={4} style={{ margin: 0 }}>
                  通用配置
                </Title>
              </Space>
              <div style={{ marginTop: 4 }}>{configFileHint}</div>
            </div>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saving}
              onClick={handleSaveSection}
            >
              保存通用配置
            </Button>
          </div>
        </Card>

        {/* 1. 1Password 配置 */}
        <Card
          loading={loading}
          style={{ borderRadius: 12 }}
          title={
            <Space>
              <KeyOutlined style={{ color: '#1677ff' }} />
              <span>1Password 密钥存储配置</span>
            </Space>
          }
        >
          <Alert
            type="info"
            showIcon
            message="模型调用配置、AI 解读模型与素材库凭证统一保存到 1Password。本地配置文件仅保留 op:// 引用；保存时会自动迁移已有明文或钥匙串凭证。"
          />
          <Form layout="vertical" style={{ marginTop: 16 }}>
            <Row gutter={16}>
              <Col xs={24} md={12}>
                <Form.Item label="1Password 账号（邮箱或账号 ID，ONEPASSWORD_ACCOUNT）">
                  <Input
                    value={onepassword.account}
                    onChange={(e) => setOnepassword({ ...onepassword, account: e.target.value })}
                    placeholder="例如：my.1password.com 或 user@example.com"
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item label="保险库（名称或 ID，ONEPASSWORD_VAULT）">
                  <Input
                    value={onepassword.vault}
                    onChange={(e) => setOnepassword({ ...onepassword, vault: e.target.value })}
                    placeholder="例如：Private 或 Development"
                  />
                </Form.Item>
              </Col>
            </Row>
            <Text type="secondary">
              请安装 <code>op</code> CLI，并在 1Password 桌面客户端启用 CLI 集成。删除配置只解除本地引用，保险库中的历史条目保留以便恢复。
            </Text>
          </Form>
        </Card>

        {/* 2. AI 解读配置 + 是否自动打开 AI 解读 */}
        <Card
          loading={loading}
          style={{ borderRadius: 12 }}
          title={
            <Space>
              <RobotOutlined style={{ color: '#1677ff' }} />
              <span>AI 解读配置</span>
              {analyzer.configured ? (
                <Tag color="success" icon={<CheckCircleOutlined />}>
                  已就绪 ({analyzer.model})
                </Tag>
              ) : (
                <Tag color="warning">待配置（未配置时使用内置诊断规则）</Tag>
              )}
            </Space>
          }
          extra={
            <Space wrap>
              <Button
                icon={<ThunderboltOutlined />}
                loading={testingAnalyzer}
                onClick={handleTestAnalyzer}
              >
                测试解读模型连通性
              </Button>
              <Button
                type="primary"
                icon={<SaveOutlined />}
                loading={saving}
                onClick={handleSaveSection}
              >
                保存通用配置
              </Button>
            </Space>
          }
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 12,
              padding: '12px 16px',
              background: '#f8fafc',
              border: '1px solid #e2e8f0',
              borderRadius: 8,
            }}
          >
            <div>
              <Text strong>测试完成后默认自动打开 AI 解读（ANALYZER_AUTO）</Text>
              <div style={{ marginTop: 2 }}>
                <Text type="secondary" style={{ fontSize: 13 }}>
                  开启后，文字、视频及图片模型测试工作台将默认勾选「完成后自动 AI 解读」，测试结束自动生成诊断报告（单次测试时仍可临时切换）。
                </Text>
              </div>
            </div>
            <Switch
              checked={Boolean(analyzer.auto_analyze)}
              onChange={(checked) => setAnalyzer({ ...analyzer, auto_analyze: checked })}
              checkedChildren="默认开启"
              unCheckedChildren="默认关闭"
            />
          </div>

          <Divider style={{ margin: '18px 0' }} />

          <div style={{ marginBottom: 14 }}>
            <Text type="secondary" style={{ fontSize: 13 }}>
              独立于被测接口配置，用于对 <code>scripts/test-results</code> 的结果进行深度根因诊断解读，支持任意 OpenAI 兼容{' '}
              <code>/chat/completions</code> 接口（对应配置文件 <code>ANALYZER_*</code> 字段）。
            </Text>
          </div>

          <Form layout="vertical">
            <Row gutter={16}>
              <Col xs={24} md={12}>
                <Form.Item label="配置名称 (ANALYZER_NAME)">
                  <Input
                    value={analyzer.name}
                    onChange={(e) => setAnalyzer({ ...analyzer, name: e.target.value })}
                    placeholder="AI 结果解读模型"
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item label="解读模型 ID (ANALYZER_MODEL)" required>
                  <Input
                    value={analyzer.model}
                    onChange={(e) => setAnalyzer({ ...analyzer, model: e.target.value })}
                    placeholder="例如 mimo-v2-pro / gpt-4o-mini / deepseek-v3"
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item label="Base URL (ANALYZER_BASE_URL)" required>
                  <Input
                    value={analyzer.base_url}
                    onChange={(e) => setAnalyzer({ ...analyzer, base_url: e.target.value })}
                    placeholder="https://api.openai.com/v1"
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item
                  label={
                    <Space size={6}>
                      <span>API Key (ANALYZER_API_KEY)</span>
                      {analyzer.has_key && !analyzer.clear_key && (
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
                    onChange={(e) =>
                      setAnalyzer({ ...analyzer, api_key: e.target.value, clear_key: false })
                    }
                    placeholder={
                      analyzer.has_key && !analyzer.clear_key
                        ? '留空则保留现有 API Key，输入新值则覆盖'
                        : '请输入解读模型 API Key'
                    }
                  />
                </Form.Item>
              </Col>
            </Row>

            {analyzer.clear_key && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message="保存后将清除已配置的解读模型 API Key 引用。"
              />
            )}
            {analyzer.has_key && !analyzer.clear_key && (
              <Button
                danger
                size="small"
                onClick={() => setAnalyzer({ ...analyzer, api_key: '', clear_key: true })}
              >
                标记清除解读模型 API Key
              </Button>
            )}
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
        </Card>
      </div>
    );
  }

  if (section === 'models') {
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
                  模型调用配置（NAME1..N / BASE_URL1..N / API_KEY1..N）
                </Title>
              </Space>
              <div style={{ marginTop: 4 }}>{configFileHint}</div>
            </div>
            <Space wrap>
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
                onClick={handleSaveSection}
              >
                保存模型调用配置
              </Button>
            </Space>
          </div>

          {profiles.length === 0 ? (
            <Alert
              type="info"
              showIcon
              message="当前暂无保存的模型调用配置组，请点击右上角「新增通用配置」添加。"
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
                      {onJumpToGeneralWithAnalyzer && (
                        <Button
                          size="small"
                          type="link"
                          icon={<SettingOutlined />}
                          onClick={() => copyFromProfileToAnalyzer(prof)}
                        >
                          复用此地址到解读模型
                        </Button>
                      )}
                      <Popconfirm
                        title="确定删除这组配置吗？删除后需点击「保存模型调用配置」生效。"
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
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Card
        loading={loading}
        style={{ borderRadius: 12 }}
        title={
          <div>
            <Space align="center">
              <LinkOutlined style={{ fontSize: 18, color: '#0f172a' }} />
              <Title level={4} style={{ margin: 0 }}>
                素材库链接配置
              </Title>
            </Space>
            <div style={{ marginTop: 4, fontWeight: 400 }}>{configFileHint}</div>
          </div>
        }
        extra={
          <Space wrap>
            <Button
              icon={<PlusOutlined />}
              onClick={() =>
                setAssetProfiles([
                  ...assetProfiles,
                  {
                    name: `素材库配置 ${assetProfiles.length + 1}`,
                    endpoint: 'https://token.peopleopen.cn/',
                  },
                ])
              }
            >
              添加素材库配置
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saving}
              onClick={handleSaveSection}
            >
              保存素材库配置
            </Button>
          </Space>
        }
      >
        <Text type="secondary">
          虚拟人像（AIGC）与真人人像可分别选择对应的素材库配置组。AK/SK 统一保存在 1Password，均留空则保留原值。
        </Text>
        {!assetProfiles.length && (
          <Alert
            style={{ marginTop: 16 }}
            type="info"
            showIcon
            message="尚无素材库链接配置，点击右上角「添加素材库配置」开始。"
          />
        )}
        {assetProfiles.map((asset, index) => (
          <Card
            key={asset.id || `new-${index}`}
            size="small"
            style={{ marginTop: 16, background: '#fafafa', borderRadius: 8 }}
            title={
              <Space>
                <Tag color="geekblue">素材库 {index + 1}</Tag>
                <Text strong>{asset.name || `素材库配置 ${index + 1}`}</Text>
                {asset.has_credentials && <Tag color="success">AK/SK 已保存</Tag>}
              </Space>
            }
            extra={
              <Popconfirm
                title="移除此配置组？保存后将解除该组凭证引用，1Password 条目保留。"
                onConfirm={() => setAssetProfiles(assetProfiles.filter((_, i) => i !== index))}
              >
                <Button danger size="small" icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            }
          >
            <Form layout="vertical">
              <Row gutter={16}>
                <Col xs={24} md={8}>
                  <Form.Item label="配置名称">
                    <Input
                      value={asset.name}
                      onChange={(e) => updateAsset(index, { name: e.target.value })}
                      placeholder="例如：生产素材库 / 测试素材库"
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} md={16}>
                  <Form.Item
                    label="素材库根地址"
                    extra="填写 HTTP(S) 根地址，不附加 /api/v3、/v1 或 /business-api。"
                  >
                    <Input
                      value={asset.endpoint}
                      onChange={(e) => updateAsset(index, { endpoint: e.target.value })}
                      placeholder="https://你的素材库域名"
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item label="Access Key（AK）">
                    <Input.Password
                      value={asset.access_key}
                      autoComplete="off"
                      onChange={(e) =>
                        updateAsset(index, {
                          access_key: e.target.value,
                          clear_credentials: false,
                        })
                      }
                      placeholder={
                        asset.has_credentials
                          ? '留空保留；更新时请同时填写 AK 和 SK'
                          : '请输入 AK'
                      }
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item label="Secret Key（SK）">
                    <Input.Password
                      value={asset.secret_key}
                      autoComplete="new-password"
                      onChange={(e) =>
                        updateAsset(index, {
                          secret_key: e.target.value,
                          clear_credentials: false,
                        })
                      }
                      placeholder={
                        asset.has_credentials
                          ? '留空保留；更新时请同时填写 AK 和 SK'
                          : '请输入 SK'
                      }
                    />
                  </Form.Item>
                </Col>
              </Row>
              {asset.clear_credentials && (
                <Alert
                  type="warning"
                  showIcon
                  message="保存时将解除本组 AK/SK 引用，保留名称及根地址。"
                />
              )}
              {asset.has_credentials && !asset.clear_credentials && (
                <Button
                  danger
                  size="small"
                  onClick={() =>
                    updateAsset(index, {
                      access_key: '',
                      secret_key: '',
                      clear_credentials: true,
                    })
                  }
                >
                  标记清除本组 AK/SK
                </Button>
              )}
            </Form>
          </Card>
        ))}
      </Card>
    </div>
  );
};
