import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Alert, Button, Card, Descriptions, Space, Tag, Typography, message } from 'antd';
export type C2paReport = { execution: string; discovery: string; signature: string; content_binding: string; trust: string; error?: string; checked_at?: string; tool_version?: string; duration_ms?: number; validation_codes?: string[]; declared_information?: unknown; notice?: string };
const states: Record<string, string> = { present: '发现凭证', missing: '未发现凭证', passed: '通过', failed: '失败', unknown: '未知 / 未验证', untrusted: '未受信任', error: '未完成检测', completed: '检测完成' };
export const C2paPanel: React.FC<{ category: string; folder: string; initial?: C2paReport | null }> = ({ category, folder, initial }) => {
  const [result, setResult] = useState(initial);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setResult(initial); }, [initial]);
  const check = async () => {
    setBusy(true);
    try { const { data } = await axios.post(`/api/results/${category}/${folder}/c2pa`); setResult(data); }
    catch { message.error('C2PA 检测请求失败'); }
    finally { setBusy(false); }
  };
  return <Card title="视频 C2PA 凭证检测" extra={<Button loading={busy} onClick={check}>{result ? '重新检测' : '检测凭证'}</Button>}>
    <Alert type="info" showIcon message="缺失凭证仅提示，不影响生成结果；凭证校验不证明画面内容真实。" description="离线检测；外部凭证和在线吊销查询未执行。" />
    {result ? <>
      <Space wrap style={{ marginTop: 12 }}>{(['execution', 'discovery', 'signature', 'content_binding', 'trust'] as const).map(k =>
        <Tag key={k} color={result[k] === 'failed' || result[k] === 'error' ? 'error' : result[k] === 'passed' ? 'success' : 'default'}>
          {{ execution: '执行', discovery: '凭证', signature: '签名', content_binding: '内容绑定', trust: '信任' }[k]}：{states[result[k]] || result[k]}
        </Tag>)}</Space>
      {result.error && <Alert style={{ marginTop: 12 }} type="warning" message={result.error} />}
      <Descriptions size="small" column={1} style={{ marginTop: 12 }} items={[
        { key: 'time', label: '检测时间', children: result.checked_at }, { key: 'version', label: '校验器', children: result.tool_version || '不可用' },
        { key: 'codes', label: '校验状态码', children: result.validation_codes?.join('、') || '无明确状态码' },
      ]} />
      <details><summary>凭证声明信息</summary><Typography.Paragraph><pre style={{ whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>{JSON.stringify(result.declared_information || {}, null, 2)}</pre></Typography.Paragraph></details>
    </> : <Typography.Paragraph style={{ marginTop: 12 }}>尚未检测。点击“检测凭证”使用平台内置校验器检查视频。</Typography.Paragraph>}
  </Card>;
};
