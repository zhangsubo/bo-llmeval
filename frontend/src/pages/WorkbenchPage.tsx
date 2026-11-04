import { MediaImages } from '../components/MediaImages';
import { C2paPanel } from '../components/C2paPanel';
import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  AutoComplete,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Divider,
  Form,
  Input,
  InputNumber,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CopyOutlined,
  EyeOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  RobotOutlined,
  StopOutlined,
  DeleteOutlined,
  VideoCameraOutlined,
  MessageOutlined,
  PictureOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  type AppConfig,
  type TestRunPayload,
  type TestRunSnapshot,
  cancelTestRun,
  startTestRun,
  subscribeTestRunEvents,
} from '../api/client';
import { MarkdownViewer } from '../components/MarkdownViewer';

const { Text } = Typography;
const { TextArea } = Input;

export type WorkbenchScope = 'text' | 'video' | 'image';
export type ApiType =
  | 'chat/completions'
  | 'responses'
  | 'video'
  | 'seedance'
  | 'images/generations'
  | 'bailian-video';

const SCOPE_PROTOCOLS: Record<WorkbenchScope, ApiType[]> = {
  text: ['chat/completions', 'responses'],
  video: ['video', 'seedance', 'bailian-video'],
  image: ['images/generations'],
};

const SCOPE_MODELS: Record<WorkbenchScope, string[]> = {
  text: ['qwen3.7-plus', 'qwen3.8-max', 'kimi-k3'],
  video: [
    'doubao-seedance-2.0',
    'doubao-seedance-2.5',
    'wan2.7-t2v-2026-06-12',
    'wan2.6-i2v-flash',
  ],
  image: ['wanx-v2', 'cogview-4', 'cogview-3-plus', 'qwen-image-3.0'],
};

const SCOPE_TITLES: Record<WorkbenchScope, string> = {
  text: '文字模型 API 诊断测试',
  video: '视频模型 API 诊断测试',
  image: '图片生成 API 诊断测试',
};

const SCOPE_MODEL_PLACEHOLDERS: Record<WorkbenchScope, string> = {
  text: '请选择或输入文字模型（如 qwen3.7-plus / qwen3.8-max / kimi-k3）',
  video: '请选择或输入视频模型 / 推理接入点（如 doubao-seedance-2.0 / wan2.7-t2v-2026-06-12）',
  image: '请选择或输入图片生成模型（如 qwen-image-3.0 / cogview-4 / wanx-v2）',
};

const SCOPE_SAMPLE_PROMPTS: Record<WorkbenchScope, string> = {
  text: '你好！请用三句话简要介绍你自己。',
  video: '一只橘猫在阳光下的花园草地上奔跑，镜头跟随，电影感画质',
  image: '一座漂浮在云海之上的未来生态花园城市，清晨柔和金光，超高清细节，电影级构图',
};

export interface WorkbenchPreset {
  video_request_format?: 'default' | 'alibaba';
  video_query_api?: 'generations' | 'videos';
  api_type?: ApiType;
  task_id?: string;
  action?: 'create' | 'query' | 'list' | 'delete';
  model?: string;
  prompt?: string;
  base_url?: string;
}

interface WorkbenchPageProps {
  scope?: WorkbenchScope;
  config: AppConfig | null;
  preset?: WorkbenchPreset | null;
  onClearPreset?: () => void;
  onNavigateToResult: (category: string, folder: string) => void;
}

