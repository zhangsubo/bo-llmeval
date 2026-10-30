import axios from 'axios';

interface PageData<T> {
  items?: T[];
  total?: number;
}

/** Read every server-side page while respecting the API's maximum page size. */
export async function fetchAllPages<T>(
  fetchPage: (params: { skip: number; limit: number }) => Promise<{ data: PageData<T> }>,
  pageSize = 100,
): Promise<T[]> {
  const items: T[] = [];
  let skip = 0;
  let total = Number.POSITIVE_INFINITY;
  while (skip < total) {
    const response = await fetchPage({ skip, limit: pageSize });
    const page = response.data.items || [];
    if (page.length === 0) break;
    items.push(...page);
    skip += page.length;
    total = response.data.total ?? skip;
    if (page.length < pageSize) break;
  }
  return items;
}

const api = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
});

// === Suppliers ===
export const suppliersApi = {
  list: (params?: { skip?: number; limit?: number; status?: string; keyword?: string }) =>
    api.get('/suppliers', { params }),
  get: (id: string) => api.get(`/suppliers/${id}`),
  create: (data: { name: string; short_name: string; type: string; remark?: string }) =>
    api.post('/suppliers', data),
  update: (id: string, data: Record<string, unknown>) => api.patch(`/suppliers/${id}`, data),
};

// === Connections ===
export const connectionsApi = {
  listAll: (params?: { skip?: number; limit?: number; supplier_id?: string; status?: string; keyword?: string; include_archived?: boolean }) =>
    api.get('/connections', { params }),
  list: (supplierId: string, params?: { skip?: number; limit?: number; status?: string; keyword?: string }) =>
    api.get(`/suppliers/${supplierId}/connections`, { params }),
  get: (id: string) => api.get(`/connections/${id}`),
  create: (supplierId: string, data: {
    name: string;
    base_url: string;
    api_key: string;
    region?: string;
    service_tier?: string;
    tls_skip_verify?: boolean;
    tls_skip_verify_confirm?: boolean;
    timeouts?: Record<string, unknown>;
    limits?: Record<string, unknown>;
    remark?: string;
  }) =>
    api.post(`/suppliers/${supplierId}/connections`, data),
  update: (id: string, data: Record<string, unknown>) => api.patch(`/connections/${id}`, data),
  delete: (id: string) => api.delete(`/connections/${id}`),
  test: (id: string) => api.post(`/connections/${id}/test`),
  syncModels: (id: string) => api.post(`/connections/${id}/sync-models`),
  replaceKey: (id: string, newApiKey: string) =>
    api.post(`/connections/${id}/replace-key`, { new_api_key: newApiKey }),
};

// === Model Deployments ===
export const modelDeploymentsApi = {
  list: (params?: { connection_id?: string; supplier_id?: string; status?: string; keyword?: string; skip?: number; limit?: number }) =>
    api.get('/model-deployments', { params }),
  get: (id: string) => api.get(`/model-deployments/${id}`),
  update: (id: string, data: Record<string, unknown>) => api.patch(`/model-deployments/${id}`, data),
  createModel: (connectionId: string, data: { model_id: string; display_name?: string }) =>
    api.post(`/connections/${connectionId}/models`, data),
};

// === Comparison Groups ===
export const comparisonGroupsApi = {
  list: (params?: { skip?: number; limit?: number; keyword?: string }) =>
    api.get('/comparison-groups', { params }),
  get: (id: string) => api.get(`/comparison-groups/${id}`),
  create: (data: { name: string; default_tokenizer_id?: string; remark?: string }) => api.post('/comparison-groups', data),
  update: (id: string, data: Record<string, unknown>) => api.patch(`/comparison-groups/${id}`, data),
  listMembers: (id: string, params?: { skip?: number; limit?: number }) => api.get(`/comparison-groups/${id}/members`, { params }),
  addMember: (groupId: string, deploymentId: string) =>
    api.post(`/comparison-groups/${groupId}/members`, { model_deployment_id: deploymentId }),
  removeMember: (groupId: string, deploymentId: string) =>
    api.delete(`/comparison-groups/${groupId}/members/${deploymentId}`),
};

