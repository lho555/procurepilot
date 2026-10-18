import { useState } from 'react'
import { Alert, Button, Card, Input, InputNumber, Modal, Space, Table, Tag, Typography } from 'antd'
import AnalysisMode from './AnalysisMode'

type Run = { id: string; status: string; phase: string; request_id: string | null; revision?: number; busy?: boolean;
  request?: { request: { sku: string; supplier_id: string; quantity: number; reason: string } } | null;
  state?: { analysis?: { run_id: string; adapter?: string; final_answer: string; error?: string | null }; action?: { reference: string }; error?: string };
  checkpoints?: { revision: number; phase: string; status: string }[] }
type Operation = 'start' | 'resume' | 'execute'

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/workflows${path}`, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(45000) })
  if (!response.ok) {
    const result = await response.json().catch(() => null) as { detail?: unknown } | null
    throw new Error(typeof result?.detail === 'string' ? result.detail : `请求失败（HTTP ${response.status}）`)
  }
  return response.json() as Promise<T>
}

export default function DurableWorkflow() {
  const [key, setKey] = useState('')
  const [query, setQuery] = useState('分析 PO-2026-10086，追加采购先审批')
  const [order, setOrder] = useState('PO-2026-10086')
  const [sku, setSku] = useState('SKU-MOTOR-01')
  const [supplier, setSupplier] = useState('SUP-B')
  const [quantity, setQuantity] = useState(78)
  const [reason, setReason] = useState('合成演示：补充订单缺口')
  const [actor, setActor] = useState('演示操作者')
  const [comment, setComment] = useState('人工核对本次操作')
  const [runs, setRuns] = useState<Run[]>([])
  const [run, setRun] = useState<Run | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [operation, setOperation] = useState<Operation | null>(null)

  async function refresh() {
    if (busy) return
    setBusy(true); setError(null)
    try { setRuns(await api<Run[]>('')); if (run) setRun(await api<Run>(`/${run.id}`)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '刷新失败') }
    finally { setBusy(false) }
  }
  async function inspect(id: string) {
    setBusy(true); setError(null); setRun(null)
    try { setRun(await api<Run>(`/${id}`)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '查询失败') }
    finally { setBusy(false) }
  }
  async function confirm() {
    if (busy || !operation || !actor.trim() || !comment.trim()) return
    setBusy(true); setError(null)
    try {
      const confirmation = { actor, comment, confirmed: true }
      const result = operation === 'start'
        ? await api<Run>('', { ...confirmation, idempotency_key: key, query,
          draft: { source_order_id: order, sku, supplier_id: supplier, quantity, reason, draft_creation_confirmed: true } })
        : await api<Run>(`/${run!.id}/resume`, { ...confirmation, execute_confirmed: operation === 'execute' })
      setRun(result); setOperation(null); setRuns(await api<Run[]>(''))
    } catch (cause) { setError(`${cause instanceof Error ? cause.message : '操作失败'}。请刷新核对；相同输入使用原幂等键，不要换键重试。`) }
    finally { setBusy(false) }
  }
  const terminal = run?.status === 'COMPLETED' || run?.status === 'REJECTED' || run?.phase === 'STOPPED'
  return <Card title="持久工作流" extra={<Tag color="cyan">Stage 6 · 本机恢复演示</Tag>} style={{ marginTop: 24 }}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary">确认创建运行会分析、保存一份草稿并提交审批，不授权执行。切换到“人工审批”批准或拒绝后，回到此页恢复运行；批准后仍需单独确认 Mock Action。同一幂等键必须保留相同输入。</Typography.Paragraph>
      <Input aria-label="工作流幂等键" placeholder="本次唯一键，如 demo-001（相同请求重试保留原键）" value={key} onChange={e => setKey(e.target.value)} maxLength={80} disabled={busy || operation !== null} />
      <Input.TextArea aria-label="持久分析问题" value={query} onChange={e => setQuery(e.target.value)} maxLength={1000} disabled={busy || operation !== null} />
      <Space wrap>
        <Input aria-label="工作流来源订单" value={order} onChange={e => setOrder(e.target.value)} maxLength={40} disabled={busy || operation !== null} />
        <Input aria-label="工作流物料" value={sku} onChange={e => setSku(e.target.value)} maxLength={40} disabled={busy || operation !== null} />
        <Input aria-label="工作流供应商" value={supplier} onChange={e => setSupplier(e.target.value)} maxLength={40} disabled={busy || operation !== null} />
        <InputNumber aria-label="工作流数量" value={quantity} onChange={value => setQuantity(value ?? 1)} min={1} max={1000000} precision={0} disabled={busy || operation !== null} />
      </Space>
      <Input aria-label="工作流草稿原因" value={reason} onChange={e => setReason(e.target.value)} maxLength={500} disabled={busy || operation !== null} />
      <Space>
        <Button disabled={busy || operation !== null || !/^[A-Za-z0-9_-]{1,80}$/.test(key) || !reason.trim() || !order.trim() || !sku.trim() || !supplier.trim() || query.trim().length < 2} onClick={() => { setError(null); setOperation('start') }}>创建并提交审批</Button>
        <Button loading={busy} disabled={operation !== null} onClick={() => void refresh()}>刷新工作流</Button>
      </Space>
      {error && operation === null && <Alert type="error" showIcon title={error} />}
      <Table<Run> rowKey="id" size="small" pagination={{ pageSize: 5 }} dataSource={runs} columns={[
        { title: '运行', dataIndex: 'id' }, { title: '状态', dataIndex: 'status' },
        { title: '查看', render: (_, item) => <Button disabled={busy || operation !== null} onClick={() => void inspect(item.id)}>查看工作流</Button> },
      ]} />
      {run && <>
        <Alert type={run.status === 'COMPLETED' ? 'success' : 'info'} title={`工作流状态：${run.status}`} description={`${run.id} · ${run.phase} · checkpoint ${run.revision} · 申请 ${run.request_id || '尚未创建'}`} />
        {run.state?.error && <Alert type="warning" title={run.state.error} />}
        {run.state?.analysis && <AnalysisMode adapter={run.state.analysis.adapter} />}
        {run.state?.analysis?.error && <Alert type="warning" title={run.state.analysis.error} />}
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{run.state?.analysis?.final_answer}</Typography.Paragraph>
        <Space>
          <Button disabled={busy || operation !== null || !run.id || terminal || run.busy} onClick={() => { setError(null); setOperation('resume') }}>恢复运行</Button>
          <Button disabled={busy || operation !== null || terminal || run.busy || run.status !== 'WAITING_EXECUTION'} onClick={() => { setError(null); setOperation('execute') }}>确认执行并恢复</Button>
        </Space>
        {run.state?.action && <Alert type="success" title={`持久 Mock 回执：${run.state.action.reference}`} />}
        <Table size="small" rowKey="revision" pagination={false} dataSource={run.checkpoints || []} columns={[
          { title: 'Checkpoint', dataIndex: 'revision' }, { title: '下一节点', dataIndex: 'phase' }, { title: '保存状态', dataIndex: 'status' },
        ]} />
      </>}
    </Space>
    <Modal title={operation === 'start' ? '确认创建持久工作流' : operation === 'execute' ? '确认恢复并执行 Mock Action' : '确认恢复运行'} open={operation !== null}
      onCancel={() => { if (!busy) setOperation(null) }} onOk={() => void confirm()} okText="确认工作流操作" cancelText="取消"
      confirmLoading={busy} okButtonProps={{ disabled: !actor.trim() || !comment.trim() }} cancelButtonProps={{ disabled: busy }} closable={!busy} mask={{ closable: !busy }}>
      <Typography.Paragraph>{operation === 'start' ? `${key} · ${order} · ${sku} · ${supplier} · 数量 ${quantity} · ${reason}` : `${run?.id} · 申请 ${run?.request_id} · ${run?.request?.request.sku || ''} · ${run?.request?.request.supplier_id || ''} · 数量 ${run?.request?.request.quantity ?? ''} · ${run?.request?.request.reason || ''}`}</Typography.Paragraph>
      <Typography.Paragraph>{operation === 'start' ? '授权分析、保存草稿并提交 WAITING_APPROVAL，不授权批准或执行。' : operation === 'execute' ? '仅在申请已批准时生成一次本地 Mock 回执，不执行真实采购。' : '按数据库 checkpoint 继续，不重新查询已保存的分析，不授权新增 Mock 执行。'}</Typography.Paragraph>
      <Space orientation="vertical" style={{ width: '100%' }}>
        <Input aria-label="工作流操作者" value={actor} onChange={e => setActor(e.target.value)} maxLength={80} disabled={busy} />
        <Input.TextArea aria-label="工作流说明" value={comment} onChange={e => setComment(e.target.value)} maxLength={500} disabled={busy} />
        {error && <Alert type="error" title={error} />}
      </Space>
    </Modal>
  </Card>
}