export const WorkbenchPage: React.FC<WorkbenchPageProps> = ({
  scope = 'text',
  config,
  preset,
  onClearPreset,
  onNavigateToResult,
}) => {
  const allowedProtocols = SCOPE_PROTOCOLS[scope];
  const scopeModels = SCOPE_MODELS[scope];
  const scopeModelOptions = scopeModels.map((m) => ({ value: m, label: m }));

  const [profileId, setProfileId] = useState<number | 'custom'>(1);
  const [customBaseUrl, setCustomBaseUrl] = useState('');
  const [customApiKey, setCustomApiKey] = useState('');
  const [overrideBaseUrl, setOverrideBaseUrl] = useState('');
  const [apiType, setApiType] = useState<ApiType>(() =>
    preset?.api_type && allowedProtocols.includes(preset.api_type)
      ? preset.api_type
      : allowedProtocols[0]
  );
  const skipBaseUrlSyncRef = useRef(false);
  const [model, setModel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [stream, setStream] = useState(true);
  const [imageCount, setImageCount] = useState(1);
  const [imageSize, setImageSize] = useState('');
  const [imageOptions, setImageOptions] = useState<Record<string, string>>({});
  const [negativePrompt, setNegativePrompt] = useState('');
  const [mediaSeed, setMediaSeed] = useState<number | null>(null);
  const [promptExtend, setPromptExtend] = useState(true);
  const [shotType, setShotType] = useState('single');

  // 视频与 Seedance 参数
  const [videoMode, setVideoMode] = useState<'create' | 'query'>('create');
  const [seedanceAction, setSeedanceAction] = useState<'create' | 'query' | 'list' | 'delete'>(
    'create'
  );
  const [taskId, setTaskId] = useState('');
  const [waitCompletion, setWaitCompletion] = useState(true);
  const [videoRequestFormat, setVideoRequestFormat] = useState<'default' | 'alibaba'>('default');
  const [videoQueryApi, setVideoQueryApi] = useState<'generations' | 'videos'>('generations');
  const [dimensionMode, setDimensionMode] = useState<'resolution' | 'size'>('resolution');
  const [resolution, setResolution] = useState('720p');
  const [size, setSize] = useState('1280x720');
  const [durationField, setDurationField] = useState<'seconds' | 'duration'>('seconds');
  const [durationValue, setDurationValue] = useState<number>(5);
  const [ratio, setRatio] = useState('16:9');
  const [generateAudio, setGenerateAudio] = useState(true);
  const [watermark, setWatermark] = useState(false);
  const [contentArray, setContentArray] = useState(false);

  // 参考素材与首尾帧
  const [mediaMode, setMediaMode] = useState<'none' | 'references' | 'frames'>('none');
  const [refImages, setRefImages] = useState<string[]>([]);
  const [refVideos, setRefVideos] = useState<string[]>([]);
  const [refAudios, setRefAudios] = useState<string[]>([]);
  const [firstFrame, setFirstFrame] = useState('');
  const [lastFrame, setLastFrame] = useState('');

  // Seedance 列表筛选
  const [pageNum, setPageNum] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(10);
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [filterModel, setFilterModel] = useState<string>('');
  const [filterTaskIds, setFilterTaskIds] = useState<string>('');

  // 超时与 AI 解读设置（默认继承「通用配置」中的自动解读开关）
  const [pollInterval, setPollInterval] = useState<number>(5);
  const [requestTimeout, setRequestTimeout] = useState<number>(120);
  const [downloadTimeout, setDownloadTimeout] = useState<number>(300);
  const [maxWait, setMaxWait] = useState<number>(1800);
  const [autoAnalyze, setAutoAnalyze] = useState<boolean>(() =>
    Boolean(config?.analyzer?.auto_analyze)
  );
  const autoAnalyzeTouchedRef = useRef(false);
  const [c2paCheck, setC2paCheck] = useState(false);

  useEffect(() => {
    if (!autoAnalyzeTouchedRef.current && config?.analyzer?.auto_analyze !== undefined) {
      setAutoAnalyze(Boolean(config.analyzer.auto_analyze));
    }
  }, [config?.analyzer?.auto_analyze]);

  // 运行状态
  const [currentRun, setCurrentRun] = useState<TestRunSnapshot | null>(null);
  const [starting, setStarting] = useState(false);
  const [videoMeta, setVideoMeta] = useState<{
    url: string;
    width: number;
    height: number;
    duration: number;
  } | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);

  const formatVideoResolution = (width: number, height: number) => {
    if (!width || !height) return '';
    const shortSide = Math.min(width, height);
    let tier = '';
    if (shortSide >= 2000) tier = '4K';
    else if (shortSide >= 1000) tier = '1080p';
    else if (shortSide >= 680) tier = '720p';
    else if (shortSide >= 400) tier = '480p';
    return tier ? `${width} × ${height} (${tier})` : `${width} × ${height}`;
  };

  const computeDefaultBaseUrl = (
    profId: number | 'custom',
    mode: 'chat/completions' | 'responses' | 'video' | 'seedance' | 'images/generations' | 'bailian-video'
  ) => {
    if (profId === 'custom' || !config) return '';
    const found = config.profiles.find((p) => p.id === profId);
    if (!found || !found.base_url) return '';
    const trimmed = found.base_url.trim().replace(/\/+$/, '');
    if (mode === 'seedance' && trimmed.endsWith('/v1')) {
      return `${trimmed.slice(0, -3)}/api/v3`;
    }
    return trimmed;
  };

  useEffect(() => {
    if (config && config.profiles.length > 0 && profileId !== 'custom') {
      const exists = config.profiles.some((p) => p.id === profileId);
      if (!exists) {
        setProfileId(config.profiles[0].id ?? 1);
      }
    } else if (config && config.profiles.length === 0) {
      setProfileId('custom');
    }
  }, [config]);

  useEffect(() => {
    if (skipBaseUrlSyncRef.current) {
      skipBaseUrlSyncRef.current = false;
      return;
    }
    if (profileId !== 'custom') {
      setOverrideBaseUrl(computeDefaultBaseUrl(profileId, apiType));
    }
  }, [profileId, apiType, config]);

  useEffect(() => {
    if (preset) {
      if (preset.api_type && allowedProtocols.includes(preset.api_type)) {
        setApiType(preset.api_type);
      }
      setVideoRequestFormat(preset.video_request_format || 'default');
      setVideoQueryApi(preset.video_query_api || 'generations');
      if (preset.model) setModel(preset.model);
      if (preset.prompt) setPrompt(preset.prompt);
      if (preset.task_id) {
        setTaskId(preset.task_id);
        if (preset.api_type === 'seedance') {
          setSeedanceAction(preset.action || 'query');
        } else {
          setVideoMode('query');
        }
      }
      if (preset.base_url && config) {
        const matched = config.profiles.find(
          (p) =>
            p.base_url === preset.base_url ||
            (preset.base_url?.endsWith('/api/v3') &&
              p.base_url.replace(/\/v1\/?$/, '/api/v3') === preset.base_url)
        );
        skipBaseUrlSyncRef.current = Boolean(
          (matched?.id && matched.id !== profileId) ||
            (preset.api_type && preset.api_type !== apiType)
        );
        if (matched && matched.id) {
          setProfileId(matched.id);
        }
        setOverrideBaseUrl(preset.base_url);
      }
      onClearPreset?.();
    }
  }, [preset]);

  useEffect(() => {
    return () => {
      unsubscribeRef.current?.();
    };
  }, []);

  const isAlibabaGateway = apiType === 'video' && videoRequestFormat === 'alibaba';
  const isAlibabaFlash = isAlibabaGateway && model.trim() === 'wan2.6-i2v-flash';
  const isBailian = apiType === 'bailian-video';
  const isImage = apiType === 'images/generations';
  const isQwenImage = isImage && model.startsWith('qwen-image');
  const imageMax = isQwenImage ? 6 : model === 'cogview-3-plus' ? 1 : 10;
  const isVideo = apiType === 'video' || apiType === 'seedance' || isBailian;
  const isSeedance = apiType === 'seedance';
  const isNewRequest = isSeedance
    ? seedanceAction === 'create'
    : isVideo
    ? videoMode === 'create'
    : true;

  const handleStartTest = async () => {
    unsubscribeRef.current?.();
    setStarting(true);
    try {
      const payload: TestRunPayload = {
        profile_id: profileId,
        custom_base_url:
          profileId === 'custom'
            ? customBaseUrl
            : overrideBaseUrl.trim() || computeDefaultBaseUrl(profileId, apiType),
        custom_api_key: profileId === 'custom' ? customApiKey : undefined,
        api_type: apiType,
        model: isNewRequest ? model : undefined,
        prompt: isNewRequest ? prompt : undefined,
        stream: !isVideo ? stream : undefined,
        poll_interval: pollInterval,
        request_timeout: requestTimeout,
        download_timeout: downloadTimeout,
        max_wait: maxWait,
        auto_analyze: autoAnalyze,
        c2pa_check: scope === 'video' ? c2paCheck : false,
      };

      if (apiType === 'video') {
        payload.video_query_api = videoQueryApi;
        payload.video_request_format = videoRequestFormat;
        if (videoMode === 'query') {
          payload.task_id = taskId;
        } else {
          if (dimensionMode === 'size') {
            payload.size = size;
          } else {
            payload.resolution = resolution;
          }
          if (durationField === 'duration') {
            payload.duration = durationValue;
          } else {
            payload.seconds = durationValue;
          }
          if (!isAlibabaFlash) payload.ratio = ratio;
          payload.generate_audio = generateAudio;
          payload.watermark = watermark;
          payload.content_array = !isAlibabaGateway && contentArray;
          if (mediaMode === 'references') {
            payload.reference_image = refImages.filter((x) => x.trim());
            payload.reference_video = refVideos.filter((x) => x.trim());
            payload.reference_audio = refAudios.filter((x) => x.trim());
          }
        }
        if (isAlibabaGateway && videoMode === 'create') {
          if (isAlibabaFlash && (payload.reference_image?.length || 0) !== 1) {
            throw new Error('wan2.6-i2v-flash 必须且只能提供 1 张参考图片，请选择参考素材并填写图片 URL');
          }
          if (payload.reference_video?.length || payload.reference_audio?.length) {
            throw new Error('当前 Alibaba 网关格式不支持参考视频/参考音频，请移除这些输入');
          }
        }
      } else if (apiType === 'seedance') {
        payload.action = seedanceAction;
        payload.wait = waitCompletion;
        if (seedanceAction === 'query' || seedanceAction === 'delete') {
          payload.task_id = taskId;
        } else if (seedanceAction === 'list') {
          payload.page_num = pageNum;
          payload.page_size = pageSize;
          if (filterStatus) payload.filter_status = filterStatus;
          if (filterModel) payload.filter_model = filterModel;
          if (filterTaskIds.trim()) {
            payload.filter_task_ids = filterTaskIds
              .split(/[,，\s]+/)
              .map((s) => s.trim())
              .filter(Boolean);
          }
        } else if (seedanceAction === 'create') {
          payload.resolution = resolution;
          payload.duration = Math.round(durationValue);
          payload.ratio = ratio;
          payload.generate_audio = generateAudio;
          payload.watermark = watermark;
          if (mediaMode === 'frames') {
            payload.first_frame = firstFrame;
            payload.last_frame = lastFrame;
          } else if (mediaMode === 'references') {
            payload.reference_image = refImages.filter((x) => x.trim());
            payload.reference_video = refVideos.filter((x) => x.trim());
            payload.reference_audio = refAudios.filter((x) => x.trim());
          }
        }
      }

      if (isImage || isBailian) {
        payload.stream = false;
        if (isBailian || isQwenImage) {
          payload.negative_prompt = negativePrompt || undefined;
          payload.seed = mediaSeed ?? undefined;
          payload.prompt_extend = promptExtend;
          payload.watermark = watermark;
        }
        payload.reference_image = isImage || model === 'wan2.6-i2v-flash' ? refImages.filter((x) => x.trim()) : [];
        if (isImage) {
          payload.image_count = Math.min(imageCount, imageMax);
          if (!isQwenImage) {
            payload.quality = imageOptions.quality || undefined;
            payload.response_format = imageOptions.response_format || undefined;
            payload.user = imageOptions.user || undefined;
            if (!['cogview-4', 'cogview-3-plus'].includes(model)) {
              payload.background = imageOptions.background || undefined;
              payload.moderation = imageOptions.moderation || undefined;
            }
            if (!['wanx-v2', 'cogview-4'].includes(model)) payload.style = imageOptions.style || undefined;
          }
          payload.size = imageSize || undefined;
        } else if (videoMode === 'query') {
          payload.task_id = taskId;
        } else {
          payload.resolution = resolution;
          payload.duration = durationValue;
          payload.ratio = ratio;
          payload.generate_audio = model === 'wan2.6-i2v-flash' ? generateAudio : true;
          payload.reference_audio = refAudios.filter((x) => x.trim());
          if (model === 'wan2.6-i2v-flash') payload.shot_type = shotType;
        }
      }
      const snapshot = await startTestRun(payload);
      setCurrentRun(snapshot);
      message.success('测试任务已启动');

      unsubscribeRef.current = subscribeTestRunEvents(snapshot.run_id, {
        onUpdate: (next) => setCurrentRun(next),
        onDone: (finalSnap) => {
          setCurrentRun(finalSnap);
          if (finalSnap.status === 'completed' || finalSnap.status === 'submitted') {
            message.success(`测试执行完成（状态：${finalSnap.status}）`);
          } else {
            message.warning(`测试结束（状态：${finalSnap.status}）`);
          }
        },
      });
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '启动测试失败，请检查参数');
    } finally {
      setStarting(false);
    }
  };

  const handleCancelRun = async () => {
    if (!currentRun) return;
    try {
      const snap = await cancelTestRun(currentRun.run_id);
      setCurrentRun(snap);
      message.info('已发送中断信号，已获得的结果已安全保存');
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '中断任务失败');
    }
  };

  const isRunning = currentRun?.status === 'running' && !currentRun?.finished_at;
  const liveReport = currentRun?.live_report || currentRun?.detail?.result;

  const resultJsonPath = (() => {
    if (!currentRun) return '';
    if (currentRun.result_json_path) return currentRun.result_json_path;
    if (currentRun.detail?.result_json_path) return currentRun.detail.result_json_path;
    if (currentRun.folder_path) return `${currentRun.folder_path}/result.json`;
    if (currentRun.detail?.folder_path) return `${currentRun.detail.folder_path}/result.json`;
    for (const line of currentRun.logs || []) {
      const mDetail = line.trim().match(/^详细结果：(.+)$/);
      if (mDetail) return mDetail[1].trim();
      const mDir = line.trim().match(/^结果目录：(.+)$/);
      if (mDir) return `${mDir[1].trim().replace(/\/+$/, '')}/result.json`;
    }
    return '';
  })();

  const handleCopyPath = async (textToCopy: string) => {
    if (!textToCopy) return;
    try {
      await navigator.clipboard.writeText(textToCopy);
      message.success(`已复制路径：${textToCopy}`);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = textToCopy;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      message.success(`已复制路径：${textToCopy}`);
    }
  };

  return (
    <Row gutter={[20, 20]}>
      {/* 左侧参数配置区 */}
      <Col xs={24} lg={13}>
        <Card
          style={{ borderRadius: 12 }}
          title={
            <Space>
              <ThunderboltOutlined style={{ color: '#1677ff' }} />
              <span>{SCOPE_TITLES[scope]}</span>
            </Space>
          }
          extra={
            <Space>
              <Text type="secondary" style={{ fontSize: 13 }}>
                完成后自动 AI 解读
              </Text>
              <Switch
                checked={autoAnalyze}
                onChange={(checked) => {
                  autoAnalyzeTouchedRef.current = true;
                  setAutoAnalyze(checked);
                }}
              />
            </Space>
          }
        >
          <Form layout="vertical">
            {scope === 'video' && (
              <Form.Item label="视频下载后检测 C2PA（缺失仅提示）">
                <Switch checked={c2paCheck} onChange={setC2paCheck} />
              </Form.Item>
            )}
            {/* 1. 选择配置组 */}
            <Form.Item label="1. 选择 API 凭证配置（Base URL 与 API Key 成对使用）">
              <Select
                value={profileId}
                onChange={(val) => setProfileId(val)}
                options={[
                  ...(config?.profiles || []).map((p) => ({
                    value: p.id!,
                    label: `${p.name} | ${p.base_url} (${p.api_key_masked || '已配置'})`,
                  })),
                  {
                    value: 'custom',
                    label: '0. 临时自定义 Base URL 和 API Key（不回写配置文件）',
                  },
                ]}
              />
            </Form.Item>

            {profileId === 'custom' && (
              <Row gutter={12}>
                <Col xs={24} md={12}>
                  <Form.Item label="临时 Base URL" required>
                    <Input
                      value={customBaseUrl}
                      onChange={(e) => setCustomBaseUrl(e.target.value)}
                      placeholder="https://api.openai.com/v1"
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item label="临时 API Key" required>
                    <Input.Password
                      value={customApiKey}
                      onChange={(e) => setCustomApiKey(e.target.value)}
                      placeholder="sk-..."
                    />
                  </Form.Item>
                </Col>
              </Row>
            )}

            {/* 2. 选择接口类型 */}
            <Form.Item
              label={
                scope === 'text'
                  ? '2. 选择文字模型接口协议'
                  : scope === 'video'
                  ? '2. 选择视频模型接口协议'
                  : '2. 当前测试接口协议'
              }
            >
              <Radio.Group
                value={apiType}
                onChange={(e) => setApiType(e.target.value)}
                buttonStyle="solid"
                style={{ width: '100%' }}
              >
                {scope === 'text' && (
                  <>
                    <Radio.Button value="chat/completions">
                      <MessageOutlined /> 1. chat/completions
                    </Radio.Button>
                    <Radio.Button value="responses">
                      <MessageOutlined /> 2. responses
                    </Radio.Button>
                  </>
                )}
                {scope === 'video' && (
                  <>
                    <Radio.Button value="video">
                      <VideoCameraOutlined /> 1. video (兼容视频)
                    </Radio.Button>
                    <Radio.Button value="seedance">
                      <VideoCameraOutlined /> 2. seedance (火山方舟原生)
                    </Radio.Button>
                    <Radio.Button value="bailian-video">
                      <VideoCameraOutlined /> 3. 百炼原生视频
                    </Radio.Button>
                  </>
                )}
                {scope === 'image' && (
                  <Radio.Button value="images/generations">
                    <PictureOutlined /> images/generations (Images 图片生成)
                  </Radio.Button>
                )}
              </Radio.Group>
            </Form.Item>

            {(isImage || isBailian) && <Alert type="info" showIcon style={{ marginBottom: 16 }} message={isImage ? (isQwenImage ? "Qwen：Base URL 使用百炼地域域名 + /compatible-mode/v1。" : "通用 Images：Base URL 使用供应商 API 地址 + /v1；同步生成图片，参考图为单张 URL 或 Base64。") : "百炼视频：Base URL 使用百炼地域域名 + /api/v1；模型、Key 与地域须一致。"} />}

            {profileId !== 'custom' && (
              <Form.Item
                label={
                  <Space size={8}>
                    <span>本次测试 Base URL（沿用所选配置 API Key，可按需修改）</span>
                    {apiType === 'seedance' && (
                      <Tag color="blue" style={{ margin: 0 }}>
                        火山方舟原生接口已自动切换为 /api/v3
                      </Tag>
                    )}
                  </Space>
                }
              >
                <Input
                  value={overrideBaseUrl}
                  onChange={(e) => setOverrideBaseUrl(e.target.value)}
                  placeholder={
                    apiType === 'seedance'
                      ? 'https://ark.cn-beijing.volces.com/api/v3'
                      : apiType === 'bailian-video'
                      ? 'https://dashscope.aliyuncs.com/api/v1'
                      : 'https://api.openai.com/v1'
                  }
                />
              </Form.Item>
            )}

            {/* 3. 针对 video 或 seedance 的操作子模式 */}
            {(apiType === 'video' || isBailian) && (
              <Card
                size="small"
                style={{ marginBottom: 16, background: '#f8fafc', borderRadius: 8 }}
              >
                <Row gutter={16} align="middle">
                  <Col xs={24} md={12}>
                    <Form.Item label="视频测试模式" style={{ marginBottom: 0 }}>
                      <Radio.Group
                        value={videoMode}
                        onChange={(e) => setVideoMode(e.target.value)}
                      >
                        <Radio value="create">新建生成任务</Radio>
                        <Radio value="query">查询已有任务 (--task-id)</Radio>
                      </Radio.Group>
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item hidden={isBailian} label="状态查询端点 (--video-query-api)" style={{ marginBottom: 0 }}>
                      <Select
                        value={videoQueryApi}
                        onChange={setVideoQueryApi}
                        options={[
                          {
                            value: 'generations',
                            label: 'generations (/v1/video/generations/{id} 含用量)',
                          },
                          { value: 'videos', label: 'videos (/v1/videos/{id} 仅状态)' },
                        ]}
                      />
                    </Form.Item>
                  </Col>
                </Row>
                {apiType === 'video' && (
                  <Form.Item label="请求格式 (--video-request-format)" style={{ marginTop: 12, marginBottom: 0 }}>
                    <Select value={videoRequestFormat} onChange={setVideoRequestFormat} options={[
                      { value: 'default', label: '默认兼容格式（保留现有行为）' },
                      { value: 'alibaba', label: 'Alibaba 网关格式（顶层 prompt / input_reference / audio）' },
                    ]} />
                    {isAlibabaGateway && <Alert type="info" showIcon style={{ marginTop: 8 }}
                      message="创建仍使用当前网关 /v1/videos，不是百炼原生 API；查询端点独立选择。"
                      description={isAlibabaFlash
                        ? 'wan2.6-i2v-flash 需要且只接受一张 HTTP(S) 参考图片，画幅跟随图片，不发送 ratio；音频开关映射为 audio。'
                        : '参考图片使用 input_reference/images；当前适配不支持参考视频或音频。具体模型的图片数量和用法仍受网关契约约束。'} />}
                  </Form.Item>
                )}
                {videoMode === 'query' && (
                  <Form.Item
                    label="已有平台任务 ID (--task-id)"
                    required
                    style={{ marginTop: 12, marginBottom: 0 }}
                  >
                    <Input
                      value={taskId}
                      onChange={(e) => setTaskId(e.target.value)}
                      placeholder="例如 task_C5jpc03RYFDCkAgxJw6xyCIh24CEBX7s"
                    />
                  </Form.Item>
                )}
              </Card>
            )}

            {apiType === 'seedance' && (
              <Card
                size="small"
                style={{ marginBottom: 16, background: '#f8fafc', borderRadius: 8 }}
              >
                <Row gutter={16} align="middle">
                  <Col xs={24} md={15}>
                    <Form.Item label="Seedance 操作 (--action)" style={{ marginBottom: 0 }}>
                      <Radio.Group
                        value={seedanceAction}
                        onChange={(e) => setSeedanceAction(e.target.value)}
                      >
                        <Radio value="create">1. 创建 (create)</Radio>
                        <Radio value="query">2. 查询 (query)</Radio>
                        <Radio value="list">3. 列表 (list)</Radio>
                        <Radio value="delete">4. 取消/删除 (delete)</Radio>
                      </Radio.Group>
                    </Form.Item>
                  </Col>
                  {(seedanceAction === 'create' || seedanceAction === 'query') && (
                    <Col xs={24} md={9}>
                      <Form.Item label="持续轮询至完成 (--wait)" style={{ marginBottom: 0 }}>
                        <Switch
                          checked={waitCompletion}
                          onChange={setWaitCompletion}
                          checkedChildren="轮询等待并下载"
                          unCheckedChildren="--no-wait 单次返回"
                        />
                      </Form.Item>
                    </Col>
                  )}
                </Row>

                {(seedanceAction === 'query' || seedanceAction === 'delete') && (
                  <Form.Item
                    label="方舟任务 ID (--task-id)"
                    required
                    style={{ marginTop: 12, marginBottom: 0 }}
                  >
                    <Input
                      value={taskId}
                      onChange={(e) => setTaskId(e.target.value)}
                      placeholder="例如 cgt-20261015..."
                    />
                  </Form.Item>
                )}

                {seedanceAction === 'list' && (
                  <Row gutter={12} style={{ marginTop: 12 }}>
                    <Col xs={12} md={6}>
                      <Form.Item label="页码" style={{ marginBottom: 8 }}>
                        <InputNumber
                          min={1}
                          value={pageNum}
                          onChange={(v) => setPageNum(v || 1)}
                          style={{ width: '100%' }}
                        />
                      </Form.Item>
                    </Col>
                    <Col xs={12} md={6}>
                      <Form.Item label="每页条数" style={{ marginBottom: 8 }}>
                        <InputNumber
                          min={1}
                          max={100}
                          value={pageSize}
                          onChange={(v) => setPageSize(v || 10)}
                          style={{ width: '100%' }}
                        />
                      </Form.Item>
                    </Col>
                    <Col xs={12} md={6}>
                      <Form.Item label="状态筛选" style={{ marginBottom: 8 }}>
                        <Select
                          allowClear
                          placeholder="全部状态"
                          value={filterStatus || undefined}
                          onChange={(v) => setFilterStatus(v || '')}
                          options={[
                            { value: 'queued', label: 'queued' },
                            { value: 'running', label: 'running' },
                            { value: 'succeeded', label: 'succeeded' },
                            { value: 'failed', label: 'failed' },
                            { value: 'cancelled', label: 'cancelled' },
                            { value: 'expired', label: 'expired' },
                          ]}
                        />
                      </Form.Item>
                    </Col>
                    <Col xs={12} md={6}>
                      <Form.Item label="接入点/模型筛选" style={{ marginBottom: 8 }}>
                        <Input
                          value={filterModel}
                          onChange={(e) => setFilterModel(e.target.value)}
                          placeholder="ep-..."
                        />
                      </Form.Item>
                    </Col>
                    <Col span={24}>
                      <Form.Item label="按任务 ID 筛选（多个用逗号分隔）" style={{ marginBottom: 0 }}>
                        <Input
                          value={filterTaskIds}
                          onChange={(e) => setFilterTaskIds(e.target.value)}
                          placeholder="cgt-xxx, cgt-yyy"
                        />
                      </Form.Item>
                    </Col>
                  </Row>
                )}
              </Card>
            )}

            {/* 4. Model ID 与 Prompt (新建请求时必填) */}
            {isNewRequest && (
              <>
                <Row gutter={12}>
                  <Col xs={24} md={isVideo || isImage ? 24 : 16}>
                    <Form.Item label="3. Model ID / 推理接入点 ID" required>
                      <AutoComplete
                        allowClear
                        value={model}
                        onChange={(val) => setModel(val)}
                        options={scopeModelOptions}
                        filterOption={(inputValue, option) => {
                          if (!inputValue || scopeModels.includes(inputValue)) {
                            return true;
                          }
                          return (
                            option?.value.toLowerCase().includes(inputValue.toLowerCase()) ?? false
                          );
                        }}
                        placeholder={SCOPE_MODEL_PLACEHOLDERS[scope]}
                        style={{ width: '100%' }}
                      />
                    </Form.Item>
                  </Col>
                  {!isVideo && !isImage && (
                    <Col xs={24} md={8}>
                      <Form.Item label="流式传输 (--stream)">
                        <Switch
                          checked={stream}
                          onChange={setStream}
                          checkedChildren="SSE 流式 (测 TTFT)"
                          unCheckedChildren="非流式 (--no-stream)"
                        />
                      </Form.Item>
                    </Col>
                  )}
                </Row>

                {(isImage || isBailian) && <Card size="small" title={isImage ? '图片生成参数' : '百炼视频参数'} style={{ marginBottom: 16 }}>
                  <Row gutter={12}>
                    {isImage ? <>
                      <Col span={12}><Form.Item label={`图片数量 (1–${imageMax})`}><InputNumber min={1} max={imageMax} precision={0} value={Math.min(imageCount, imageMax)} onChange={(v) => setImageCount(v || 1)} /></Form.Item></Col>
                      <Col span={12}><Form.Item label="图片尺寸"><Input value={imageSize} onChange={(e) => setImageSize(e.target.value)} placeholder="留空使用服务端默认；例如 1024x1024" /></Form.Item></Col>
                    </> : <>
                      <Col span={12}><Form.Item label="分辨率"><Select value={resolution} onChange={setResolution} options={[{value:'720p',label:'720P'},{value:'1080p',label:'1080P'}]} /></Form.Item></Col>
                      <Col span={12}><Form.Item label="时长（2–15 秒）"><InputNumber min={2} max={15} precision={0} value={durationValue} onChange={(v) => setDurationValue(v || 5)} /></Form.Item></Col>
                      {model !== 'wan2.6-i2v-flash' && <Col span={12}><Form.Item label="画面比例"><Select value={ratio} onChange={setRatio} options={['16:9','9:16','1:1','4:3','3:4'].map(value => ({value,label:value}))} /></Form.Item></Col>}
                      {model === 'wan2.6-i2v-flash' && <>
                        <Col span={12}><Form.Item label="生成音频"><Switch checked={generateAudio} onChange={setGenerateAudio} /></Form.Item></Col>
                        <Col span={12}><Form.Item label="镜头类型（需开启提示词改写）"><Select value={shotType} onChange={setShotType} options={[{value:'single',label:'单镜头'},{value:'multi',label:'多镜头'}]} /></Form.Item></Col>
                      </>}
                    </>}
                    {(isBailian || isQwenImage) && <>
                    <Col span={12}><Form.Item label="随机种子（可选）"><InputNumber min={0} max={2147483647} precision={0} value={mediaSeed} onChange={setMediaSeed} /></Form.Item></Col>
                    <Col span={6}><Form.Item label="提示词改写"><Switch checked={promptExtend} onChange={setPromptExtend} /></Form.Item></Col>
                    <Col span={6}><Form.Item label="水印"><Switch checked={watermark} onChange={setWatermark} /></Form.Item></Col>
                    </>}
                  </Row>
                  {(isBailian || isQwenImage) && <Form.Item label="反向提示词（可选）"><Input value={negativePrompt} onChange={(e) => setNegativePrompt(e.target.value)} /></Form.Item>}
                  {isImage && !isQwenImage && <Row gutter={12}>
                    {[
                      { key: 'quality', label: '质量', values: ['low', 'medium', 'high', 'auto'] },
                      { key: 'response_format', label: '返回格式', values: ['url', 'b64_json'] },
                      ...(!['cogview-4', 'cogview-3-plus'].includes(model) ? [
                        { key: 'background', label: '背景', values: ['transparent', 'opaque', 'auto'] },
                        { key: 'moderation', label: '审核级别', values: ['low', 'auto'] },
                      ] : []),
                      ...(!['wanx-v2', 'cogview-4'].includes(model) ? [{ key: 'style', label: '风格', values: ['vivid', 'natural'] }] : []),
                    ].map(({ key, label, values }) => <Col span={12} key={key}><Form.Item label={label}>
                      <Select allowClear placeholder="不指定，使用服务端默认" value={imageOptions[key] || undefined} onChange={(value) => setImageOptions({ ...imageOptions, [key]: value || '' })} options={values.map(value => ({ value, label: value }))} />
                    </Form.Item></Col>)}
                    <Col span={24}><Form.Item label="用户标识（可选）"><Input value={imageOptions.user || ''} onChange={(e) => setImageOptions({ ...imageOptions, user: e.target.value })} /></Form.Item></Col>
                  </Row>}
                  {(isImage || model === 'wan2.6-i2v-flash') && <Form.Item label={isImage ? (isQwenImage ? '参考图片 URL / Base64（每行一张，Qwen 3.0 最多 3 张）' : '参考图片（单张 URL 或 Base64，可选）') : '首帧图片 URL（必填一张）'} required={!isImage}>
                    <TextArea rows={2} value={refImages.join('\n')} onChange={(e) => setRefImages(e.target.value.split('\n'))} placeholder="https://..." />
                  </Form.Item>}
                  {isBailian && <Form.Item label="参考音频 URL（可选）"><Input value={refAudios[0] || ''} onChange={(e) => setRefAudios(e.target.value ? [e.target.value] : [])} /></Form.Item>}
                </Card>}

                {/* 视频生成专属参数 */}
                {isVideo && !isBailian && (
                  <Card
                    size="small"
                    title="视频生成规格与多模态参考素材"
                    style={{ marginBottom: 16, borderRadius: 8 }}
                  >
                    <Row gutter={12}>
                      <Col xs={12} md={6}>
                        <Form.Item label="分辨率设置">
                          {apiType === 'video' ? (
                            <Select
                              value={dimensionMode}
                              onChange={setDimensionMode}
                              options={[
                                { value: 'resolution', label: '档位 (--resolution)' },
                                { value: 'size', label: '像素 (--size)' },
                              ]}
                            />
                          ) : (
                            <Input disabled value="档位 (--resolution)" />
                          )}
                        </Form.Item>
                      </Col>
                      <Col xs={12} md={6}>
                        {dimensionMode === 'size' && apiType === 'video' ? (
                          <Form.Item label="像素宽高 (--size)">
                            <Input
                              value={size}
                              onChange={(e) => setSize(e.target.value)}
                              placeholder="1280x720"
                            />
                          </Form.Item>
                        ) : (
                          <Form.Item label="分辨率档位">
                            <Select
                              value={resolution}
                              onChange={setResolution}
                              options={[
                                { value: '480p', label: '480p' },
                                { value: '720p', label: '720p (默认)' },
                                { value: '1080p', label: '1080p' },
                                { value: '4k', label: '4K' },
                              ]}
                            />
                          </Form.Item>
                        )}
                      </Col>
                      <Col xs={12} md={6}>
                        <Form.Item
                          label={
                            apiType === 'seedance'
                              ? '时长 (duration 整数秒)'
                              : `时长 (${durationField})`
                          }
                        >
                          <InputNumber
                            min={1}
                            max={60}
                            precision={apiType === 'seedance' ? 0 : undefined}
                            value={durationValue}
                            onChange={(v) => setDurationValue(v || 5)}
                            style={{ width: '100%' }}
                            addonAfter={
                              apiType === 'video' ? (
                                <Select
                                  value={durationField}
                                  onChange={setDurationField}
                                  options={[
                                    { value: 'seconds', label: 'seconds' },
                                    { value: 'duration', label: 'duration' },
                                  ]}
                                />
                              ) : (
                                '秒'
                              )
                            }
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={12} md={6}>
                        <Form.Item label="画面比例 (--ratio)" extra={isAlibabaFlash ? '画幅跟随输入图片，不发送 ratio' : undefined}>
                          <Select
                            disabled={isAlibabaFlash}
                            value={ratio}
                            onChange={setRatio}
                            options={[
                              { value: '16:9', label: '16:9 横屏' },
                              { value: '9:16', label: '9:16 竖屏' },
                              { value: '1:1', label: '1:1 方屏' },
                              { value: '4:3', label: '4:3' },
                              { value: '3:4', label: '3:4' },
                              { value: '21:9', label: '21:9 宽银幕' },
                            ]}
                          />
                        </Form.Item>
                      </Col>
                    </Row>

                    <Row gutter={12}>
                      <Col xs={8} md={8}>
                        <Form.Item label={isAlibabaGateway ? '同步音频（映射为 audio）' : '同步音频 (--generate-audio)'} style={{ marginBottom: 8 }}>
                          <Switch checked={generateAudio} onChange={setGenerateAudio} />
                        </Form.Item>
                      </Col>
                      <Col xs={8} md={8}>
                        <Form.Item label="开启水印 (--watermark)" style={{ marginBottom: 8 }}>
                          <Switch checked={watermark} onChange={setWatermark} />
                        </Form.Item>
                      </Col>
                      {apiType === 'video' && !isAlibabaGateway && (
                        <Col xs={8} md={8}>
                          <Form.Item label="使用 content 数组" style={{ marginBottom: 8 }}>
                            <Switch checked={contentArray} onChange={setContentArray} />
                          </Form.Item>
                        </Col>
                      )}
                    </Row>

                    <Divider style={{ margin: '8px 0 12px' }} />

                    <Form.Item label="参考素材 / 首尾帧配置" style={{ marginBottom: 8 }}>
                      <Radio.Group
                        value={mediaMode}
                        onChange={(e) => setMediaMode(e.target.value)}
                      >
                        <Radio value="none">不使用参考素材 (--no-reference)</Radio>
                        <Radio value="references">多模态参考素材 (图片 / 视频 / 音频)</Radio>
                        {apiType === 'seedance' && (
                          <Radio value="frames">首尾帧模式 (--first-frame / --last-frame)</Radio>
                        )}
                      </Radio.Group>
                    </Form.Item>

                    {mediaMode === 'frames' && apiType === 'seedance' && (
                      <Row gutter={12}>
                        <Col xs={24} md={12}>
                          <Form.Item label="首帧图片 URL / asset:// (--first-frame)" required>
                            <Input
                              value={firstFrame}
                              onChange={(e) => setFirstFrame(e.target.value)}
                              placeholder="https://... 或 asset://asset-..."
                            />
                          </Form.Item>
                        </Col>
                        <Col xs={24} md={12}>
                          <Form.Item label="尾帧图片 URL / asset:// (--last-frame 可选)">
                            <Input
                              value={lastFrame}
                              onChange={(e) => setLastFrame(e.target.value)}
                              placeholder="需与首帧配合使用"
                            />
                          </Form.Item>
                        </Col>
                      </Row>
                    )}

                    {mediaMode === 'references' && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {(
                          [
                            ['参考图片 (--reference-image)', refImages, setRefImages],
                            ['参考视频 (--reference-video)', refVideos, setRefVideos],
                            ['参考音频 (--reference-audio)', refAudios, setRefAudios],
                          ] as const
                        ).map(([label, list, setter]) => (
                          <div key={label}>
                            <Space style={{ marginBottom: 4 }}>
                              <Text style={{ fontSize: 13 }}>{label}</Text>
                              <Button
                                size="small"
                                type="dashed"
                                icon={<PlusOutlined />}
                                onClick={() => setter([...list, ''])}
                              >
                                添加
                              </Button>
                            </Space>
                            {list.map((url, idx) => (
                              <Space
                                key={idx}
                                style={{ display: 'flex', marginBottom: 6 }}
                                align="baseline"
                              >
                                <Input
                                  style={{ width: 420 }}
                                  value={url}
                                  onChange={(e) => {
                                    const next = [...list];
                                    next[idx] = e.target.value;
                                    setter(next);
                                  }}
                                  placeholder="https://example.com/media 或 asset://asset-xxx"
                                />
                                <Button
                                  size="small"
                                  danger
                                  icon={<DeleteOutlined />}
                                  onClick={() => setter(list.filter((_, i) => i !== idx))}
                                />
                              </Space>
                            ))}
                          </div>
                        ))}
                      </div>
                    )}
                  </Card>
                )}

                <Form.Item
                  label={
                    <Space>
                      <span>4. 测试提示词 (--prompt)</span>
                      <Button
                        size="small"
                        type="link"
                        onClick={() => setPrompt(SCOPE_SAMPLE_PROMPTS[scope])}
                      >
                        填入示例提示词
                      </Button>
                    </Space>
                  }
                  required
                >
                  <TextArea
                    rows={3}
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder="请输入本次测试的提示词内容（必填，脚本不使用默认提示词）"
                  />
                </Form.Item>
              </>
            )}

            {/* 5. 高级超时与轮询参数折叠面板 */}
            <Collapse
              ghost
              items={[
                {
                  key: 'advanced',
                  label:
                    scope === 'video'
                      ? '高级网络超时与轮询间隔参数'
                      : scope === 'image'
                      ? '高级网络与下载超时参数'
                      : '高级网络超时参数',
                  children: (
                    <Row gutter={12}>
                      {scope === 'video' && (
                        <Col xs={12} md={6}>
                          <Form.Item label="轮询间隔 (秒)">
                            <InputNumber
                              min={1}
                              value={pollInterval}
                              onChange={(v) => setPollInterval(v || 5)}
                              style={{ width: '100%' }}
                            />
                          </Form.Item>
                        </Col>
                      )}
                      <Col xs={12} md={scope === 'video' ? 6 : 12}>
                        <Form.Item label="单次请求超时 (秒)">
                          <InputNumber
                            min={1}
                            value={requestTimeout}
                            onChange={(v) => setRequestTimeout(v || 120)}
                            style={{ width: '100%' }}
                          />
                        </Form.Item>
                      </Col>
                      {(scope === 'video' || scope === 'image') && (
                        <Col xs={12} md={scope === 'video' ? 6 : 12}>
                          <Form.Item label={scope === 'image' ? '图片下载超时 (秒)' : '视频下载超时 (秒)'}>
                            <InputNumber
                              min={5}
                              value={downloadTimeout}
                              onChange={(v) => setDownloadTimeout(v || 300)}
                              style={{ width: '100%' }}
                            />
                          </Form.Item>
                        </Col>
                      )}
                      {scope === 'video' && (
                        <Col xs={12} md={6}>
                          <Form.Item label="总等待上限 (秒)">
                            <InputNumber
                              min={5}
                              value={maxWait}
                              onChange={(v) => setMaxWait(v || 1800)}
                              style={{ width: '100%' }}
                            />
                          </Form.Item>
                        </Col>
                      )}
                    </Row>
                  ),
                },
              ]}
            />

            <div style={{ marginTop: 12, display: 'flex', gap: 12 }}>
              <Button
                type="primary"
                size="large"
                icon={<PlayCircleOutlined />}
                loading={starting || isRunning}
                onClick={handleStartTest}
                style={{ flex: 1 }}
              >
                {isRunning ? '正在执行测试…' : '立即发起测试'}
              </Button>
              {isRunning && (
                <Button
                  danger
                  size="large"
                  icon={<StopOutlined />}
                  onClick={handleCancelRun}
                >
                  中断测试 (保存已有结果)
                </Button>
              )}
            </div>
          </Form>
        </Card>
      </Col>

      {/* 右侧实时执行监控与即时解读面板 */}
      <Col xs={24} lg={11}>
        <Card
          style={{ borderRadius: 12, minHeight: 540 }}
          title={
            <Space>
              <span>实时执行控制台</span>
              {currentRun && (
                <Badge
                  status={
                    isRunning
                      ? 'processing'
                      : currentRun.status === 'completed'
                      ? 'success'
                      : 'error'
                  }
                  text={currentRun.status}
                />
              )}
            </Space>
          }
          extra={
            currentRun?.category &&
            currentRun?.folder && (
              <Button
                type="primary"
                ghost
                size="small"
                icon={<EyeOutlined />}
                onClick={() => onNavigateToResult(currentRun.category, currentRun.folder!)}
              >
                在结果解读查看完整报告
              </Button>
            )
          }
        >
          {!currentRun ? (
            <div style={{ padding: '80px 20px', textAlign: 'center', color: '#94a3b8' }}>
              <PlayCircleOutlined style={{ fontSize: 40, marginBottom: 12 }} />
              <div>在左侧选择配置与接口参数后，点击「立即发起测试」实时观察请求过程与 AI 诊断解读</div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* 实时核心指标小卡片 */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(3, 1fr)',
                  gap: 10,
                  background: '#f8fafc',
                  padding: 12,
                  borderRadius: 8,
                }}
              >
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    当前配置 / 模型
                  </Text>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>
                    {currentRun.profile_name} · {currentRun.model || liveReport?.model || '-'}
                  </div>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    实时耗时
                  </Text>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>
                    {liveReport?.elapsed_ms ? `${liveReport.elapsed_ms} ms` : '计时中…'}
                  </div>
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    TTFT / 服务端状态
                  </Text>
                  <div style={{ fontWeight: 600, fontSize: 13, color: '#1677ff' }}>
                    {liveReport?.ttft_ms != null
                      ? `TTFT: ${liveReport.ttft_ms} ms`
                      : liveReport?.last_status
                      ? `状态: ${liveReport.last_status}`
                      : '-'}
                  </div>
                </div>
              </div>

              {/* 实时流式文本预览 */}
              {liveReport?.response_text && (
                <Card
                  size="small"
                  title="流式响应文本实时输出"
                  style={{ background: '#f0fdf4', borderColor: '#bbf7d0' }}
                >
                  <div style={{ whiteSpace: 'pre-wrap', fontSize: 14 }}>
                    {liveReport.response_text}
                  </div>
                </Card>
              )}

              {/* 视频生成成功后直接内嵌播放 */}
              <MediaImages images={currentRun.detail?.images} />
              {currentRun.detail?.has_video && <C2paPanel key={currentRun.detail.id} category={currentRun.detail.category} folder={currentRun.detail.folder} initial={currentRun.detail.c2pa} />}
              {currentRun.detail?.has_video && currentRun.detail.video_url && (
                <Card
                  size="small"
                  title="生成视频预览 (video.mp4)"
                  extra={
                    <Space size={6} wrap>
                      {videoMeta &&
                        videoMeta.url === currentRun.detail.video_url &&
                        videoMeta.width > 0 && (
                          <Tag color="blue" style={{ margin: 0 }}>
                            分辨率: {formatVideoResolution(videoMeta.width, videoMeta.height)}
                          </Tag>
                        )}
                      {(liveReport?.request_body?.parameters?.resolution || liveReport?.request_body?.resolution ||
                        liveReport?.request_body?.size ||
                        liveReport?.resolution) && (
                        <Tag color="geekblue" style={{ margin: 0 }}>
                          请求规格:{' '}
                          {liveReport?.request_body?.parameters?.resolution || liveReport?.request_body?.resolution ||
                            liveReport?.request_body?.size ||
                            liveReport?.resolution}
                        </Tag>
                      )}
                      {videoMeta &&
                        videoMeta.url === currentRun.detail.video_url &&
                        Number.isFinite(videoMeta.duration) &&
                        videoMeta.duration > 0 && (
                          <Tag color="purple" style={{ margin: 0 }}>
                            时长: {videoMeta.duration.toFixed(1)}s
                          </Tag>
                        )}
                      {currentRun.detail.video_size_bytes > 0 && (
                        <Tag color="green" style={{ margin: 0 }}>
                          {(currentRun.detail.video_size_bytes / (1024 * 1024)).toFixed(2)} MB
                        </Tag>
                      )}
                    </Space>
                  }
                >
                  <video
                    controls
                    src={currentRun.detail.video_url}
                    onLoadedMetadata={(e) => {
                      const el = e.currentTarget;
                      setVideoMeta({
                        url: currentRun.detail!.video_url!,
                        width: el.videoWidth,
                        height: el.videoHeight,
                        duration: el.duration,
                      });
                    }}
                    style={{ width: '100%', maxHeight: 280, borderRadius: 8, background: '#000' }}
                  />
                </Card>
              )}

              {/* 终端日志流与 result.json 路径一键复制 */}
              <div>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    gap: 8,
                    marginBottom: 6,
                  }}
                >
                  <Text strong style={{ fontSize: 13 }}>
                    实时诊断输出日志：
                  </Text>
                  {resultJsonPath && (
                    <Button
                      type="primary"
                      size="small"
                      icon={<CopyOutlined />}
                      onClick={() => handleCopyPath(resultJsonPath)}
                    >
                      一键复制 result.json 路径
                    </Button>
                  )}
                </div>

                {resultJsonPath && (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 8,
                      background: '#eff6ff',
                      border: '1px solid #bfdbfe',
                      padding: '6px 10px',
                      borderRadius: 6,
                      marginBottom: 8,
                    }}
                  >
                    <code
                      style={{
                        fontSize: 12,
                        color: '#1e3a8a',
                        wordBreak: 'break-all',
                        flex: 1,
                      }}
                    >
                      {resultJsonPath}
                    </code>
                    <Button
                      size="small"
                      type="link"
                      icon={<CopyOutlined />}
                      onClick={() => handleCopyPath(resultJsonPath)}
                      style={{ padding: 0, flexShrink: 0 }}
                    >
                      复制给 Agent
                    </Button>
                  </div>
                )}

                <pre
                  style={{
                    marginTop: 0,
                    background: '#0f172a',
                    color: '#e2e8f0',
                    padding: 12,
                    borderRadius: 8,
                    maxHeight: 240,
                    overflowY: 'auto',
                    fontSize: 12,
                    lineHeight: 1.55,
                  }}
                >
                  {currentRun.logs.length > 0 ? currentRun.logs.join('\n') : '正在初始化子进程…'}
                </pre>
              </div>

              {/* 自动 AI 解读结果展示 */}
              {(currentRun.analysis || currentRun.detail?.analysis) && (
                <Card
                  size="small"
                  style={{ borderColor: '#91caff', background: '#f0f7ff' }}
                  title={
                    <Space>
                      <RobotOutlined style={{ color: '#1677ff' }} />
                      <span>AI 诊断解读报告</span>
                      <Tag color="blue">
                        {(currentRun.analysis || currentRun.detail?.analysis)?.analyzer_model}
                      </Tag>
                    </Space>
                  }
                >
                  <MarkdownViewer
                    content={(currentRun.analysis || currentRun.detail?.analysis)?.content || ''}
                  />
                </Card>
              )}
            </div>
          )}
        </Card>
      </Col>
    </Row>
  );
};
