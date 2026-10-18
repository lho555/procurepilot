import { useState } from 'react'
import { Alert, Button, Card, Space, Table, Tag, Typography } from 'antd'

const labels = { task_success: '案例验收', grounded_facts: '事实约束', citation_applicability: '引用符合题集标注', recommendation_coverage: '建议覆盖题集要求', failure_handling: '预期失败处理', unexpected_acceptance: '预期失败却被采纳（越低越好）' }
type MetricName = keyof typeof labels
type Metric = { numerator: number; denominator: number; value: number | null }
type Outcome = 'accepted' | 'rejected' | 'timed_out' | 'error'
type Row = { id: string; passed: boolean; checks: Record<string, boolean>; metrics: Record<MetricName, boolean | null>; expected_outcome: Exclude<Outcome, 'error'>; actual_outcome: Outcome; provider_attempts: number; local_validation_ms: number }
type Report = { schema_version: number; stage: number; adapter: string; mode: 'fixture_replay' | 'recorded_replay'; passed: boolean; task_count: number; evaluated_count: number; results: Row[]; metrics: Record<MetricName, Metric>; finished_at: string; case_sha256: string; responses_sha256: string }
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const hash = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const names = Object.keys(labels) as MetricName[]
const metricValues = (value: unknown): value is Record<MetricName, boolean | null> => object(value) && names.every(name => value[name] === null || typeof value[name] === 'boolean')

export function parseModelReport(text: string): Report {
  const data: unknown = JSON.parse(text.replace(/^\uFEFF/, ''))
  if (!object(data) || data.schema_version !== 1 || data.stage !== 9 || data.adapter !== 'response_replay' ||
    !['fixture_replay', 'recorded_replay'].includes(String(data.mode)) || data.live_model_requests !== 0 || data.model_quality_claim !== false ||
    data.model_cost !== null || data.model_latency_ms !== null || typeof data.passed !== 'boolean' ||
    !count(data.task_count) || data.task_count < 1 || data.task_count > 30 || !count(data.evaluated_count) || data.evaluated_count > data.task_count ||
    !Array.isArray(data.results) || data.results.length !== data.evaluated_count || !object(data.metrics) ||
    !hash(data.case_sha256) || !hash(data.responses_sha256) || typeof data.finished_at !== 'string' || !Number.isFinite(Date.parse(data.finished_at)))
    throw new Error('请选择离线模型评测器生成的 report.json；本页面不接受在线运行声明')
  const ids = new Set<string>()
  for (const row of data.results) {
    if (!object(row) || typeof row.id !== 'string' || !/^[a-z][a-z0-9_]{0,49}$/.test(row.id) || ids.has(row.id) ||
      typeof row.passed !== 'boolean' || !object(row.checks) || Object.keys(row.checks).length === 0 ||
      !Object.values(row.checks).every(value => typeof value === 'boolean') || row.passed !== Object.values(row.checks).every(Boolean) ||
      !metricValues(row.metrics) || row.metrics.task_success !== row.passed ||
      !['accepted', 'rejected', 'timed_out'].includes(String(row.expected_outcome)) || !['accepted', 'rejected', 'timed_out', 'error'].includes(String(row.actual_outcome)) ||
      row.checks.outcome !== (row.expected_outcome === row.actual_outcome) || !count(row.provider_attempts) || row.provider_attempts > 1 ||
      typeof row.local_validation_ms !== 'number' || !Number.isFinite(row.local_validation_ms) || row.local_validation_ms < 0)
      throw new Error('案例结果、检查或评分不一致')
    const positive = row.expected_outcome === 'accepted'
    for (const name of ['grounded_facts', 'citation_applicability', 'recommendation_coverage'] as const) {
      if (positive ? typeof row.checks[name] !== 'boolean' || row.metrics[name] !== row.checks[name] : row.metrics[name] !== null)
        throw new Error('案例指标适用范围不一致')
    }
    if (positive ? row.metrics.failure_handling !== null || row.metrics.unexpected_acceptance !== null :
      row.metrics.failure_handling !== row.checks.outcome || row.metrics.unexpected_acceptance !== (row.actual_outcome === 'accepted'))
      throw new Error('失败处理指标不一致')
    ids.add(row.id)
  }
  for (const name of names) {
    const values = data.results.map(row => row.metrics[name]).filter(value => value !== null)
    const numerator = values.filter(Boolean).length
    const metric = data.metrics[name]
    if (!object(metric) || metric.numerator !== numerator || metric.denominator !== values.length || metric.value !== (values.length ? numerator / values.length : null))
      throw new Error('报告分数与逐项结果不一致')
  }
  if (data.passed !== (data.evaluated_count === data.task_count && data.results.every(row => row.passed)))
    throw new Error('报告汇总与案例结果不一致')
  return data as Report
}

