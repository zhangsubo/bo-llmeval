import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Input, List, Pagination, Select, Space, Tag, Typography } from 'antd';
import { DownloadOutlined, FileSearchOutlined, LinkOutlined } from '@ant-design/icons';
import { fetchAllPages, reportsApi, runsApi, suppliersApi, tasksApi } from '../api';

interface Report { id: string; version: number; status: string; summary_path?: string | null; details_path?: string | null; created_at: string; }
interface TaskOption { id: string; name?: string | null; status: string; }
interface RunOption { id: string; seq: number; status: string; model_deployment_id?: string | null; }
interface SupplierOption { id: string; name: string; }

export default function ReportsPage() {
  const [taskId, setTaskId] = useState('');
  const [allMode, setAllMode] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>();
  const [supplierId, setSupplierId] = useState<string>();
  const [createdFrom, setCreatedFrom] = useState('');
  const [createdTo, setCreatedTo] = useState('');
  const [tasks, setTasks] = useState<TaskOption[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierOption[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [runs, setRuns] = useState<RunOption[]>([]);
  const [reportPage, setReportPage] = useState(1);
  const [reportPageSize, setReportPageSize] = useState(20);
  const [reportTotal, setReportTotal] = useState(0);
  const [runPage, setRunPage] = useState(1);
  const [runPageSize, setRunPageSize] = useState(20);
  const [runTotal, setRunTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const generationKeys = useRef(new Map<string, string>());
  const taskSearchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    Promise.all([
      fetchAllPages<TaskOption>(params => tasksApi.list(params)),
      fetchAllPages<SupplierOption>(params => suppliersApi.list(params)),
    ])
      .then(([taskItems, supplierItems]) => {
        setTasks(taskItems);
        setSuppliers(supplierItems);
      })
      .catch(() => setError('任务列表读取失败'));
  }, []);

  useEffect(() => () => {
    if (taskSearchTimer.current) clearTimeout(taskSearchTimer.current);
  }, []);

  const searchTasks = (value: string) => {
    if (taskSearchTimer.current) clearTimeout(taskSearchTimer.current);
    if (!value.trim()) return;
    taskSearchTimer.current = setTimeout(() => {
      void tasksApi.list({ skip: 0, limit: 100, keyword: value.trim() })
        .then(response => {
          setTasks(current => {
            const byId = new Map(current.map(item => [item.id, item]));
            (response.data.items || []).forEach((item: TaskOption) => byId.set(item.id, item));
            return [...byId.values()];
          });
        })
        .catch(() => setError('任务搜索失败'));
    }, 250);
  };

  const searchSuppliers = (value: string) => {
    if (!value.trim()) return;
    void suppliersApi.list({ skip: 0, limit: 100, keyword: value.trim() })
      .then(response => {
        setSuppliers(current => {
          const byId = new Map(current.map(item => [item.id, item]));
          (response.data.items || []).forEach((item: SupplierOption) => byId.set(item.id, item));
          return [...byId.values()];
        });
      })
      .catch(() => setError('供应商搜索失败'));
  };

  const load = async (
    selectedTaskId = taskId,
    listAll = allMode,
    reportStatus = statusFilter,
    nextReportPage = reportPage,
    nextReportPageSize = reportPageSize,
    nextRunPage = runPage,
    nextRunPageSize = runPageSize,
    reportSupplierId = supplierId,
    reportCreatedFrom = createdFrom,
    reportCreatedTo = createdTo,
  ) => {
    if (!selectedTaskId.trim() && !listAll) { setError('请选择任务或切换到全部报告'); return; }
    setLoading(true); setError('');
    try {
      const dateParams = {
        supplier_id: reportSupplierId,
        created_from: reportCreatedFrom ? new Date(reportCreatedFrom).toISOString() : undefined,
        created_to: reportCreatedTo ? new Date(reportCreatedTo).toISOString() : undefined,
      };
      const [reportResponse, runResponse] = listAll
        ? [await reportsApi.listAll({ skip: (nextReportPage - 1) * nextReportPageSize, limit: nextReportPageSize, status: reportStatus, ...dateParams }), null]
        : await Promise.all([
          reportsApi.list(selectedTaskId.trim(), { skip: (nextReportPage - 1) * nextReportPageSize, limit: nextReportPageSize, status: reportStatus, created_from: dateParams.created_from, created_to: dateParams.created_to }),
          runsApi.listByTask(selectedTaskId.trim(), { skip: (nextRunPage - 1) * nextRunPageSize, limit: nextRunPageSize }),
        ]);
      setReports(reportResponse.data.items || []);
      setReportTotal(reportResponse.data.total || 0);
      setRuns(runResponse?.data.items || []);
      setRunTotal(runResponse?.data.total || 0);
    }
    catch (e: any) { setError(e.response?.data?.error?.message || '报告读取失败'); setReports([]); setRuns([]); }
    finally { setLoading(false); }
  };

  const exportRun = async (run: RunOption, format: 'csv' | 'json') => {
    try {
      const response = format === 'csv'
        ? await reportsApi.exportCsv(run.id)
        : await reportsApi.exportJson(run.id);
      const path = response.data?.path;
      if (!path) throw new Error('导出文件未生成');
      window.open(fileUrl(path), '_blank', 'noopener,noreferrer');
    } catch (e: any) {
      setError(e.response?.data?.error?.message || e.message || '导出失败');
    }
  };

  const generate = async () => {
    const selectedTaskId = taskId.trim();
    if (!selectedTaskId) { setError('请选择或输入 Task ID'); return; }
    if (loading) return;
    setLoading(true); setError('');
    try {
      let key = generationKeys.current.get(selectedTaskId);
      if (!key) {
        key = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
        generationKeys.current.set(selectedTaskId, key);
      }
      await reportsApi.generate(selectedTaskId, key);
      // A later deliberate click should create the next immutable version;
      // retries after a lost response keep the same key until this succeeds.
      generationKeys.current.delete(selectedTaskId);
      await load();
    }
    catch (e: any) { setError(e.response?.data?.error?.message || '报告生成失败'); }
    finally { setLoading(false); }
  };

  const fileUrl = (path?: string | null) => path ? `/api/v1/reports/files/${encodeURIComponent(path.split('/').pop() || '')}` : '';
  return <div>
    <Typography.Title level={3}><FileSearchOutlined /> 报告版本</Typography.Title>
    <Card>
      <Space style={{ width: '100%', marginBottom: 12 }} wrap>
        <Select
          showSearch allowClear style={{ minWidth: 360 }} value={allMode ? '__all__' : (taskId || undefined)}
          placeholder="选择任务"
          optionFilterProp="label"
          onSearch={searchTasks}
          options={[{ value: '__all__', label: '全部报告' }, ...tasks.map(task => ({ value: task.id, label: `${task.name || task.id} (${task.status})` }))]}
          onChange={value => {
            setReportPage(1); setRunPage(1);
            if (value === '__all__') { setAllMode(true); setTaskId(''); return void load('', true, statusFilter, 1, reportPageSize, 1, runPageSize); }
            const next = value || '';
            setAllMode(false); setTaskId(next); setSupplierId(undefined);
            if (next) void load(next, false, statusFilter, 1, reportPageSize, 1, runPageSize);
          }}
        />
        <Select allowClear placeholder="报告状态" style={{ width: 120 }} value={statusFilter} onChange={value => { setStatusFilter(value); setReportPage(1); if (allMode) void load('', true, value, 1, reportPageSize, runPage, runPageSize); else if (taskId) void load(taskId, false, value, 1, reportPageSize, runPage, runPageSize); }} options={[{ value: 'generating', label: '生成中' }, { value: 'completed', label: '已完成' }, { value: 'failed', label: '失败' }]} />
        <Select allowClear showSearch optionFilterProp="label" disabled={!allMode} onSearch={searchSuppliers} placeholder="供应商（全部报告）" style={{ width: 170 }} value={supplierId} onChange={value => { setSupplierId(value); setReportPage(1); if (allMode) void load('', true, statusFilter, 1, reportPageSize, runPage, runPageSize, value); }} options={suppliers.map(item => ({ value: item.id, label: item.name }))} />
        <Input type="datetime-local" value={createdFrom} onChange={event => setCreatedFrom(event.target.value)} aria-label="报告生成起点" />
        <Input type="datetime-local" value={createdTo} onChange={event => setCreatedTo(event.target.value)} aria-label="报告生成终点" />
      </Space>
      <Space.Compact style={{ width: '100%' }}>
        <Input value={taskId} onChange={event => { setTaskId(event.target.value); setAllMode(false); }} placeholder="也可输入 Task UUID" onPressEnter={() => void load()} />
        <Button onClick={() => void load()} loading={loading}>查询</Button>
        <Button type="primary" onClick={generate} loading={loading}>生成新版本</Button>
      </Space.Compact>
    </Card>
    {error && <Alert style={{ marginTop: 16 }} type="error" message={error} />}
    <Card style={{ marginTop: 16 }}>
      <List loading={loading} locale={{ emptyText: taskId || allMode ? '暂无报告版本' : '选择任务或全部报告后查询' }} dataSource={reports} renderItem={report => <List.Item actions={[report.summary_path && <a key="summary" href={fileUrl(report.summary_path)} target="_blank" rel="noreferrer"><LinkOutlined /> 摘要</a>, report.details_path && <a key="details" href={fileUrl(report.details_path)} target="_blank" rel="noreferrer"><LinkOutlined /> 明细</a>] .filter(Boolean)}><List.Item.Meta title={`版本 ${report.version}`} description={<Space><Tag color={report.status === 'completed' ? 'green' : 'orange'}>{report.status}</Tag><Typography.Text type="secondary">{new Date(report.created_at).toLocaleString('zh-CN')}</Typography.Text></Space>} /></List.Item>} />
      {!!reportTotal && <Pagination current={reportPage} pageSize={reportPageSize} total={reportTotal} showSizeChanger onChange={(nextPage, nextPageSize) => { setReportPage(nextPage); setReportPageSize(nextPageSize); void load(taskId, allMode, statusFilter, nextPage, nextPageSize, runPage, runPageSize); }} style={{ marginTop: 12, textAlign: 'right' }} />}
    </Card>
    <Card style={{ marginTop: 16 }} title="Run 数据导出">
      <List
        loading={loading}
        locale={{ emptyText: taskId ? '暂无 Run' : '选择具体任务后查看 Run' }}
        dataSource={runs}
        renderItem={run => (
          <List.Item actions={[
            <Button key="csv" size="small" icon={<DownloadOutlined />} onClick={() => void exportRun(run, 'csv')}>CSV</Button>,
            <Button key="json" size="small" icon={<DownloadOutlined />} onClick={() => void exportRun(run, 'json')}>JSON</Button>,
          ]}>
            <List.Item.Meta title={`Run ${run.seq}`} description={<Space><Tag>{run.status}</Tag><Typography.Text type="secondary">{run.id}</Typography.Text></Space>} />
          </List.Item>
        )}
      />
      {!!runTotal && <Pagination current={runPage} pageSize={runPageSize} total={runTotal} showSizeChanger onChange={(nextPage, nextPageSize) => { setRunPage(nextPage); setRunPageSize(nextPageSize); void load(taskId, false, statusFilter, reportPage, reportPageSize, nextPage, nextPageSize); }} style={{ marginTop: 12, textAlign: 'right' }} />}
    </Card>
  </div>;
}
