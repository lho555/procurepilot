import { useState } from 'react'
import { Alert, Button, Card, Descriptions, Form, Input, InputNumber, Modal, Space, Table, Tag, Typography } from 'antd'

type OrderItem = { sku: string; quantity: number }
type Order = {
  id: string; supplier_id: string; due_date: string; supplier_delivery_date: string
  status: string; items: OrderItem[]
}
type Inventory = { sku: string; name: string; on_hand: number; reserved: number; available: number }
type Quote = { id: string; sku: string; unit_price: string; currency: string; minimum_order_quantity: number; lead_time_days: number }
type Supplier = { id: string; name: string; lead_time_days: number; quotes: Quote[] }
type Row = OrderItem & Inventory
type Report = { order: Order; supplier: Supplier; rows: Row[] }
type DraftValues = { supplier_id: string; quantity: number; reason: string }
type Draft = { id: string; status: 'DRAFT' }

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, signal: AbortSignal.timeout(10000) })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    throw new Error(typeof body?.detail === 'string' ? body.detail : `请求失败（HTTP ${response.status}）`)
  }
  return response.json() as Promise<T>
}

function SupplierDetails({ supplier }: { supplier: Supplier }) {
  return <>
    <Typography.Paragraph>{supplier.name}（{supplier.id}） · 默认交期 {supplier.lead_time_days} 天</Typography.Paragraph>
    <Table<Quote> rowKey="id" size="small" pagination={false} scroll={{ x: 500 }} dataSource={supplier.quotes}
      columns={[
        { title: '物料', dataIndex: 'sku' },
        { title: '单价', render: (_, quote) => `${quote.unit_price} ${quote.currency}` },
        { title: '起订量', dataIndex: 'minimum_order_quantity' },
        { title: '交期（天）', dataIndex: 'lead_time_days' },
      ]} />
  </>
}