// === Prices ===
export const pricesApi = {
  list: (deploymentId: string, params?: { skip?: number; limit?: number }) =>
    api.get(`/model-deployments/${deploymentId}/prices`, { params }),
  create: (deploymentId: string, data: { input_token_price: string | number; output_token_price: string | number; cached_token_price?: string | number; unit?: string; currency?: string; exchange_rate: string | number }) =>
    api.post(`/model-deployments/${deploymentId}/prices`, data),
  getLatest: (deploymentId: string) => api.get(`/model-deployments/${deploymentId}/prices/latest`),
};

// === Tokenizers ===
export const tokenizersApi = {
  list: (params?: { skip?: number; limit?: number; status?: string; keyword?: string }) =>
    api.get('/tokenizers', { params }),
  get: (id: string) => api.get(`/tokenizers/${id}`),
  create: (data: { name: string; source: string; resource_id?: string }) =>
    api.post('/tokenizers', data),
  download: (id: string) => api.post(`/tokenizers/${id}/download`),
  upload: (id: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return api.post(`/tokenizers/${id}/upload`, form, { headers: { 'Content-Type': 'multipart/form-data' } });
  },
  delete: (id: string) => api.delete(`/tokenizers/${id}`),
};

// === Datasets ===
export const datasetsApi = {
  list: (params?: { skip?: number; limit?: number; kind?: string; keyword?: string }) =>
    api.get('/datasets', { params }),
  get: (id: string) => api.get(`/datasets/${id}`),
  create: (data: { name: string; kind: string }) => api.post('/datasets', data),
  versions: (datasetId: string, params?: { skip?: number; limit?: number }) =>
    api.get(`/datasets/${datasetId}/versions`, { params }),
  listVersions: (params?: { skip?: number; limit?: number; dataset_id?: string; version_id?: string; status?: string; keyword?: string }) =>
    api.get('/dataset-versions', { params }),
  createVersion: (datasetId: string, data: Record<string, unknown>) =>
    api.post(`/datasets/${datasetId}/versions`, data),
  uploadVersion: (datasetId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return api.post(`/datasets/${datasetId}/versions/upload`, form, { headers: { 'Content-Type': 'multipart/form-data' } });
  },
  builtinCatalog: () => api.get('/datasets/builtins/catalog'),
  downloadBuiltin: (key: string, force = false) =>
    api.post(`/datasets/builtins/${encodeURIComponent(key)}/download`, null, { params: { force } }),
};

// === Templates ===
export const templatesApi = {
  list: (params?: { skip?: number; limit?: number; is_builtin?: boolean; keyword?: string }) =>
    api.get('/templates', { params }),
  create: (data: { name: string; is_builtin?: boolean }) => api.post('/templates', data),
  versions: (templateId: string, params?: { skip?: number; limit?: number }) =>
    api.get(`/templates/${templateId}/versions`, { params }),
  listVersions: (params?: { skip?: number; limit?: number; template_id?: string; version_id?: string; keyword?: string }) =>
    api.get('/template-versions', { params }),
  createVersion: (templateId: string, data: Record<string, unknown>) =>
    api.post(`/templates/${templateId}/versions`, data),
  copy: (id: string, newName: string) => api.post(`/templates/${id}/copy`, { new_name: newName }),
};

// === Tasks ===
export const tasksApi = {
  list: (params?: {
    skip?: number; limit?: number; status?: string; keyword?: string; supplier_id?: string;
    model_deployment_id?: string; comparison_group_id?: string; template_id?: string;
    template_version_id?: string; test_type?: string; created_from?: string; created_to?: string;
  }) =>
    api.get('/tasks', { params }),
  get: (id: string) => api.get(`/tasks/${id}`),
  create: (data: Record<string, unknown>) => api.post('/tasks', data),
  update: (id: string, data: Record<string, unknown>) => api.patch(`/tasks/${id}`, data),
  submit: (id: string) => api.post(`/tasks/${id}/submit`),
  cancel: (id: string) => api.post(`/tasks/${id}/cancel`),
  retest: (id: string) => api.post(`/tasks/${id}/retest`),
  listNotes: (id: string) => api.get(`/tasks/${id}/notes`),
  createNote: (id: string, body: string) => api.post(`/tasks/${id}/notes`, { body }),
};

