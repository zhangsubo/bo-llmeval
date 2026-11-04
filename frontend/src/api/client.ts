import axios from 'axios';

export interface ProfileConfig {
  id?: number;
  name: string;
  base_url: string;
  api_key?: string;
  api_key_masked?: string;
  has_key?: boolean;
  valid?: boolean;
}

export interface AnalyzerConfig {
  name: string;
  base_url: string;
  model: string;
  api_key?: string;
  api_key_masked?: string;
  has_key?: boolean;
  auto_analyze?: boolean;
  configured?: boolean;
  clear_key?: boolean;
}

export interface AssetConfig {
  id?: string;
  name?: string;
  endpoint: string;
  access_key?: string;
  secret_key?: string;
  has_credentials?: boolean;
  configured?: boolean;
  clear_credentials?: boolean;
}

export interface AppConfig {
  config_path: string;
  credential_warnings?: string[];
  profiles: ProfileConfig[];
  analyzer: AnalyzerConfig;
  assets?: AssetConfig;
  asset_profiles?: AssetConfig[];
  onepassword?: { account: string; vault: string };
}

export interface ExtractedError {
  stage?: string;
  error?: string;
  fail_reason?: string;
  request_index?: number;
  phase?: string;
  method?: string;
  url?: string;
  http_status?: number;
  request_error?: string;
  upstream_errors?: Array<{
    code?: unknown;
    gateway_code?: string;
    type?: string;
    param?: unknown;
    message?: unknown;
    fail_reason?: unknown;
  }>;
  response_body?: unknown;
  response_text?: string;
}

export interface DiagnosticDigest {
  mode: string;
  outcome: string;
  health_level: 'healthy' | 'warning' | 'error' | string;
  model: string;
  base_url?: string;
  started_at?: string;
  task_id?: string;
  response_id?: string;
  request_body?: Record<string, unknown>;
  response_text_preview?: string | null;
  timing_analysis: Record<string, any>;
  usage_analysis: Record<string, any>;
  extracted_errors: ExtractedError[];
  auto_findings: string[];
  returned_links_count: number;
  summary_text?: string;
}

export interface AnalysisRecord {
  analyzed_at?: string | null;
  source?: string;
  analyzer_name?: string;
  analyzer_model?: string;
  analyzer_base_url?: string | null;
  duration_ms?: number;
  usage?: Record<string, unknown> | null;
  content: string;
  metrics_digest?: DiagnosticDigest;
}

export interface ResultSummary {
  id: string;
  category: string;
  folder: string;
  started_at: string;
  mode: string;
  outcome: string;
  health_level: string;
  model: string;
  base_url: string;
  elapsed_ms?: number;
  request_ms?: number;
  ttft_ms?: number | null;
  create_ms?: number;
  completion_ms?: number;
  query_completion_ms?: number;
  task_id?: string;
  response_id?: string;
  action?: string;
  query_only?: boolean;
  stream?: boolean;
  usage?: Record<string, any> | null;
  error?: string;
  extracted_errors: ExtractedError[];
  auto_findings: string[];
  prompt_preview: string;
  response_preview: string;
  references: Array<{ type: string; role: string; url: string }>;
  has_video: boolean;
  video_size_bytes: number;
  has_analysis: boolean;
  analysis_meta?: {
    analyzed_at?: string;
    source?: string;
    analyzer_model?: string;
    analyzer_name?: string;
  };
}

export interface ResultDetail {
  c2pa?: import('../components/C2paPanel').C2paReport | null;
  images?: { local_url: string; file: string; bytes: number; revised_prompt?: string }[];
  id: string;
  category: string;
  folder: string;
  folder_path: string;
  result_json_path?: string;
  result: Record<string, any>;
  summary_text: string;
  digest: DiagnosticDigest;
  analysis: AnalysisRecord | null;
  has_video: boolean;
  video_url: string | null;
  video_size_bytes: number;
}

export interface TestRunPayload {
  c2pa_check?: boolean;
  image_count?: number;
  quality?: string;
  background?: string;
  moderation?: string;
  response_format?: string;
  style?: string;
  user?: string;
  negative_prompt?: string;
  seed?: number;
  prompt_extend?: boolean;
  shot_type?: string;
  profile_id?: number | 'custom';
  custom_base_url?: string;
  custom_api_key?: string;
  api_type: 'chat/completions' | 'responses' | 'video' | 'seedance' | 'images/generations' | 'bailian-video';
  model?: string;
  prompt?: string;
  stream?: boolean;
  task_id?: string;
  action?: 'create' | 'query' | 'list' | 'delete';
  wait?: boolean;
  video_query_api?: 'generations' | 'videos';
  video_request_format?: 'default' | 'alibaba';
  resolution?: string;
  size?: string;
  seconds?: number;
  duration?: number;
  ratio?: string;
  generate_audio?: boolean;
  watermark?: boolean;
  content_array?: boolean;
  reference_image?: string[];
  reference_video?: string[];
  reference_audio?: string[];
  first_frame?: string;
  last_frame?: string;
  page_num?: number;
  page_size?: number;
  filter_status?: string;
  filter_model?: string;
  filter_task_ids?: string[];
  poll_interval?: number;
  request_timeout?: number;
  download_timeout?: number;
  max_wait?: number;
  auto_analyze?: boolean;
}

export interface TestRunSnapshot {
  run_id: string;
  status: string;
  api_type: string;
  category: string;
  folder: string | null;
  folder_path?: string | null;
  result_json_path?: string | null;
  result_id: string | null;
  profile_name: string;
  base_url: string;
  model: string;
  auto_analyze: boolean;
  started_at: string;
  finished_at: string | null;
  exit_code: number | null;
  logs: string[];
  live_report: Record<string, any> | null;
  analysis: AnalysisRecord | null;
  detail?: ResultDetail;
  error: string | null;
}