export default function BusinessQuery() {
  const [orderId, setOrderId] = useState('PO-2026-10086')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [supplierId, setSupplierId] = useState('SUP-B')
  const [supplier, setSupplier] = useState<Supplier | null>(null)
  const [supplierLoading, setSupplierLoading] = useState(false)
  const [supplierError, setSupplierError] = useState<string | null>(null)
  const [draftRow, setDraftRow] = useState<Row | null>(null)
  const [saving, setSaving] = useState(false)
  const [draftError, setDraftError] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [form] = Form.useForm<DraftValues>()

  async function queryOrder() {
    if (loading || !orderId.trim()) return
    setLoading(true)
    setError(null)
    setReport(null)
    setDraft(null)
    try {
      const order = await api<Order>(`/orders/${encodeURIComponent(orderId.trim())}`)
      const [primarySupplier, inventories] = await Promise.all([
        api<Supplier>(`/suppliers/${encodeURIComponent(order.supplier_id)}`),
        Promise.all(order.items.map(item => api<Inventory>(`/inventory/${encodeURIComponent(item.sku)}`))),
      ])
      setReport({ order, supplier: primarySupplier, rows: order.items.map((item, index) => ({ ...item, ...inventories[index] })) })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '查询失败')
    } finally { setLoading(false) }
  }

  async function querySupplier() {
    if (supplierLoading || !supplierId.trim()) return
    setSupplierLoading(true)
    setSupplierError(null)
    setSupplier(null)
    try { setSupplier(await api<Supplier>(`/suppliers/${encodeURIComponent(supplierId.trim())}`)) }
    catch (cause) { setSupplierError(cause instanceof Error ? cause.message : '查询失败') }
    finally { setSupplierLoading(false) }
  }

  function prepareDraft(row: Row) {
    if (!report) return
    setDraftError(null)
    form.setFieldsValue({ supplier_id: report.order.supplier_id, quantity: Math.max(row.quantity - row.available, 1), reason: '合成演示：订单物料补货' })
    setDraftRow(row)
  }

  async function saveDraft() {
    if (!report || !draftRow || saving) return
    let values: DraftValues
    try { values = await form.validateFields() } catch { return }
    setSaving(true)
    setDraftError(null)
    try {
      const result = await api<Draft>('/purchase-requests/draft', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...values, source_order_id: report.order.id, sku: draftRow.sku, draft_creation_confirmed: true }),
      })
      setDraft(result)
      setDraftRow(null)
    } catch (cause) {
      setDraftError(cause instanceof Error ? cause.message : '保存失败')
    } finally { setSaving(false) }
  }

  return <Space orientation="vertical" size="large" style={{ width: '100%', marginTop: 24 }}>
    <Card title="采购订单查询" extra={<Tag color="cyan">Stage 1 · 合成数据</Tag>}>
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <Input.Search aria-label="订单号" value={orderId} onChange={event => setOrderId(event.target.value)}
          onSearch={() => void queryOrder()} loading={loading} disabled={loading} enterButton="查询关联数据" />
        <Typography.Text type="secondary">示例：PO-2026-10086 缺货 / 延期；PO-2026-10087 库存充足 / 按期。</Typography.Text>
        {error && <Alert role="alert" type="error" showIcon title={error} />}
        {report && <>
          <Descriptions title={report.order.id} column={2} size="small" items={[
            { key: 'due', label: '要求交付', children: report.order.due_date },
            { key: 'delivery', label: '供应商预计交付', children: report.order.supplier_delivery_date },
            { key: 'status', label: '状态', children: report.order.status },
            { key: 'late', label: '交期', children: report.order.supplier_delivery_date > report.order.due_date ? <Tag color="error">延期</Tag> : <Tag color="success">按期</Tag> },
          ]} />
          <Table<Row> rowKey="sku" size="small" pagination={false} dataSource={report.rows} scroll={{ x: 700 }} columns={[
            { title: '物料', dataIndex: 'name' }, { title: 'SKU', dataIndex: 'sku' },
            { title: '需求', dataIndex: 'quantity' }, { title: '可用库存', dataIndex: 'available' },
            { title: '缺口', render: (_, row) => Math.max(row.quantity - row.available, 0) },
            { title: '操作', render: (_, row) => <Button onClick={() => prepareDraft(row)}>准备采购草稿</Button> },
          ]} />
          <Typography.Text type="secondary">可用库存 = 在库 − 已预留；缺口是单订单参考值，不会占用库存。</Typography.Text>
          <SupplierDetails supplier={report.supplier} />
        </>}
        {draft && <Alert type="success" showIcon title="采购草稿已保存" description={`${draft.id} · ${draft.status}。尚未审批采购或执行业务动作。`} />}
      </Space>
    </Card>
    <Card title="供应商查询">
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <Input.Search aria-label="供应商编号" value={supplierId} onChange={event => setSupplierId(event.target.value)}
          onSearch={() => void querySupplier()} loading={supplierLoading} disabled={supplierLoading} enterButton="查询供应商" />
        {supplierError && <Alert role="alert" type="error" showIcon title={supplierError} />}
        {supplier && <SupplierDetails supplier={supplier} />}
      </Space>
    </Card>
    <Modal title={`确认保存采购草稿 · ${draftRow?.sku ?? ''}`} open={draftRow !== null}
      onCancel={() => { if (!saving) setDraftRow(null) }} onOk={() => void saveDraft()}
      okText="确认保存草稿" cancelText="取消" confirmLoading={saving} cancelButtonProps={{ disabled: saving }} closable={!saving} mask={{ closable: !saving }}>
      <Typography.Paragraph>此确认仅授权保存 DRAFT 草稿，不会下单、修改库存或执行采购。再次确认会新增一份草稿，请勿重复提交。</Typography.Paragraph>
      <Form form={form} layout="vertical">
        <Form.Item name="supplier_id" label="供应商编号" rules={[{ required: true }, { pattern: /^[A-Za-z0-9_-]{1,40}$/, message: '填写有效供应商编号' }]}><Input disabled={saving} /></Form.Item>
        <Form.Item name="quantity" label="数量（须满足供应商起订量）" rules={[{ required: true }]}><InputNumber min={1} max={1000000} precision={0} disabled={saving} /></Form.Item>
        <Form.Item name="reason" label="原因" rules={[{ required: true, whitespace: true, max: 500 }]}><Input.TextArea maxLength={500} disabled={saving} /></Form.Item>
      </Form>
      {draftError && <Alert role="alert" type="error" showIcon title={draftError} description="若请求超时或连接断开，请先检查保存结果，避免重复提交。" />}
    </Modal>
  </Space>
}
