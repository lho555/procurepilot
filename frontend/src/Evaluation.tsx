import { useState } from 'react'
import { Alert, Button, Card, Space, Table, Tag, Typography } from 'antd'

const metricLabels = {
  task_success: '任务成功', tool_selection_correct: '工具选择', tool_arguments_correct: '工具参数',
  policy_citation_correct: '制度引用', approval_required_correct: '审批约束',
  unauthorized_action_rate: '未授权业务效果（越低越好）', recovery_success: '恢复成功',
}
type Metric = { numerator: number; denominator: number }
type Task = { id: string; passed: boolean; checks: Record<string, boolean>; error_type: string | null }
type Report = { stage: number; adapter: string; scope: string; passed: boolean; task_count: number; evaluated_count: number;
  finished_at: string; golden_sha256: string; results: Task[]; metrics: Record<keyof typeof metricLabels, Metric> }
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0

export function parseReport(text: string): Report {
  const data: unknown = JSON.parse(text.replace(/^\uFEFF/, ''))
  if (!object(data) || data.stage !== 7 || data.adapter !== 'mock' || typeof data.scope !== 'string' ||
    typeof data.passed !== 'boolean' || !count(data.task_count) || data.task_count < 1 || data.task_count > 100 ||
    !count(data.evaluated_count) || data.evaluated_count > data.task_count ||
    typeof data.finished_at !== 'string' || !Number.isFinite(Date.parse(data.finished_at)) ||
    typeof data.golden_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(data.golden_sha256) ||
    !Array.isArray(data.results) || data.results.length !== data.evaluated_count || !object(data.metrics)) {
    throw new Error('报告格式不正确，请选择 Stage 7 评测器生成的 report.json')
  }
  const ids = new Set<string>()
  for (const row of data.results) {
    if (!object(row) || typeof row.id !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(row.id) || ids.has(row.id) ||
      typeof row.passed !== 'boolean' || !object(row.checks) || Object.keys(row.checks).length === 0 ||
      !Object.values(row.checks).every(value => typeof value === 'boolean') ||
      row.passed !== Object.values(row.checks).every(Boolean) ||
      !(row.error_type === null || typeof row.error_type === 'string')) throw new Error('任务结果格式或检查状态不一致')
    ids.add(row.id)
  }
  for (const name of Object.keys(metricLabels)) {
    const metric = data.metrics[name]
    if (!object(metric) || !count(metric.numerator) || !count(metric.denominator) ||
      metric.numerator > metric.denominator || metric.denominator > data.evaluated_count) throw new Error('指标分子 / 分母不正确')
  }
  const success = data.metrics.task_success as Metric
  if (success.denominator !== data.evaluated_count || success.numerator !== data.results.filter(row => row.passed).length ||
    (data.passed && (data.evaluated_count !== data.task_count || success.numerator !== data.task_count || 'setup_error_type' in data))) {
    throw new Error('报告汇总与任务结果不一致')
  }
  return data as Report
}

export default function Evaluation() {
  const [report, setReport] = useState<Report | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  async function load(file?: File) {
    if (!file) return
    setReport(null); setError(null); setName(''); setLoading(true)
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('报告不能超过 2 MB')
      setReport(parseReport(await file.text())); setName(file.name)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '报告读取失败') }
    finally { setLoading(false) }
  }
  const metrics = report ? Object.entries(metricLabels).map(([key, label]) => ({ key, label, ...report.metrics[key as keyof typeof metricLabels] })) : []
  return <Card title="Evaluation · 评测结果">
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Alert type="info" showIcon title="固定合成任务 · Mock 工作流" description="指标反映导入报告中的确定性验收结果，不代表真实 LLM 推理或生产安全。未授权指标仅检查评测观测的业务效果。导入不会重新执行或验证业务任务。" />
      <Typography.Paragraph>运行评测后，选择生成的 report.json。文件仅在浏览器内读取，不上传；刷新页面后需重新选择。</Typography.Paragraph>
      <Typography.Text code>./.venv/Scripts/python.exe scripts/run_evals.py --confirm</Typography.Text>
      <label>选择评测报告 <input aria-label="选择评测报告" type="file" accept=".json,application/json" disabled={loading} onChange={event => { void load(event.target.files?.[0]); event.target.value = '' }} /></label>
      {loading && <Typography.Text role="status">正在读取报告…</Typography.Text>}
      {error && <Alert role="alert" type="error" showIcon title="无法读取评测报告" description={error} />}
      {!report && !error && <Typography.Text type="secondary">尚未导入报告。不会显示预设成绩。</Typography.Text>}
      {report && <>
        <Alert type={report.passed ? 'success' : 'error'} showIcon title={report.passed ? '报告结果：全部通过' : '报告结果：未通过'} description={`${name} · 已评测 ${report.evaluated_count}/${report.task_count} · ${report.finished_at}`} />
        <Table size="small" pagination={false} rowKey="key" dataSource={metrics} columns={[
          { title: '指标', dataIndex: 'label' },
          { title: '分子 / 分母', render: (_, row) => `${row.numerator} / ${row.denominator}` },
          { title: '比例', render: (_, row) => row.denominator === 0 ? '不适用' : `${(row.numerator / row.denominator * 100).toFixed(2)}%` },
        ]} />
        <Typography.Text type="secondary">不适用项排除；适用失败计入失败。未完成全部任务时整体报告未通过。</Typography.Text>
        <Table<Task> size="small" pagination={{ pageSize: 10 }} scroll={{ x: 500 }} rowKey="id" dataSource={report.results} columns={[
          { title: 'Golden Task', dataIndex: 'id' },
          { title: '结果', render: (_, row) => <Tag color={row.passed ? 'green' : 'red'}>{row.passed ? '通过' : '失败'}</Tag> },
          { title: '失败检查 / 异常', render: (_, row) => [...Object.entries(row.checks).filter(([, passed]) => !passed).map(([check]) => check), row.error_type].filter(Boolean).join('、') || '—' },
        ]} />
        <Typography.Paragraph style={{ overflowWrap: 'anywhere' }}>任务集 SHA256：{report.golden_sha256}</Typography.Paragraph>
        <Button onClick={() => { setReport(null); setName(''); setError(null) }}>清除报告</Button>
      </>}
    </Space>
  </Card>
}
