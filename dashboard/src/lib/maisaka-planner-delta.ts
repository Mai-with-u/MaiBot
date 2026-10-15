import type { MaisakaFinalizedToolResult, PlannerFinalizedEvent } from './maisaka-monitor-client'
import type { WsEventEnvelope } from './unified-ws'

type PlannerSnapshot = PlannerFinalizedEvent & { event_id: number }

interface PlannerDelta {
  session_id: string
  run_id: string
  cycle_id: number
  base_event_id: number
  event_type: 'planner.progress' | 'planner.finalized'
  changes: Partial<PlannerSnapshot> & { event_id: number }
  removed_fields: string[]
  tools_from?: number
  tools?: MaisakaFinalizedToolResult[]
}

const MAX_PLANNER_BASES = 128

/** 与后端连接级编码器一致：只缓存未结束轮次，淘汰后由服务端重新发完整快照。 */
export class PlannerDeltaDecoder {
  private readonly bases = new Map<string, PlannerSnapshot>()

  decode(message: WsEventEnvelope): WsEventEnvelope | null {
    if (message.domain !== 'maisaka_monitor') return message
    if (message.event === 'planner.reset') {
      this.bases.clear()
      return null
    }
    if (!['planner.progress', 'planner.finalized', 'planner.delta'].includes(message.event)) {
      return message
    }
    const data = message.data as unknown as PlannerSnapshot | PlannerDelta
    const key = JSON.stringify([data.session_id, data.run_id, data.cycle_id])
    let decoded = message
    let snapshot: PlannerSnapshot
    let eventType = message.event
    if (message.event === 'planner.delta') {
      const delta = data as PlannerDelta
      const previous = this.bases.get(key)
      if (!previous || previous.event_id !== delta.base_event_id) {
        throw new Error(`Planner 增量基准不匹配: ${key}, expected=${delta.base_event_id}`)
      }
      if (!Number.isInteger(delta.changes.event_id) || delta.changes.event_id <= 0) {
        throw new Error(`Planner 增量缺少有效事件版本: ${key}`)
      }
      snapshot = { ...previous, ...delta.changes }
      for (const field of delta.removed_fields) {
        delete (snapshot as unknown as Record<string, unknown>)[field]
      }
      if (delta.tools_from !== undefined) {
        if (
          !Number.isInteger(delta.tools_from) || delta.tools_from < 0 ||
          delta.tools_from > previous.tools.length || !Array.isArray(delta.tools)
        ) {
          throw new Error(`Planner 工具增量位置无效: ${key}`)
        }
        snapshot.tools = [...previous.tools.slice(0, delta.tools_from), ...delta.tools]
      }
      eventType = delta.event_type
      decoded = { ...message, event: eventType, data: snapshot as unknown as Record<string, unknown> }
    } else {
      snapshot = data as PlannerSnapshot
      if (!snapshot.run_id || !Number.isInteger(snapshot.event_id) || snapshot.event_id <= 0) {
        throw new Error(`Planner 快照缺少有效的 run_id/event_id: ${key}`)
      }
    }

    if (eventType === 'planner.finalized') {
      this.bases.delete(key)
    } else {
      this.bases.set(key, snapshot)
      if (this.bases.size > MAX_PLANNER_BASES) {
        this.bases.delete(this.bases.keys().next().value!)
      }
    }
    return decoded
  }
}
