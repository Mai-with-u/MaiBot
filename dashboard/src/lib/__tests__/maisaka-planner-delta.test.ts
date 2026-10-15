import { describe, expect, it } from 'vitest'

import { PlannerDeltaDecoder } from '../maisaka-planner-delta'
import type { WsEventEnvelope } from '../unified-ws'

function full(eventId = 1, changes: Record<string, unknown> = {}): WsEventEnvelope {
  return {
    op: 'event', domain: 'maisaka_monitor', event: 'planner.progress',
    data: {
      session_id: 'group-a', run_id: 'run-1', cycle_id: 1, event_id: eventId,
      timestamp: eventId, request: null, planner: { content: '思考' },
      tools: [], active_tool_call_id: 'tool-1', final_state: {}, ...changes,
    },
  }
}

function delta(eventId = 2, changes: Record<string, unknown> = {}): WsEventEnvelope {
  return {
    op: 'event', domain: 'maisaka_monitor', event: 'planner.delta',
    data: {
      session_id: 'group-a', run_id: 'run-1', cycle_id: 1, base_event_id: eventId - 1,
      event_type: 'planner.progress', changes: { event_id: eventId, timestamp: eventId },
      removed_fields: [], ...changes,
    },
  }
}

describe('Planner 增量解码', () => {
  it('按字段合并并保留未变化的对象引用，不修改上一版本', () => {
    const decoder = new PlannerDeltaDecoder()
    const original = full()
    decoder.decode(original)
    const next = decoder.decode(delta(2, { tools_from: 0, tools: [{ summary: '结果' }] }))!
    expect(next.event).toBe('planner.progress')
    expect(next.data.planner).toBe(original.data.planner)
    expect(next.data.event_id).toBe(2)
    expect(next.data.tools).toEqual([{ summary: '结果' }])
    expect(original.data.tools).toEqual([])
    expect(original.data.event_id).toBe(1)
    const final = decoder.decode(delta(3, {
      event_type: 'planner.finalized', tools_from: 1, tools: [{ summary: '结果2' }],
    }))!
    expect(final.event).toBe('planner.finalized')
    expect(final.data.tools).toEqual([{ summary: '结果' }, { summary: '结果2' }])
    expect(() => decoder.decode(delta(4))).toThrow('基准不匹配')
  })

  it('替换工具列表后缀，支持删除字段和显式空值', () => {
    const decoder = new PlannerDeltaDecoder()
    decoder.decode(full(1, { tools: [{ summary: 'a' }, { summary: 'b' }] }))
    const next = decoder.decode(delta(2, {
      tools_from: 1, tools: [{ summary: 'c' }], removed_fields: ['active_tool_call_id'],
      changes: { event_id: 2, planner: null },
    }))!
    expect(next.data.tools).toEqual([{ summary: 'a' }, { summary: 'c' }])
    expect(next.data).not.toHaveProperty('active_tool_call_id')
    expect(next.data.planner).toBeNull()
    expect(decoder.decode(delta(3, { tools_from: 0, tools: [] }))!.data.tools).toEqual([])
  })

  it('缺失或错误基准直接报错，不能把不完整快照交给视图', () => {
    const decoder = new PlannerDeltaDecoder()
    expect(() => decoder.decode(delta())).toThrow('基准不匹配')
    decoder.decode(full())
    expect(() => decoder.decode(delta(3))).toThrow('基准不匹配')
    expect(() => decoder.decode(delta(2, { tools_from: 1, tools: [] }))).toThrow('位置无效')
    expect(() => decoder.decode(delta(2, { changes: {} }))).toThrow('事件版本')
    expect(decoder.decode(delta())!.data.event_id).toBe(2)
  })

  it('重订阅标记清空基准，重新收到完整快照后继续增量', () => {
    const decoder = new PlannerDeltaDecoder()
    decoder.decode(full())
    expect(decoder.decode({ ...full(), event: 'planner.reset', data: {} })).toBeNull()
    expect(() => decoder.decode(delta())).toThrow('基准不匹配')
    decoder.decode(full(10))
    expect(decoder.decode(delta(11))!.data.event_id).toBe(11)
  })

  it('不同群和轮次独立，回放与实时交错仍使用实际收到的基准', () => {
    const decoder = new PlannerDeltaDecoder()
    decoder.decode(full(10))
    decoder.decode(full(11, { session_id: 'group-b' }))
    expect(decoder.decode(delta(5, { base_event_id: 10 }))!.data.event_id).toBe(5)
    expect(decoder.decode(delta(12, { base_event_id: 5 }))!.data.event_id).toBe(12)
    expect(decoder.decode(delta(13, { session_id: 'group-b', base_event_id: 11 }))!.data.event_id).toBe(13)
  })

  it('缓存有上限，淘汰后完整快照可以重新建立基准', () => {
    const decoder = new PlannerDeltaDecoder()
    for (let index = 0; index < 129; index += 1) decoder.decode(full(1, { cycle_id: index }))
    expect(() => decoder.decode(delta(2, { cycle_id: 0 }))).toThrow('基准不匹配')
    decoder.decode(full(1, { cycle_id: 0 }))
    expect(decoder.decode(delta(2, { cycle_id: 0 }))!.data.event_id).toBe(2)
  })

  it('非 Planner 事件直接传递，无版本的快照报错', () => {
    const decoder = new PlannerDeltaDecoder()
    const message = { ...full(), event: 'message.ingested' }
    expect(decoder.decode(message)).toBe(message)
    const legacy = full(1, { event_id: undefined, run_id: undefined })
    expect(() => decoder.decode(legacy)).toThrow('run_id/event_id')
  })
})
