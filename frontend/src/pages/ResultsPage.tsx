import { MediaImages } from '../components/MediaImages';
import { C2paPanel } from '../components/C2paPanel';
import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  VideoCameraOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  type ResultDetail,
  type ResultSummary,
  fetchResultDetail,
  fetchResults,
  removeResultFolder,
  streamResultAnalysis,
  triggerResultAnalysis,
} from '../api/client';
import { MarkdownViewer } from '../components/MarkdownViewer';
import type { WorkbenchPreset } from './WorkbenchPage';

const { Title, Text } = Typography;

// Gateway error fields may contain nested JSON rather than strings.
function errorFieldText(value: unknown): string {
  if (value == null) return '';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

interface ResultsPageProps {
  selectedTarget?: { category: string; folder: string } | null;
  onSelectTarget?: (target: { category: string; folder: string } | null) => void;
  onJumpToWorkbench: (preset: WorkbenchPreset) => void;
}

function outcomeTag(outcome: string) {
  if (outcome === 'completed') {
    return (
      <Tag color="success" icon={<CheckCircleOutlined />}>
        completed
      </Tag>
    );
  }
  if (outcome === 'incomplete' || outcome === 'submitted') {
    return (
      <Tag color="warning" icon={<ExclamationCircleOutlined />}>
        {outcome}
      </Tag>
    );
  }
  if (outcome === 'running') {
    return (
      <Tag color="processing" icon={<ClockCircleOutlined />}>
        running
      </Tag>
    );
  }
  return (
    <Tag color="error" icon={<CloseCircleOutlined />}>
      {outcome}
    </Tag>
  );
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatVideoResolution(width: number, height: number): string {
  if (!width || !height) return '';
  const shortSide = Math.min(width, height);
  let tier = '';
  if (shortSide >= 2000) tier = '4K';
  else if (shortSide >= 1000) tier = '1080p';
  else if (shortSide >= 680) tier = '720p';
  else if (shortSide >= 400) tier = '480p';
  return tier ? `${width} × ${height} (${tier})` : `${width} × ${height}`;
}

function extractPromptText(result: Record<string, any> | undefined): string | null {
  if (!result) return null;
  const reqBody = result.request_body;
  if (reqBody && typeof reqBody === 'object') {
    if (typeof reqBody.prompt === 'string' && reqBody.prompt.trim()) {
      return reqBody.prompt.trim();
    }
    if (typeof reqBody.input?.prompt === 'string') return reqBody.input.prompt;
    if (typeof reqBody.input === 'string' && reqBody.input.trim()) {
      return reqBody.input.trim();
    }
    if (Array.isArray(reqBody.content)) {
      const texts = reqBody.content
        .filter((item: any) => item && typeof item.text === 'string' && item.text.trim())
        .map((item: any) => item.text.trim());
      if (texts.length > 0) return texts.join('\n');
    }
    if (Array.isArray(reqBody.messages)) {
      const userMsg =
        reqBody.messages.find((m: any) => m?.role === 'user') || reqBody.messages[0];
      if (typeof userMsg?.content === 'string' && userMsg.content.trim()) {
        return userMsg.content.trim();
      }
    }
  }
  if (typeof result.prompt === 'string' && result.prompt.trim()) {
    return result.prompt.trim();
  }
  return null;
}

export const ResultsPage: React.FC<ResultsPageProps> = ({
  selectedTarget,
  onSelectTarget,
  onJumpToWorkbench,
}) => {
  const [items, setItems] = useState<ResultSummary[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  const [outcomeFilter, setOutcomeFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');

  const [activeItem, setActiveItem] = useState<{ category: string; folder: string } | null>(null);
  const [detail, setDetail] = useState<ResultDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [videoMeta, setVideoMeta] = useState<{
    url: string;
    width: number;
    height: number;
    duration: number;
  } | null>(null);

  // AI 解读状态
  const [analyzing, setAnalyzing] = useState(false);
  const [streamingMarkdown, setStreamingMarkdown] = useState<string>('');
  const stopStreamRef = useRef<(() => void) | null>(null);

  const loadList = async (preserveSelection = true) => {
    setLoadingList(true);
    try {
      const res = await fetchResults({
        category: categoryFilter === 'all' ? undefined : categoryFilter,
        outcome: outcomeFilter === 'all' ? undefined : outcomeFilter,
        search: searchQuery.trim() || undefined,
      });
      setItems(res.items);
      if (selectedTarget) {
        setActiveItem(selectedTarget);
      } else if (!preserveSelection || !activeItem) {
        if (res.items.length > 0) {
          setActiveItem({ category: res.items[0].category, folder: res.items[0].folder });
        } else {
          setActiveItem(null);
          setDetail(null);
        }
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '加载测试结果列表失败');
    } finally {
      setLoadingList(false);
    }
  };

  useEffect(() => {
    loadList();
  }, [categoryFilter, outcomeFilter]);

  useEffect(() => {
    if (selectedTarget) {
      setActiveItem(selectedTarget);
      onSelectTarget?.(null);
    }
  }, [selectedTarget]);

  useEffect(() => {
    if (!activeItem) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    setStreamingMarkdown('');
    fetchResultDetail(activeItem.category, activeItem.folder)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (!cancelled) {
          message.error(err?.response?.data?.detail || '读取结果详情失败');
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeItem?.category, activeItem?.folder]);

  useEffect(() => {
    return () => {
      stopStreamRef.current?.();
    };
  }, []);

  const handleAnalyze = () => {
    if (!detail) return;
    stopStreamRef.current?.();
    setAnalyzing(true);
    setStreamingMarkdown('');

    stopStreamRef.current = streamResultAnalysis(
      detail.category,
      detail.folder,
      {
        onDelta: (piece) => {
          setStreamingMarkdown((prev) => prev + piece);
        },
        onComplete: (record) => {
          setAnalyzing(false);
          setStreamingMarkdown('');
          setDetail((prev) => (prev ? { ...prev, analysis: record } : prev));
          loadList(true);
          message.success(`AI 诊断解读已完成（模型：${record.analyzer_model}）`);
        },
        onError: async () => {
          // 若 SSE 异常则回退到普通 POST 调用
          try {
            const fallbackRes = await triggerResultAnalysis(detail.category, detail.folder, true);
            setDetail(fallbackRes.detail);
            loadList(true);
            message.success('已完成诊断解读');
          } catch (err: any) {
            message.error(err?.response?.data?.detail || 'AI 解读失败，请检查解读模型配置');
          } finally {
            setAnalyzing(false);
          }
        },
      },
      true
    );
  };

  const handleDelete = async (category: string, folder: string) => {
    try {
      await removeResultFolder(category, folder);
      message.success('已删除该测试记录');
      if (activeItem?.category === category && activeItem?.folder === folder) {
        setActiveItem(null);
      }
      loadList(false);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '删除失败');
    }
  };

  const totalCount = items.length;
  const completedCount = items.filter((i) => i.outcome === 'completed').length;
  const failedCount = items.filter((i) => i.outcome !== 'completed').length;
  const videoCount = items.filter((i) => i.has_video).length;
  const analyzedCount = items.filter((i) => i.has_analysis).length;

  const requestsList: any[] = Array.isArray(detail?.result?.requests)
    ? detail!.result.requests
    : [];
  const maxDurationMs = Math.max(
    1,
    ...requestsList.map((r) => (typeof r.duration_ms === 'number' ? r.duration_ms : 0))
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 顶部汇总统计与过滤栏 */}
      <Card size="small" style={{ borderRadius: 12 }}>
        <Row gutter={[16, 12]} align="middle">
          <Col xs={12} sm={4}>
            <Statistic title="结果总数" value={totalCount} />
          </Col>
          <Col xs={12} sm={4}>
            <Statistic
              title="成功 (completed)"
              value={completedCount}
              valueStyle={{ color: '#16a34a' }}
            />
          </Col>
          <Col xs={12} sm={4}>
            <Statistic
              title="失败 / 异常"
              value={failedCount}
              valueStyle={{ color: failedCount > 0 ? '#dc2626' : undefined }}
            />
          </Col>
          <Col xs={12} sm={4}>
            <Statistic
              title="已下载视频 (MP4)"
              value={videoCount}
              valueStyle={{ color: '#2563eb' }}
            />
          </Col>
          <Col xs={12} sm={4}>
            <Statistic
              title="已生成 AI 解读"
              value={analyzedCount}
              valueStyle={{ color: '#7c3aed' }}
            />
          </Col>
          <Col xs={24} sm={4} style={{ textAlign: 'right' }}>
            <Button icon={<ReloadOutlined />} onClick={() => loadList(true)} loading={loadingList}>
              刷新列表
            </Button>
          </Col>
        </Row>

        <div
          style={{
            marginTop: 12,
            paddingTop: 12,
            borderTop: '1px solid #f0f0f0',
            display: 'flex',
            gap: 12,
            flexWrap: 'wrap',
          }}
        >
          <Select
            style={{ width: 180 }}
            value={categoryFilter}
            onChange={setCategoryFilter}
            options={[
              { value: 'all', label: '全部接口目录' },
              { value: 'chat-completions', label: 'chat-completions' },
              { value: 'responses', label: 'responses' },
              { value: 'openai-video', label: 'openai-video' },
              { value: 'seedance', label: 'seedance' },
              { value: 'images-generations', label: 'Images 图片生成' },
              { value: 'bailian-video', label: '百炼原生视频' },
              { value: 'volcengine-assets', label: '素材库 · volcengine-assets' },
            ]}
          />
          <Select
            style={{ width: 170 }}
            value={outcomeFilter}
            onChange={setOutcomeFilter}
            options={[
              { value: 'all', label: '全部执行状态' },
              { value: 'completed', label: 'completed (成功)' },
              { value: 'failed', label: 'failed (失败)' },
              { value: 'incomplete', label: 'incomplete (截断)' },
              { value: 'completed_download_failed', label: '下载失败' },
              { value: 'timeout', label: 'timeout (超时)' },
            ]}
          />
          <Input
            style={{ width: 320 }}
            allowClear
            placeholder="搜索模型名称、提示词、Task ID、错误码..."
            prefix={<SearchOutlined />}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onPressEnter={() => loadList(true)}
          />
          <Button type="primary" onClick={() => loadList(true)}>
            筛选
          </Button>
        </div>
      </Card>

      {/* 主体左右分栏：左侧历史记录列表，右侧详情与 AI 解读 */}
      <Row gutter={16}>
        <Col xs={24} md={8} lg={7}>
          <Card
            size="small"
            title={`测试历史记录 (${items.length})`}
            style={{ borderRadius: 12 }}
            styles={{
              body: {
                maxHeight: 'calc(100vh - 260px)',
                overflowY: 'auto',
                padding: 8,
              },
            }}
          >
            {items.length === 0 ? (
              <Empty description="暂无匹配的测试记录" style={{ margin: '40px 0' }} />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {items.map((item) => {
                  const isSelected =
                    activeItem?.category === item.category && activeItem?.folder === item.folder;
                  return (
                    <div
                      key={item.id}
                      onClick={() =>
                        setActiveItem({ category: item.category, folder: item.folder })
                      }
                      style={{
                        padding: '10px 12px',
                        borderRadius: 8,
                        cursor: 'pointer',
                        border: isSelected ? '2px solid #1677ff' : '1px solid #e2e8f0',
                        background: isSelected ? '#f0f7ff' : '#fff',
                        transition: 'all 0.15s',
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          marginBottom: 6,
                        }}
                      >
                        <Space size={4} wrap>
                          <Tag color="geekblue" style={{ marginRight: 0 }}>
                            {item.mode}
                          </Tag>
                          {outcomeTag(item.outcome)}
                        </Space>
                        <Space size={4}>
                          {item.has_video && (
                            <Tag color="blue" icon={<VideoCameraOutlined />} style={{ marginRight: 0 }}>
                              MP4
                            </Tag>
                          )}
                          {item.has_analysis && (
                            <Tag color="purple" icon={<RobotOutlined />} style={{ marginRight: 0 }}>
                              已解读
                            </Tag>
                          )}
                        </Space>
                      </div>

                      <div style={{ fontWeight: 600, fontSize: 13, color: '#0f172a' }}>
                        {item.model || '未指定模型'}{' '}
                        <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                          · {item.elapsed_ms != null ? `${(item.elapsed_ms / 1000).toFixed(2)}s` : '-'}
                        </Text>
                      </div>

                      {item.prompt_preview && (
                        <div
                          style={{
                            fontSize: 12,
                            color: '#475569',
                            marginTop: 4,
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                          }}
                        >
                          提示词：{item.prompt_preview}
                        </div>
                      )}

                      {item.error && (
                        <div
                          style={{
                            fontSize: 12,
                            color: '#dc2626',
                            marginTop: 4,
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                          }}
                        >
                          错误：{item.error}
                        </div>
                      )}

                      <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
                        {item.folder}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>
        </Col>

        {/* 右侧测试详情与 AI 智能诊断面板 */}
        <Col xs={24} md={16} lg={17}>
          {loadingDetail ? (
            <Card style={{ borderRadius: 12, textAlign: 'center', padding: 80 }}>
              <Spin size="large" tip="正在加载诊断详情..." />
            </Card>
          ) : !detail ? (
            <Card style={{ borderRadius: 12, textAlign: 'center', padding: 80 }}>
              <Empty description="请在左侧选择一条测试记录查看详情与 AI 诊断报告" />
            </Card>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* 1. 顶部操作与核心指标概览 */}
              <Card
                size="small"
                style={{ borderRadius: 12 }}
                title={
                  <Space wrap>
                    <Title level={5} style={{ margin: 0 }}>
                      {detail.category} / {detail.folder}
                    </Title>
                    {outcomeTag(detail.result.outcome)}
                    <Tag color="blue">{detail.result.mode === 'volcengine-assets' ? detail.result.action : detail.result.model || '未指定模型'}</Tag>
                  </Space>
                }
                extra={
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<RobotOutlined />}
                      loading={analyzing}
                      onClick={handleAnalyze}
                    >
                      {detail.analysis ? '重新 AI 解读' : 'AI 智能解读'}
                    </Button>
                    {detail.result.task_id && (
                      <Button
                        icon={<PlayCircleOutlined />}
                        onClick={() =>
                          onJumpToWorkbench({
                            api_type:
                              detail.result.mode === 'seedance' ? 'seedance' : detail.result.mode === 'bailian-video' ? 'bailian-video' : 'video',
                            task_id: detail.result.task_id,
                            video_request_format: detail.result.video_request_format || 'default',
                            video_query_api: detail.result.video_query_api === 'videos' ? 'videos' : 'generations',
                            action: 'query',
                            base_url: detail.result.base_url,
                          })
                        }
                      >
                        用此 Task ID 继续查询/下载
                      </Button>
                    )}
                    {!['volcengine-assets', 'benchmark'].includes(detail.result.mode) && <Button
                      icon={<ThunderboltOutlined />}
                      onClick={() => {
                        const reqBody = detail.result.request_body || {};
                        onJumpToWorkbench({
                          api_type:
                            detail.result.mode === 'openai-video'
                              ? 'video'
                              : detail.result.mode,
                          model: detail.result.model,
                          video_request_format: detail.result.video_request_format || 'default',
                          video_query_api: detail.result.video_query_api === 'videos' ? 'videos' : 'generations',
                          base_url: detail.result.base_url,
                          prompt:
                            reqBody.prompt ||
                            (typeof reqBody.input === 'string' ? reqBody.input : reqBody.input?.prompt) ||
                            reqBody.messages?.[0]?.content ||
                            reqBody.content?.[0]?.text ||
                            '',
                        });
                      }}
                    >
                      复用参数重测
                    </Button>}
                    <Popconfirm
                      title="确定永久删除该测试结果目录吗？"
                      onConfirm={() => handleDelete(detail.category, detail.folder)}
                    >
                      <Button danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                }
              >
                <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} bordered>
                  <Descriptions.Item label="result.json 路径" span={3}>
                    <Space wrap>
                      <code style={{ wordBreak: 'break-all' }}>
                        {detail.result_json_path || `${detail.folder_path}/result.json`}
                      </code>
                      <Button
                        size="small"
                        type="link"
                        icon={<CopyOutlined />}
                        onClick={() => {
                          const fullPath =
                            detail.result_json_path || `${detail.folder_path}/result.json`;
                          navigator.clipboard.writeText(fullPath);
                          message.success(`已复制：${fullPath}`);
                        }}
                      >
                        复制给 Agent
                      </Button>
                    </Space>
                  </Descriptions.Item>
                  <Descriptions.Item label="接口 Base URL">
                    <code>{detail.result.base_url}</code>
                  </Descriptions.Item>
                  <Descriptions.Item label="开始时间 (UTC)">
                    {detail.result.started_at}
                  </Descriptions.Item>
                  <Descriptions.Item label="完整测试耗时">
                    <Text strong>{detail.result.elapsed_ms} ms</Text>
                  </Descriptions.Item>
                  {detail.result.ttft_ms !== undefined && (
                    <Descriptions.Item label="首字延迟 (TTFT)">
                      <Text strong style={{ color: '#1677ff' }}>
                        {detail.result.ttft_ms != null ? `${detail.result.ttft_ms} ms` : '未收到'}
                      </Text>
                    </Descriptions.Item>
                  )}
                  {detail.result.create_ms !== undefined && (
                    <Descriptions.Item label="任务创建耗时">
                      {detail.result.create_ms} ms
                    </Descriptions.Item>
                  )}
                  {detail.result.completion_ms !== undefined && (
                    <Descriptions.Item label="首次观察完成耗时">
                      <Text strong style={{ color: '#1677ff' }}>
                        {detail.result.completion_ms} ms (
                        {(detail.result.completion_ms / 1000).toFixed(1)} s)
                      </Text>
                    </Descriptions.Item>
                  )}
                  {(detail.result.task_id || detail.result.response_id) && (
                    <Descriptions.Item label="任务 / 响应 ID" span={2}>
                      <code>{detail.result.task_id || detail.result.response_id}</code>
                    </Descriptions.Item>
                  )}
                  <Descriptions.Item label="服务端用量">
                    {['images/generations', 'bailian-video'].includes(detail.result.mode)
                      ? (detail.result.usage ? JSON.stringify(detail.result.usage) : '服务端未上报')
                      : detail.result.usage
                      ? `总计 ${detail.result.usage.total_tokens ?? '-'} (输出 ${
                          detail.result.usage.completion_tokens ??
                          detail.result.usage.output_tokens ??
                          '-'
                        })`
                      : '服务端未上报'}
                  </Descriptions.Item>
                  <Descriptions.Item label="使用提示词" span={3}>
                    {extractPromptText(detail.result) ? (
                      <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                        {extractPromptText(detail.result)}
                      </span>
                    ) : (
                      <Text type="secondary">
                        {detail.result.query_only
                          ? '（单任务查询模式，未携带创建提示词）'
                          : '未记录提示词'}
                      </Text>
                    )}
                  </Descriptions.Item>
                </Descriptions>
              </Card>

              {/* 2. AI 智能解读与根因诊断卡片 */}
              <Card
                size="small"
                style={{
                  borderRadius: 12,
                  border: '1px solid #91caff',
                  background: '#f8fbff',
                }}
                title={
                  <Space>
                    <RobotOutlined style={{ color: '#1677ff', fontSize: 18 }} />
                    <span style={{ fontWeight: 600 }}>test-results 智能诊断与结果解读</span>
                    {detail.analysis && (
                      <Tag color="blue">
                        解读引擎：{detail.analysis.analyzer_model} (
                        {detail.analysis.source === 'llm' ? 'OpenAI 兼容大模型' : '内置规则引擎'})
                      </Tag>
                    )}
                  </Space>
                }
              >
                {/* 自动预检与深层错误解包 */}
                <div style={{ marginBottom: 12 }}>
                  <Text strong style={{ fontSize: 13 }}>
                    系统预检指标与深层错误自动解包：
                  </Text>
                  <ul style={{ margin: '6px 0 0', paddingLeft: 20 }}>
                    {detail.digest.auto_findings.map((finding, idx) => (
                      <li key={idx} style={{ marginBottom: 4, color: '#1e293b' }}>
                        {finding}
                      </li>
                    ))}
                  </ul>
                </div>

                {detail.digest.extracted_errors.length > 0 && (
                  <Alert
                    type="error"
                    showIcon
                    style={{ marginBottom: 14 }}
                    message="解包后的网关/上游错误详情"
                    description={
                      <div style={{ fontSize: 13 }}>
                        {detail.digest.extracted_errors.map((err, i) => (
                          <div key={i} style={{ marginBottom: 6 }}>
                            <div>
                              <Tag color="red">
                                {err.phase || err.stage || 'error'}{' '}
                                {err.http_status ? `HTTP ${err.http_status}` : ''}
                              </Tag>
                              {err.request_error && <Text code>{err.request_error}</Text>}
                            </div>
                            {err.upstream_errors?.map((up, j) => (
                              <div
                                key={j}
                                style={{
                                  marginTop: 4,
                                  padding: '6px 10px',
                                  background: '#fff1f0',
                                  borderRadius: 6,
                                  fontFamily: 'monospace',
                                }}
                              >
                                {Boolean(up.code) && (
                                  <span>
                                    <strong>错误码:</strong> {errorFieldText(up.code)} |{' '}
                                  </span>
                                )}
                                {Boolean(up.param) && (
                                  <span>
                                    <strong>问题参数:</strong> {errorFieldText(up.param)} |{' '}
                                  </span>
                                )}
                                <span>
                                  <strong>原因:</strong> {errorFieldText(up.message || up.fail_reason)}
                                </span>
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    }
                  />
                )}

                {/* 大模型流式/持久化解读内容 */}
                {analyzing && streamingMarkdown ? (
                  <div
                    style={{
                      background: '#fff',
                      padding: 16,
                      borderRadius: 8,
                      border: '1px solid #e2e8f0',
                    }}
                  >
                    <Badge status="processing" text="AI 正在实时生成深度解读报告..." />
                    <MarkdownViewer content={streamingMarkdown} />
                  </div>
                ) : detail.analysis ? (
                  <div
                    style={{
                      background: '#fff',
                      padding: 16,
                      borderRadius: 8,
                      border: '1px solid #e2e8f0',
                    }}
                  >
                    <MarkdownViewer content={detail.analysis.content} />
                  </div>
                ) : (
                  <Alert
                    type="info"
                    showIcon
                    message="该测试结果尚未生成大模型详细解读报告"
                    description="点击右上角「AI 智能解读」按钮，即可调用已配置的 OpenAI 兼容大模型生成包含性能剖析、Token 计费核查与排障修复建议的完整报告（自动落盘至 analysis.md）。"
                  />
                )}
              </Card>

              <MediaImages images={detail.images} />
              {/* 3. 多媒体视频播放器或返回文本预览 */}
              {detail.has_video && <C2paPanel key={detail.id} category={detail.category} folder={detail.folder} initial={detail.c2pa} />}
              {(detail.has_video || detail.result.response_text !== undefined) && (
                <Card
                  size="small"
                  style={{ borderRadius: 12 }}
                  title={detail.has_video ? '已下载视频预览 (video.mp4)' : '接口返回文本内容'}
                  extra={
                    detail.has_video &&
                    detail.video_url && (
                      <Space size={6} wrap>
                        {videoMeta &&
                          videoMeta.url === detail.video_url &&
                          videoMeta.width > 0 && (
                            <Tag color="blue" style={{ margin: 0 }}>
                              分辨率: {formatVideoResolution(videoMeta.width, videoMeta.height)}
                            </Tag>
                          )}
                        {(detail.result?.request_body?.parameters?.resolution || detail.result?.request_body?.resolution ||
                          detail.result?.request_body?.size ||
                          detail.result?.resolution) && (
                          <Tag color="geekblue" style={{ margin: 0 }}>
                            请求规格:{' '}
                            {detail.result?.request_body?.parameters?.resolution || detail.result?.request_body?.resolution ||
                              detail.result?.request_body?.size ||
                              detail.result?.resolution}
                          </Tag>
                        )}
                        {videoMeta &&
                          videoMeta.url === detail.video_url &&
                          Number.isFinite(videoMeta.duration) &&
                          videoMeta.duration > 0 && (
                            <Tag color="purple" style={{ margin: 0 }}>
                              时长: {videoMeta.duration.toFixed(1)}s
                            </Tag>
                          )}
                        <Tag color="green" style={{ margin: 0 }}>
                          {formatBytes(detail.video_size_bytes)}
                        </Tag>
                        <Button
                          size="small"
                          type="primary"
                          ghost
                          icon={<DownloadOutlined />}
                          href={detail.video_url}
                          download={`${detail.folder}-video.mp4`}
                        >
                          下载 MP4
                        </Button>
                      </Space>
                    )
                  }
                >
                  {detail.has_video && detail.video_url && (
                    <video
                      controls
                      src={detail.video_url}
                      onLoadedMetadata={(e) => {
                        const el = e.currentTarget;
                        setVideoMeta({
                          url: detail.video_url!,
                          width: el.videoWidth,
                          height: el.videoHeight,
                          duration: el.duration,
                        });
                      }}
                      style={{
                        width: '100%',
                        maxHeight: 420,
                        borderRadius: 8,
                        background: '#000',
                      }}
                    />
                  )}
                  {detail.result.response_text !== undefined && (
                    <div
                      style={{
                        whiteSpace: 'pre-wrap',
                        background: '#f8fafc',
                        padding: 14,
                        borderRadius: 8,
                        fontSize: 14,
                        lineHeight: 1.65,
                      }}
                    >
                      {detail.result.response_text || '（空响应文本）'}
                    </div>
                  )}
                </Card>
              )}

              {/* 4. 请求时序瀑布表、summary.txt 与 result.json 检查器 */}
              <Card size="small" style={{ borderRadius: 12 }}>
                <Tabs
                  items={[
                    {
                      key: 'requests',
                      label: `各阶段 HTTP 请求时序 (${requestsList.length})`,
                      children: (
                        <Table
                          size="small"
                          rowKey={(_, idx) => String(idx)}
                          pagination={{ pageSize: 12 }}
                          dataSource={requestsList}
                          columns={[
                            {
                              title: '#',
                              width: 50,
                              render: (_, __, idx) => idx + 1,
                            },
                            {
                              title: '阶段 (Phase)',
                              dataIndex: 'phase',
                              width: 100,
                              render: (v) => <Tag color="geekblue">{v}</Tag>,
                            },
                            {
                              title: 'HTTP 状态',
                              dataIndex: 'http_status',
                              width: 100,
                              render: (code) =>
                                code ? (
                                  <Tag color={code >= 200 && code < 300 ? 'green' : 'red'}>
                                    HTTP {code}
                                  </Tag>
                                ) : (
                                  '-'
                                ),
                            },
                            {
                              title: '响应头耗时',
                              dataIndex: 'headers_ms',
                              width: 110,
                              render: (v) => (v != null ? `${v} ms` : '-'),
                            },
                            {
                              title: '首字节耗时',
                              dataIndex: 'first_body_byte_ms',
                              width: 110,
                              render: (v) => (v != null ? `${v} ms` : '-'),
                            },
                            {
                              title: '总耗时 (duration_ms)',
                              dataIndex: 'duration_ms',
                              render: (v) => {
                                const pct =
                                  typeof v === 'number'
                                    ? Math.min(100, Math.max(4, (v / maxDurationMs) * 100))
                                    : 0;
                                return (
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                    <div
                                      style={{
                                        width: 120,
                                        height: 8,
                                        background: '#f1f5f9',
                                        borderRadius: 4,
                                        overflow: 'hidden',
                                      }}
                                    >
                                      <div
                                        style={{
                                          width: `${pct}%`,
                                          height: '100%',
                                          background: v >= 1000 ? '#f59e0b' : '#1677ff',
                                        }}
                                      />
                                    </div>
                                    <span>{v != null ? `${v} ms` : '-'}</span>
                                  </div>
                                );
                              },
                            },
                          ]}
                        />
                      ),
                    },
                    {
                      key: 'summary',
                      label: '文本摘要 (summary.txt)',
                      children: (
                        <pre
                          style={{
                            background: '#0f172a',
                            color: '#f8fafc',
                            padding: 14,
                            borderRadius: 8,
                            overflowX: 'auto',
                            fontSize: 12,
                            lineHeight: 1.6,
                          }}
                        >
                          {detail.summary_text}
                        </pre>
                      ),
                    },
                    {
                      key: 'json',
                      label: '完整诊断数据 (result.json)',
                      children: (
                        <pre
                          style={{
                            background: '#0f172a',
                            color: '#f8fafc',
                            padding: 14,
                            borderRadius: 8,
                            maxHeight: 520,
                            overflowY: 'auto',
                            fontSize: 12,
                            lineHeight: 1.5,
                          }}
                        >
                          {JSON.stringify(detail.result, null, 2)}
                        </pre>
                      ),
                    },
                  ]}
                />
              </Card>
            </div>
          )}
        </Col>
      </Row>
    </div>
  );
};
