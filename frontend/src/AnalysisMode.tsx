import { Tag } from 'antd'

export default function AnalysisMode({ adapter }: { adapter?: string }) {
  const label = adapter === 'mock' ? '离线分析' : adapter === 'chat_completions' ? '受控模型分析' : adapter ? '分析模式不可用' : '历史分析'
  return <Tag color={adapter === 'chat_completions' ? 'purple' : 'blue'}>{label}</Tag>
}