const outcomes: Record<Outcome, string> = { accepted: '采纳', rejected: '拒绝', timed_out: '超时', error: '评测异常' }

export default function ModelEvaluation() {
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  async function load(file?: File) {
    if (!file) return
    setReport(null); setError(null); setLoading(true)
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('报告不能超过 2 MB')
      setReport(parseModelReport(await file.text()))
    } catch (cause) { setError(cause instanceof Error ? cause.message : '读取失败') }
    finally { setLoading(false) }
  }
  const metrics = report ? names.map(key => ({ key, label: labels[key], ...report.metrics[key] })) : []
  return <Card title="模型验收 · 离线响应回放">
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Alert type="info" showIcon title="固定合成题集 · 非在线模型成绩" description="校验响应契约、引用与建议是否符合题集标注。导入响应的来源不经认证；分数不能证明真实模型质量或制度适用性。文件仅在浏览器内读取，不上传、不调用模型。" />
      <Typography.Text code>./.venv/Scripts/python.exe scripts/run_model_evals.py --confirm</Typography.Text>
      <label>选择模型评测报告 <input aria-label="选择模型评测报告" type="file" accept=".json,application/json" disabled={loading} onChange={event => { void load(event.target.files?.[0]); event.target.value = '' }} /></label>
      {loading && <Typography.Text role="status">正在核对报告…</Typography.Text>}
      {error && <Alert role="alert" type="error" title="无法读取模型报告" description={error} />}
      {!report && !error && <Typography.Text type="secondary">尚未导入报告，不显示预设成绩。</Typography.Text>}
      {report && <>
        <Tag color="blue">{report.mode === 'fixture_replay' ? '内置响应回放' : '导入响应回放'}</Tag>
        <Alert type={report.passed ? 'success' : 'warning'} title={report.passed ? '模型回放验收：全部通过' : '模型回放验收：未通过'} description={`已评测 ${report.evaluated_count}/${report.task_count} · ${report.finished_at}`} />
        <Typography.Paragraph>在线模型请求：0 · 模型时延：未测量 · 模型费用：未测量</Typography.Paragraph>
        <Table rowKey="key" size="small" pagination={false} dataSource={metrics} columns={[
          { title: '指标', dataIndex: 'label' }, { title: '分子 / 分母', render: (_, row) => `${row.numerator} / ${row.denominator}` },
          { title: '比例', render: (_, row) => row.denominator ? `${(row.numerator / row.denominator * 100).toFixed(2)}%` : '不适用' },
        ]} />
        <Typography.Text type="secondary">适用失败仍计入分母；契约通过的回答也可能不符合题集标注。预期失败却被采纳的比例越低越好。</Typography.Text>
        <Table<Row> rowKey="id" size="small" pagination={{ pageSize: 12 }} scroll={{ x: 700 }} dataSource={report.results} columns={[
          { title: '案例', dataIndex: 'id' }, { title: '预期', render: (_, row) => outcomes[row.expected_outcome] },
          { title: '实际', render: (_, row) => outcomes[row.actual_outcome] },
          { title: '验收', render: (_, row) => <Tag color={row.passed ? 'green' : 'red'}>{row.passed ? '通过' : '失败'}</Tag> },
          { title: '失败检查', render: (_, row) => Object.keys(row.checks).filter(key => !row.checks[key]).join('、') || '—' },
        ]} />
        <Typography.Paragraph style={{ overflowWrap: 'anywhere' }}>题集 SHA256：{report.case_sha256}<br />响应 SHA256：{report.responses_sha256}</Typography.Paragraph>
        <Button onClick={() => { setReport(null); setError(null) }}>清除模型报告</Button>
      </>}
    </Space>
  </Card>
}
