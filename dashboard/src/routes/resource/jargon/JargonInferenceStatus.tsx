import { useTranslation } from 'react-i18next'

const labels: Record<string, string> = {
  unattempted: '未推断',
  queued: '排队中',
  running: '推断中',
  succeeded: '推断成功',
  insufficient_info: '信息不足',
  missing_evidence: '缺少证据',
  failed: '请求失败',
  protected: '人工保护',
  stale: '词条已更新',
  missing: '词条已删除',
  cancelled: '已取消',
  cancelling: '正在取消',
  completed: '已完成',
  interrupted: '运行中断',
  duplicate: '已在队列中',
  has_meaning: '已有含义',
  threshold: '未达到阈值',
  locked: '已锁定',
  manual: '手动维护',
  no_session: '没有可用会话',
  skipped: '已跳过',
}

export function InferenceStatus({ status }: { status: string }) {
  const { t } = useTranslation()
  return <>{t(`jargonInference.states.${status}`, labels[status] ?? status)}</>
}
