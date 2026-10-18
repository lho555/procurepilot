import { useState } from 'react'
import { Alert, Button, Card, Input, Modal, Space, Tag, Typography } from 'antd'

type Citation = {
  number: number; document_id: string; title: string; filename: string; section: string
  start_line: number; end_line: number; excerpt: string; source_sha256: string
}
type Answer = { found: boolean; answer: string; citations: Citation[]; index_revision: string }
type Document = { id: string; title: string; content: string; sha256: string }

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/knowledge${path}`, { ...options, signal: AbortSignal.timeout(10000) })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    throw new Error(typeof body?.detail === 'string' ? body.detail : `请求失败（HTTP ${response.status}）`)
  }
  return response.json() as Promise<T>
}

export default function KnowledgeQuery() {
  const [question, setQuestion] = useState('紧急采购超过 20000 元需要谁审批？')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [source, setSource] = useState<Document | null>(null)
  const [sourceLoading, setSourceLoading] = useState(false)
  const [sourceError, setSourceError] = useState<string | null>(null)

  async function ask() {
    if (loading) return
    if (question.trim().length < 2) { setError('请输入至少两个字符的问题'); return }
    setLoading(true)
    setError(null)
    setAnswer(null)
    setSource(null)
    setSourceError(null)
    try {
      setAnswer(await request<Answer>('/ask', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, top_k: 3 }),
      }))
    } catch (cause) { setError(cause instanceof Error ? cause.message : '查询失败') }
    finally { setLoading(false) }
  }

  async function viewSource(citation: Citation) {
    if (sourceLoading) return
    setSourceLoading(true)
    setSourceError(null)
    try {
      const document = await request<Document>(`/documents/${encodeURIComponent(citation.document_id)}`)
      if (document.sha256 !== citation.source_sha256) throw new Error('制度索引已更新，请重新查询后查看来源')
      setSource(document)
    } catch (cause) { setSourceError(cause instanceof Error ? cause.message : '来源查询失败') }
    finally { setSourceLoading(false) }
  }

  return <Card title="采购制度查询" extra={<Tag color="blue">Stage 2 · 合成制度</Tag>} style={{ marginTop: 24 }}>
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary">根据本地制度原文摘录回答，并展示章节和行号。使用词面向量检索，不调用外部模型；未找到依据时请交由人工核实。</Typography.Paragraph>
      <Input.TextArea aria-label="制度问题" value={question} onChange={event => setQuestion(event.target.value)} maxLength={1000} rows={2} disabled={loading} />
      <Button type="primary" onClick={() => void ask()} loading={loading}>查询制度依据</Button>
      {error && <Alert role="alert" type="error" showIcon title={error} />}
      {answer && <>
        <Alert type={answer.found ? 'info' : 'warning'} showIcon title={answer.found ? '已找到制度依据' : '未找到依据'} />
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{answer.answer}</Typography.Paragraph>
        {answer.citations.map(citation => <Card key={`${answer.index_revision}-${citation.number}`} size="small"
          title={`[${citation.number}] ${citation.title}`}>
          <Typography.Paragraph>{citation.section} · {citation.filename} · 第 {citation.start_line}～{citation.end_line} 行</Typography.Paragraph>
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{citation.excerpt}</Typography.Paragraph>
          <Button onClick={() => void viewSource(citation)} loading={sourceLoading}>查看来源快照 {citation.number}</Button>
        </Card>)}
      </>}
      {sourceError && <Alert role="alert" type="warning" showIcon title={sourceError} />}
    </Space>
    <Modal title={source?.title} open={source !== null} onCancel={() => setSource(null)} footer={null} width={850}>
      <Typography.Paragraph type="secondary">显示导入时保存的文档快照，与回答引用一致。</Typography.Paragraph>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{source?.content.split('\n').map((line, index) => `${index + 1}: ${line}`).join('\n')}</pre>
    </Modal>
  </Card>
}
