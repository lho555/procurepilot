import { useState } from 'react'
import { Alert, Button, Card, Descriptions, Input, Modal, Space, Table, Tag, Typography } from 'antd'

type Status = 'DRAFT' | 'WAITING_APPROVAL' | 'APPROVED' | 'REJECTED'
type PurchaseRequest = { id: string; source_order_id: string; sku: string; supplier_id: string; quantity: number; reason: string; status: Status }
type Event = { id: string; event: string; actor: string; comment: string; created_at: string }
type Detail = { request: PurchaseRequest; events: Event[]; action: { reference: string; actor: string; created_at: string } | null }
type Operation = 'submit' | 'approve' | 'reject' | 'execute'
const labels: Record<Operation, string> = { submit: '提交审批', approve: '批准申请', reject: '拒绝申请', execute: '执行 Mock Action' }

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/purchase-requests${path}`, { ...options, signal: AbortSignal.timeout(10000) })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    throw new Error(typeof body?.detail === 'string' ? body.detail : `请求失败（HTTP ${response.status}）`)
  }
  return response.json() as Promise<T>
}

export default function ApprovalQueue() {
  const [requests, setRequests] = useState<PurchaseRequest[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [operation, setOperation] = useState<Operation | null>(null)
  const [actor, setActor] = useState('演示操作者')
  const [comment, setComment] = useState('已核对合成申请')

  async function refresh() {
    if (busy) return
    setBusy(true); setError(null)
    try {
      setRequests(await api<PurchaseRequest[]>(''))
      if (detail) setDetail(await api<Detail>(`/${encodeURIComponent(detail.request.id)}`))
    } catch (cause) { setError(cause instanceof Error ? cause.message : '刷新失败') }
    finally { setBusy(false) }
  }

  async function inspect(id: string) {
    if (busy) return
    setBusy(true); setError(null); setDetail(null)
    try { setDetail(await api<Detail>(`/${encodeURIComponent(id)}`)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '查询失败') }
    finally { setBusy(false) }
  }

  async function confirm() {
    if (!detail || !operation || busy || !actor.trim() || !comment.trim()) return
    const id = detail.request.id
    const endpoint = operation === 'submit' ? 'submit' : operation === 'execute' ? 'mock-action' : 'decision'
    const body = { actor: actor.trim(), comment: comment.trim(), confirmed: true,
      ...(operation === 'approve' || operation === 'reject' ? { decision: operation === 'approve' ? 'APPROVED' : 'REJECTED' } : {}) }
    setBusy(true); setError(null)
    try {
      const updated = await api<Detail>(`/${encodeURIComponent(id)}/${endpoint}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      setDetail(updated)
      setRequests(previous => previous.map(item => item.id === id ? updated.request : item))
      setOperation(null)
    } catch (cause) {
      setError(`${cause instanceof Error ? cause.message : '操作失败'}。请取消并刷新核对结果，勿直接重复提交。`)
    } finally { setBusy(false) }
  }

  return <Card title="人工审批" extra={<Tag color="blue">Stage 5 · 本机合成演示</Tag>} style={{ marginTop: 24 }}>
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Typography.Paragraph type="secondary">先在订单查询中保存草稿，再刷新此列表。提交、审批和模拟执行分别需要确认。拒绝后不能执行；批准不会自动执行。操作者名称仅作记录，当前没有身份认证。</Typography.Paragraph>
      <Button loading={busy} disabled={operation !== null} onClick={() => void refresh()}>刷新采购申请</Button>
      {error && operation === null && <Alert role="alert" type="error" showIcon title={error} />}
      <Typography.Text type="secondary">按创建时间列出最近 100 份申请。</Typography.Text>
      <Table<PurchaseRequest> size="small" rowKey="id" pagination={{ pageSize: 5 }} scroll={{ x: 800 }} dataSource={requests} columns={[
        { title: '申请', dataIndex: 'id' }, { title: '来源订单', dataIndex: 'source_order_id' },
        { title: '物料', dataIndex: 'sku' }, { title: '数量', dataIndex: 'quantity' }, { title: '状态', dataIndex: 'status' },
        { title: '查看', render: (_, record) => <Button disabled={busy || operation !== null} onClick={() => void inspect(record.id)}>查看申请</Button> },
      ]} />
      {detail && <>
        <Descriptions title="当前申请" column={1} size="small" items={[
          { key: 'id', label: '申请编号', children: detail.request.id },
          { key: 'status', label: '申请状态', children: <Tag>{detail.request.status}</Tag> },
          { key: 'content', label: '申请内容', children: `${detail.request.source_order_id} · ${detail.request.sku} · ${detail.request.supplier_id} · 数量 ${detail.request.quantity}` },
          { key: 'reason', label: '申请原因', children: detail.request.reason },
        ]} />
        <Space wrap>
          <Button disabled={busy || operation !== null || detail.request.status !== 'DRAFT'} onClick={() => { setError(null); setOperation('submit') }}>提交审批</Button>
          <Button disabled={busy || operation !== null || detail.request.status !== 'WAITING_APPROVAL'} onClick={() => { setError(null); setOperation('approve') }}>批准申请</Button>
          <Button danger disabled={busy || operation !== null || detail.request.status !== 'WAITING_APPROVAL'} onClick={() => { setError(null); setOperation('reject') }}>拒绝申请</Button>
          <Button disabled={busy || operation !== null || detail.request.status !== 'APPROVED' || detail.action !== null} onClick={() => { setError(null); setOperation('execute') }}>执行 Mock Action</Button>
        </Space>
        {detail.request.status === 'REJECTED' && <Alert type="warning" title="申请已拒绝，不允许执行 Mock Action" />}
        {detail.action && <Alert type="success" title="Mock Action 已记录" description={`${detail.action.reference} · ${detail.action.actor} · ${detail.action.created_at}。仅生成本地模拟回执，没有下单或扣减库存。`} />}
        <Table<Event> size="small" rowKey="id" pagination={false} dataSource={detail.events} columns={[
          { title: '审批 / 执行记录', dataIndex: 'event' }, { title: '操作者', dataIndex: 'actor' },
          { title: '说明', dataIndex: 'comment' }, { title: '时间', dataIndex: 'created_at' },
        ]} />
      </>}
    </Space>
    <Modal title={operation ? `确认${labels[operation]}` : ''} open={operation !== null}
      onCancel={() => { if (!busy) setOperation(null) }} onOk={() => void confirm()} okText="确认操作" cancelText="取消"
      confirmLoading={busy} okButtonProps={{ disabled: !actor.trim() || !comment.trim() }} cancelButtonProps={{ disabled: busy }} closable={!busy} mask={{ closable: !busy }}>
      <Typography.Paragraph>{detail?.request.id} · {detail?.request.sku} · {detail?.request.supplier_id} · 数量 {detail?.request.quantity}</Typography.Paragraph>
      <Typography.Paragraph>{operation === 'execute' ? '此次确认只生成本地 Mock 回执，不连接 ERP、不下单、不扣减库存。' : operation === 'reject' ? '拒绝将终止此申请的审批流程，之后不能执行。' : operation === 'approve' ? '批准此份申请；执行 Mock Action 仍需单独确认。' : '将此份草稿提交为 WAITING_APPROVAL，不会自动批准或执行。'}</Typography.Paragraph>
      <Space orientation="vertical" style={{ width: '100%' }}>
        <Input aria-label="审批操作者" value={actor} onChange={event => setActor(event.target.value)} maxLength={80} disabled={busy} placeholder="操作者名称" />
        <Input.TextArea aria-label="审批说明" value={comment} onChange={event => setComment(event.target.value)} maxLength={500} disabled={busy} placeholder="说明" />
        {error && <Alert role="alert" type="error" showIcon title={error} />}
      </Space>
    </Modal>
  </Card>
}