export async function fetchConfig(): Promise<AppConfig> {
  const { data } = await axios.get<AppConfig>('/api/config');
  return data;
}

export async function updateConfig(payload: {
  profiles?: ProfileConfig[];
  analyzer?: AnalyzerConfig;
  assets?: AssetConfig;
  asset_profiles?: AssetConfig[];
  onepassword?: { account: string; vault: string };
}): Promise<AppConfig> {
  const { data } = await axios.put<AppConfig>('/api/config', payload);
  return data;
}

export async function testAnalyzerConnection(payload: {
  base_url?: string;
  api_key?: string;
  model?: string;
}): Promise<{ status: string; model: string; base_url: string; reply: string; duration_ms: number }> {
  const { data } = await axios.post('/api/analyzer/test', payload);
  return data;
}

export async function fetchResults(params?: {
  category?: string;
  outcome?: string;
  search?: string;
}): Promise<{ total: number; items: ResultSummary[] }> {
  const { data } = await axios.get<{ total: number; items: ResultSummary[] }>('/api/results', {
    params,
  });
  return data;
}

export async function fetchResultDetail(category: string, folder: string): Promise<ResultDetail> {
  const { data } = await axios.get<ResultDetail>(`/api/results/${category}/${folder}`);
  return data;
}

export async function removeResultFolder(category: string, folder: string): Promise<void> {
  await axios.delete(`/api/results/${category}/${folder}`);
}

export async function triggerResultAnalysis(
  category: string,
  folder: string,
  allowFallback = true
): Promise<{ analysis: AnalysisRecord; detail: ResultDetail }> {
  const { data } = await axios.post(`/api/results/${category}/${folder}/analyze`, {
    allow_fallback: allowFallback,
    stream: false,
  });
  return data;
}

export function streamResultAnalysis(
  category: string,
  folder: string,
  callbacks: {
    onDelta: (piece: string) => void;
    onComplete: (record: AnalysisRecord) => void;
    onError: (errMsg: string) => void;
  },
  allowFallback = true
): () => void {
  const url = `/api/results/${encodeURIComponent(category)}/${encodeURIComponent(folder)}/analyze/stream?allow_fallback=${allowFallback}`;
  const es = new EventSource(url);

  es.addEventListener('delta', (ev) => {
    try {
      const parsed = JSON.parse((ev as MessageEvent).data);
      if (parsed.text) callbacks.onDelta(parsed.text);
    } catch {
      // ignore malformed chunk
    }
  });

  es.addEventListener('complete', (ev) => {
    try {
      const record = JSON.parse((ev as MessageEvent).data);
      callbacks.onComplete(record);
    } finally {
      es.close();
    }
  });

  es.addEventListener('error', (ev) => {
    try {
      const dataStr = (ev as MessageEvent).data;
      if (dataStr) {
        const parsed = JSON.parse(dataStr);
        callbacks.onError(parsed.error || 'AI 解读请求失败');
      } else {
        callbacks.onError('AI 解读流连接中断');
      }
    } catch {
      callbacks.onError('AI 解读发生未知错误');
    } finally {
      es.close();
    }
  });

  return () => es.close();
}

export async function startTestRun(payload: TestRunPayload): Promise<TestRunSnapshot> {
  const { data } = await axios.post<TestRunSnapshot>('/api/tests/run', payload);
  return data;
}

export async function fetchTestRun(runId: string): Promise<TestRunSnapshot> {
  const { data } = await axios.get<TestRunSnapshot>(`/api/tests/runs/${runId}`);
  return data;
}

export async function cancelTestRun(runId: string): Promise<TestRunSnapshot> {
  const { data } = await axios.post<TestRunSnapshot>(`/api/tests/runs/${runId}/cancel`);
  return data;
}

export function subscribeTestRunEvents(
  runId: string,
  callbacks: {
    onUpdate: (snapshot: TestRunSnapshot) => void;
    onDone: (snapshot: TestRunSnapshot) => void;
    onError?: () => void;
  }
): () => void {
  const es = new EventSource(`/api/tests/runs/${encodeURIComponent(runId)}/events`);
  es.addEventListener('update', (ev) => {
    try {
      callbacks.onUpdate(JSON.parse((ev as MessageEvent).data));
    } catch {
      // ignore
    }
  });
  es.addEventListener('done', (ev) => {
    try {
      callbacks.onDone(JSON.parse((ev as MessageEvent).data));
    } finally {
      es.close();
    }
  });
  es.onerror = () => {
    callbacks.onError?.();
    es.close();
  };
  return () => es.close();
}

export interface AssetRunRequest {
  use_saved_credentials?: boolean;
  asset_profile_id?: string;
  endpoint: string;
  auth: 'aksk' | 'bearer';
  access_key?: string;
  secret_key?: string;
  security_token?: string;
  api_key?: string;
  byted_token?: string;
  action: string;
  region: string;
  payload: Record<string, unknown>;
  wait: boolean;
  timeout: number;
  max_wait: number;
  poll_interval: number;
  confirm_delete?: boolean;
}

export interface AssetRunResponse {
  report: {
    action: string;
    success: boolean;
    error?: string;
    result?: Record<string, unknown>;
    asset_uri?: string;
    group_id?: string;
    elapsed_ms: number;
    requests: Array<Record<string, unknown>>;
  };
  category: string;
  folder: string;
  validation_session?: { h5_link: string; byted_token: string };
}

export async function runAssetRequest(payload: AssetRunRequest): Promise<AssetRunResponse> {
  const { data } = await axios.post<AssetRunResponse>('/api/assets/run', payload, {
    timeout: (payload.timeout + (payload.wait ? payload.max_wait : 0) + 15) * 1000,
  });
  return data;
}
