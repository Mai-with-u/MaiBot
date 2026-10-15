import { useEffect, useState } from 'react'

import type { StageStatusInfo } from './use-maisaka-monitor'

export function StageStatusLabel({ status }: { status: StageStatusInfo }) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  const waitUntil = status.agentState === 'wait' ? status.waitUntil : undefined

  useEffect(() => {
    if (waitUntil === undefined) return
    setNow(Date.now() / 1000)
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [waitUntil])

  // 使用后端的等待截止时间，刷新或重新进入页面后仍显示真实剩余时间。
  if (waitUntil !== undefined) {
    return <>等待({Math.max(0, Math.ceil(waitUntil - now))}s)</>
  }
  return <>{status.stage === '等待消息' ? '空闲' : status.stage || '未知阶段'}</>
}
