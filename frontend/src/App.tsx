import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Space, Tabs, Tag, Typography } from 'antd'
import BusinessQuery from './BusinessQuery'
import KnowledgeQuery from './KnowledgeQuery'
import AgentQuery from './AgentQuery'
import ApprovalQueue from './ApprovalQueue'
import DurableWorkflow from './DurableWorkflow'
import Evaluation from './Evaluation'

type Health = { status: 'ok'; service: string; stage: number }

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError(null)
    setHealth(null)
    try {
      const response = await fetch('/api/health', {
        signal: signal
          ? AbortSignal.any([signal, AbortSignal.timeout(5000)])
          : AbortSignal.timeout(5000),
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data: unknown = await response.json()
      if (
        typeof data !== 'object' || data === null ||
        !('status' in data) || data.status !== 'ok' ||
        !('service' in data) || typeof data.service !== 'string' ||
        !('stage' in data) || typeof data.stage !== 'number'
      ) throw new Error('健康检查响应格式不正确')
      if (!signal?.aborted) setHealth(data as Health)
    } catch (cause) {
      if (!signal?.aborted) {
        setError(cause instanceof Error ? cause.message : '请求失败')
      }
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void refresh(controller.signal)
    return () => controller.abort()
  }, [refresh])

  return (
    <main>
      <Typography.Title>ProcurePilot AI</Typography.Title>
      <Typography.Paragraph>采购 / 供应链异常协同助手</Typography.Paragraph>
      <Alert style={{ marginBottom: 24 }} type="info" showIcon title="合成数据 · 人工审批" description="先分析事实与制度，再由人工确认草稿、审批和执行。分析模式以运行结果为准；执行仅保存本地回执，不下单、不付款、不改变库存。" />
      <Card title="服务状态" extra={<Tag>基础服务</Tag>}>
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <div role="status" aria-live="polite">
            {loading && <Typography.Text>正在检查后端…</Typography.Text>}
            {health && <Alert type="success" showIcon title="后端运行正常" description={health.service} />}
            {error && <Alert type="error" showIcon title="无法连接后端" description={`${error}。请确认后端已启动，然后重试。`} />}
          </div>
          <Button onClick={() => void refresh()} loading={loading}>刷新状态</Button>
          <Typography.Text type="secondary">此状态仅代表 API 存活，不检查 MySQL。</Typography.Text>
        </Space>
      </Card>
      <Tabs style={{ marginTop: 24 }} defaultActiveKey="analysis" items={[
        { key: 'analysis', label: '任务与分析', children: <AgentQuery /> },
        { key: 'workflow', label: '持久工作流', children: <DurableWorkflow /> },
        { key: 'approval', label: '人工审批', children: <ApprovalQueue /> },
        { key: 'business', label: '业务查询', children: <BusinessQuery /> },
        { key: 'knowledge', label: '制度证据', children: <KnowledgeQuery /> },
        { key: 'evaluation', label: 'Evaluation', children: <Evaluation /> },
      ]} />
    </main>
  )
}
