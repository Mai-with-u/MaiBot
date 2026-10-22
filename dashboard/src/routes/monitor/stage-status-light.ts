import type { LightStatus } from '@/components/ui/status-light'

import type { StageStatusInfo } from './use-maisaka-monitor'

/** 空闲表示会话仍在待命；后端此时的 agentState 可能是 stop，不能据此显示灰灯。 */
export function getStageStatusLight(status?: StageStatusInfo): LightStatus {
  if (!status) return 'off'
  if (status.stage === '空闲' || status.stage === '等待消息' || status.agentState === 'idle') {
    return 'on'
  }
  // wait 是工具暂停后继续工作的倒计时，与等待新消息的空闲不同。
  if (status.agentState === 'running' || status.agentState === 'wait') return 'mixed'
  return 'off'
}
