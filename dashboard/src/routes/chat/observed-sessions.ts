import type { ChatStream } from '@/lib/chat-management-api'
import type { SessionInfo, StageStatusInfo } from '@/routes/monitor/use-maisaka-monitor'

export function isFocusFlowId(sessionId: string): boolean {
  return sessionId.startsWith('focus:')
}

/** 普通聊天来自数据库；运行中的 Focus 流替代成员入口，退出后自然恢复。 */
export function buildObservedSessions(
  chats: ChatStream[],
  observed: Map<string, SessionInfo>,
  statuses: Map<string, StageStatusInfo>,
) {
  const focusFlows = Array.from(observed.values()).filter(
    (session) => isFocusFlowId(session.sessionId) && statuses.has(session.sessionId)
  )
  const focusFlowByMember = new Map(focusFlows.flatMap(
    (flow) => (flow.focusSessionIds ?? []).map((id) => [id, flow.sessionId] as const)
  ))
  const sessions = new Map<string, SessionInfo>()
  for (const chat of chats) {
    if (focusFlowByMember.has(chat.session_id)) continue
    const live = observed.get(chat.session_id)
    sessions.set(chat.session_id, {
      sessionId: chat.session_id,
      sessionName: chat.display_name,
      isGroupChat: chat.chat_type === 'group',
      groupId: chat.group_id,
      userId: chat.user_id,
      platform: chat.platform,
      lastActivity: Math.max(chat.last_active_at ?? chat.created_at ?? 0, live?.lastActivity ?? 0),
      eventCount: live?.eventCount ?? 0,
    })
  }
  for (const flow of focusFlows) sessions.set(flow.sessionId, flow)
  return { sessions, focusFlowByMember }
}
