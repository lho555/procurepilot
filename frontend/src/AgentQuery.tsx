import { useState } from 'react'
import { Alert, Button, Card, Input, Space, Table, Tag, Typography } from 'antd'

type Trace = { step: string; tool: string | null; transport?: string; arguments: Record<string, string>; attempt: number; status: string; duration_ms: number; error: string | null }
type Citation = { number: number; title: string; filename: string; section: string; start_line: number; end_line: number; excerpt: string }
type Result = { run_id: string; status: string; adapter: string; read_only: boolean; final_answer: string; error: string | null; trace: Trace[]; tool_calls: Trace[]; policy: { citations?: Citation[] } }

export default function AgentQuery() {
  const [query, setQuery] = useState('PO-2026-10086 为什么无法按期交付？给出补货建议，追加采购先让我审批。')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<Result | null>(null)

  async function analyze() {
    if (loading) return
    setResult(null)
    setError(null)
    if (query.trim().length < 2) { setError('请输入订单问题'); return }
    setLoading(true)
    try {
      const response = await fetch('/api/agent/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }), signal: AbortSignal.timeout(30000),
      })
      if (!response.ok) throw new Error(`分析请求失败（HTTP ${response.status}）`)
      setResult(await response.json() as Result)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '分析失败') }
    finally { setLoading(false) }
  }

  return <Card title="订单协同分析" extra={<Tag color="purple">Stage 4 · MCP / 只读 / Mock</Tag>} style={{ marginTop: 24 }}>
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary">通过本机 MCP 查询订单、库存与供应商，并检索制度。当前使用离线 Mock adapter，不调用真实模型，不保存草稿或执行采购。运行记录仅保留在当前响应中。</Typography.Paragraph>
      <Input.TextArea aria-label="订单分析问题" value={query} onChange={event => setQuery(event.target.value)} rows={3} maxLength={1000} disabled={loading} />
      <Button type="primary" loading={loading} onClick={() => void analyze()}>运行只读分析</Button>
      {loading && <Typography.Text role="status">正在查询关联事实和制度，最多等待 25 秒…</Typography.Text>}
      {error && <Alert role="alert" type="error" showIcon title={error} />}
      {result && <>
        <Alert type={result.status === 'COMPLETED' ? 'success' : 'warning'} showIcon
          title={`分析状态：${result.status}`} description={result.error || `${result.run_id} · ${result.tool_calls.length} 次读取调用`} />
        <Typography.Title level={5}>Final Result · 分析结论</Typography.Title>
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{result.final_answer}</Typography.Paragraph>
        <Typography.Title level={5}>Agent Timeline · 执行记录与工具参数</Typography.Title>
        <Table<Trace> size="small" pagination={false} scroll={{ x: 750 }} rowKey={entry => `${entry.step}:${entry.tool}:${JSON.stringify(entry.arguments)}:${entry.attempt}`} dataSource={result.trace} columns={[
          { title: '步骤', dataIndex: 'step' }, { title: '工具', dataIndex: 'tool', render: value => value || '—' },
          { title: '通道', dataIndex: 'transport', render: value => value || '—' },
          { title: '尝试', dataIndex: 'attempt' }, { title: '状态', dataIndex: 'status' },
          { title: '耗时（ms）', dataIndex: 'duration_ms' },
          { title: '参数 / 错误', render: (_, entry) => entry.error || JSON.stringify(entry.arguments) },
        ]} />
        {(result.policy.citations || []).map(citation => <Card size="small" key={citation.number} title={`制度依据 [${citation.number}] ${citation.title}`}>
          <Typography.Paragraph>{citation.section} · {citation.filename} · 第 {citation.start_line}～{citation.end_line} 行</Typography.Paragraph>
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{citation.excerpt}</Typography.Paragraph>
        </Card>)}
      </>}
    </Space>
  </Card>
}