// === Runs ===
export const runsApi = {
  listByTask: (taskId: string, params?: { skip?: number; limit?: number }) =>
    api.get(`/tasks/${taskId}/runs`, { params }),
  get: (id: string) => api.get(`/runs/${id}`),
  listRequests: (runId: string, params?: { skip?: number; limit?: number }) =>
    api.get(`/runs/${runId}/requests`, { params }),
  getRequest: (runId: string, requestId: number) => api.get(`/runs/${runId}/requests/${requestId}`),
  metrics: (runId: string) => api.get(`/runs/${runId}/metrics`),
  cancel: (runId: string) => api.post(`/runs/${runId}/cancel`),
  retry: (runId: string) => api.post(`/runs/${runId}/retry`),
  exportCsv: (runId: string) => api.get(`/runs/${runId}/export`),
  listNotes: (runId: string) => api.get(`/runs/${runId}/notes`),
  createNote: (runId: string, body: string) => api.post(`/runs/${runId}/notes`, { body }),
};

export const comparisonsApi = {
  compare: (runIds: string[]) => api.post('/comparisons', { run_ids: runIds }),
};

// === Monitoring ===
export const monitoringApi = {
  dashboard: () => api.get('/admin/dashboard'),
  taskStatus: (taskId: string) => api.get(`/tasks/${taskId}/status`),
};

export const reportsApi = {
  generate: (taskId: string, idempotencyKey?: string) => api.post(`/tasks/${taskId}/report`, null, {
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  }),
  list: (taskId: string, params?: { skip?: number; limit?: number; status?: string; created_from?: string; created_to?: string }) => api.get(`/tasks/${taskId}/reports`, { params }),
  listAll: (params?: { skip?: number; limit?: number; task_id?: string; supplier_id?: string; status?: string; created_from?: string; created_to?: string }) => api.get('/reports', { params }),
  get: (reportId: string) => api.get(`/reports/${reportId}`),
  exportJson: (runId: string) => api.get(`/runs/${runId}/export-json`),
  exportCsv: (runId: string) => api.get(`/runs/${runId}/export`),
};

// === Admin ===
export const adminApi = {
  dashboard: () => api.get('/admin/dashboard'),
  health: () => api.get('/admin/health'),
};

// === Settings ===
export const settingsApi = {
  list: () => api.get('/settings'),
  update: (key: string, value: unknown) => api.patch(`/settings/${key}`, { value }),
  getWhitelist: () => api.get('/settings/whitelist'),
  updateWhitelist: (data: { domains: string[]; cidrs: string[]; confirmation: string }) =>
    api.put('/settings/whitelist', data),
};

// === History cleanup ===
export interface CleanupPreviewRequest {
  cutoff_at: string;
  statuses: string[];
}

export interface CleanupExecuteRequest {
  confirmation: string;
}

export const historyCleanupApi = {
  preview: (payload: CleanupPreviewRequest) => api.post('/admin/history-cleanup/preview', payload),
  createBatch: (payload: CleanupPreviewRequest) => api.post('/admin/history-cleanup/batches', payload),
  getBatch: (batchId: string) => api.get(`/admin/history-cleanup/batches/${batchId}`),
  execute: (batchId: string, confirmation: string) =>
    api.post(`/admin/history-cleanup/batches/${batchId}/execute`, { confirmation } satisfies CleanupExecuteRequest),
  retry: (batchId: string, confirmation: string) =>
    api.post(`/admin/history-cleanup/batches/${batchId}/retry`, { confirmation } satisfies CleanupExecuteRequest),
};

export default api;
