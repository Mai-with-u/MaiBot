import type { ReactNode } from 'react'

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type {
  ContextItemSnapshot,
  GenerationAttemptSnapshot,
  GenerationTraceSnapshot,
} from '@/lib/reasoning-process-api'

import {
  GenerationAttemptTimeline,
  GenerationTraceCard,
  ProviderResponseTimeline,
} from './generation-attempts'

vi.mock('@/components/ui/collapsible', () => ({
  Collapsible: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CollapsibleContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CollapsibleTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('./context-items', () => ({
  ContextItemTimeline: ({
    title,
    items,
  }: {
    title: string
    items: Array<{ meta: { item_id: string } }>
  }) => (
    <section data-testid="context-item-timeline">
      {title}：{items.map((item) => item.meta.item_id).join(',') || '空'}
    </section>
  ),
  ToolDefinitionsCollapsible: ({ toolDefinitions }: { toolDefinitions: unknown[] }) => (
    <div data-testid="tool-definitions">工具定义 {toolDefinitions.length}</div>
  ),
}))

afterEach(() => {
  cleanup()
})

function createItem(itemId: string, text: string): ContextItemSnapshot {
  return {
    item_type: 'UserMessageItem',
    meta: {
      item_id: itemId,
      logical_turn_id: null,
      timestamp: '2026-08-06T00:00:00.000Z',
    },
    parts: [{ type: 'text', text }],
  }
}

function createTrace(
  overrides: Partial<GenerationTraceSnapshot> = {}
): GenerationTraceSnapshot {
  return {
    provider: 'openai',
    endpoint: 'https://api.openai.com/v1/responses',
    model: 'gpt-4.1',
    response_id: 'resp-123',
    status: 'completed',
    prompt_tokens: 10,
    completion_tokens: 20,
    total_tokens: 30,
    prompt_cache_hit_tokens: 4,
    prompt_cache_miss_tokens: 6,
    output_item_ids: ['out-1', 'out-2'],
    ...overrides,
  }
}

function createAttempt(
  overrides: Partial<GenerationAttemptSnapshot> = {}
): GenerationAttemptSnapshot {
  return {
    attempt_id: 'att-1',
    workflow_purpose: 'reply',
    workflow_attempt: 1,
    provider_attempt: 2,
    model_attempt: 3,
    status: 'succeeded',
    started_at: '2026-08-01T12:00:00.000Z',
    duration_ms: 250,
    provider: 'openai',
    endpoint: 'https://api.openai.com',
    model: 'gpt-test',
    client_type: 'openai',
    operation: 'generate',
    wire_protocol: 'responses',
    request_items: [],
    tool_definitions: [],
    request_parameters: { temperature: 0.2 },
    wire_request: { model: 'gpt-test' },
    wire_response: null,
    output_items: [],
    ...overrides,
  }
}

describe('GenerationTraceCard', () => {
  it('渲染完整 Trace 元数据与 output_item_ids', () => {
    render(<GenerationTraceCard trace={createTrace()} />)

    expect(screen.getByText('Generation Trace')).toBeInTheDocument()
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('2 Items')).toBeInTheDocument()
    expect(screen.getByText('resp-123')).toBeInTheDocument()
    expect(screen.getByText('Provider：openai')).toBeInTheDocument()
    expect(screen.getByText('模型：gpt-4.1')).toBeInTheDocument()
    expect(
      screen.getByText('Endpoint：https://api.openai.com/v1/responses')
    ).toBeInTheDocument()
    expect(screen.getByText('Token：10 + 20 = 30')).toBeInTheDocument()
    expect(screen.getByText('缓存：命中 4 / 未命中 6')).toBeInTheDocument()
    expect(screen.getByText('output_item_ids: out-1, out-2')).toBeInTheDocument()
  })

  it('缺失字段回退为未知/unknown，且不展示空 ids', () => {
    render(
      <GenerationTraceCard
        trace={createTrace({
          provider: '',
          endpoint: '',
          model: '',
          response_id: null,
          status: '',
          output_item_ids: [],
        })}
      />
    )

    expect(screen.getByText('unknown')).toBeInTheDocument()
    expect(screen.getByText('0 Items')).toBeInTheDocument()
    expect(screen.getByText('Provider：未知')).toBeInTheDocument()
    expect(screen.getByText('模型：未知')).toBeInTheDocument()
    expect(screen.getByText('Endpoint：未知')).toBeInTheDocument()
    expect(screen.queryByText(/output_item_ids/)).not.toBeInTheDocument()
    expect(screen.queryByText('resp-123')).not.toBeInTheDocument()
  })
})

describe('ProviderResponseTimeline', () => {
  it('没有 output Items 时展示空态', () => {
    render(<ProviderResponseTimeline response={{ output: 'not-array' }} />)

    expect(screen.getByText('Responses 原生输出')).toBeInTheDocument()
    expect(screen.getByText('0 Items')).toBeInTheDocument()
    expect(screen.getByText('Provider 响应未包含 output Items。')).toBeInTheDocument()
  })

  it('渲染 usage、文本 parts 与 tool call payload', () => {
    render(
      <ProviderResponseTimeline
        response={{
          id: 'resp-9',
          status: 'completed',
          model: 'gpt-4.1',
          usage: {
            input_tokens: 11,
            output_tokens: 22,
            total_tokens: 33,
          },
          output: [
            {
              id: 'rsn-1',
              type: 'reasoning',
              status: 'completed',
              summary: [{ text: '先思考天气' }],
              content: { text: '推理正文' },
            },
            {
              call_id: 'msg-1',
              type: 'message',
              content: [
                { type: 'output_text', text: '今天晴' },
                { type: 'output_text', text: '适合出门' },
              ],
            },
            {
              id: 'fn-1',
              type: 'function_call',
              name: 'lookup_weather',
              arguments: '{"city":"上海"}',
            },
            {
              type: 'custom_tool_call',
              arguments: '{not-json',
            },
            {
              type: 'computer_call',
              action: { click: '#submit' },
            },
            {
              type: 'web_search_call',
              output: '搜索摘要',
            },
          ],
        }}
      />
    )

    expect(screen.getByText('6 Items')).toBeInTheDocument()
    expect(screen.getAllByText('completed').length).toBeGreaterThan(0)
    expect(screen.getByText('gpt-4.1')).toBeInTheDocument()
    expect(screen.getByText('resp-9')).toBeInTheDocument()
    expect(screen.getByText('输入 11')).toBeInTheDocument()
    expect(screen.getByText('输出 22')).toBeInTheDocument()
    expect(screen.getByText('总计 33')).toBeInTheDocument()

    expect(screen.getByText('推理')).toBeInTheDocument()
    expect(screen.getAllByText(/先思考天气/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/推理正文/).length).toBeGreaterThan(0)
    expect(screen.getByText('消息输出')).toBeInTheDocument()
    expect(screen.getAllByText(/今天晴/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/适合出门/).length).toBeGreaterThan(0)
    expect(screen.getByText('Function 调用')).toBeInTheDocument()
    expect(screen.getAllByText(/lookup_weather/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/"city": "上海"/).length).toBeGreaterThan(0)
    expect(screen.getByText('自定义工具调用')).toBeInTheDocument()
    expect(screen.getAllByText(/\{not-json/).length).toBeGreaterThan(0)
    expect(screen.getByText('计算机操作')).toBeInTheDocument()
    expect(screen.getAllByText(/"click": "#submit"/).length).toBeGreaterThan(0)
    expect(screen.getByText('联网搜索')).toBeInTheDocument()
    expect(screen.getAllByText(/搜索摘要/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/rsn-1/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/msg-1/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('完整 Item JSON').length).toBe(6)
    expect(screen.getByText('完整 Responses JSON')).toBeInTheDocument()
  })

  it('兼容非对象 Item，并只展示存在的 usage 字段', () => {
    render(
      <ProviderResponseTimeline
        response={{
          usage: { input_tokens: 7, output_tokens: 'x', total_tokens: null },
          output: ['裸字符串', { type: '   ', output: undefined }],
        }}
      />
    )

    expect(screen.getByText('输入 7')).toBeInTheDocument()
    expect(screen.queryByText(/^输出 /)).not.toBeInTheDocument()
    expect(screen.queryByText(/^总计 /)).not.toBeInTheDocument()
    expect(screen.getAllByText('未知 Item').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/裸字符串/).length).toBeGreaterThan(0)
  })
})

describe('GenerationAttemptTimeline', () => {
  it('没有调用时展示空态', () => {
    render(<GenerationAttemptTimeline attempts={[]} />)

    expect(screen.getByText('这条记录没有 Provider 调用诊断。')).toBeInTheDocument()
  })

  it.each([
    ['succeeded', '请求成功', 'emerald'],
    ['completed', '请求成功', 'emerald'],
    ['succeeded_after_retry', '重试后成功', 'emerald'],
    ['retrying', '等待重试', 'amber'],
    ['switching_model', '切换模型', 'amber'],
    ['failed', '本次失败', 'destructive'],
    ['final_failed', '最终失败', 'destructive'],
  ] as const)('状态 %s 显示 %s', (status, label, styleToken) => {
    render(<GenerationAttemptTimeline attempts={[createAttempt({ status })]} />)

    expect(screen.getByText(label).className).toContain(styleToken)
  })

  it('成功调用展示秒级耗时、工具定义、trace 与 wire response', () => {
    render(
      <GenerationAttemptTimeline
        attempts={[
          createAttempt({
            status: 'succeeded',
            duration_ms: 2500,
            tool_definitions: [{ name: 'lookup' }],
            wire_response: { ok: true },
            output_items: [createItem('out-1', '模型输出')],
            trace: createTrace(),
          }),
        ]}
      />
    )

    expect(screen.getByRole('region', { name: 'Generation Attempt 时间线' })).toBeInTheDocument()
    expect(screen.getByText('调用 #1')).toBeInTheDocument()
    expect(screen.getByText('工作流 1')).toBeInTheDocument()
    expect(screen.getByText('Provider 2 / 模型内 3')).toBeInTheDocument()
    expect(screen.getByText('2.50 s')).toBeInTheDocument()
    expect(screen.getByText('用途：reply')).toBeInTheDocument()
    expect(screen.getByText('Provider：openai / openai')).toBeInTheDocument()
    expect(screen.getByText('操作：generate / responses')).toBeInTheDocument()
    expect(screen.getByTestId('tool-definitions')).toHaveTextContent('工具定义 1')
    expect(screen.getByText('请求参数与 Wire Request')).toBeInTheDocument()
    expect(screen.getByText('Wire Response / 可用失败响应')).toBeInTheDocument()
    expect(screen.getByText(/"ok": true/)).toBeInTheDocument()
    expect(screen.getByText('Provider 原始输出 Items：out-1')).toBeInTheDocument()
    expect(screen.getByText('Generation Trace')).toBeInTheDocument()
  })

  it('失败调用展示错误、毫秒耗时与回退文案', () => {
    render(
      <GenerationAttemptTimeline
        attempts={[
          createAttempt({
            attempt_id: '',
            status: 'failed',
            duration_ms: 12.3,
            workflow_purpose: '',
            provider: '',
            client_type: '',
            operation: '',
            wire_protocol: '',
            endpoint: '',
            model: '',
            error: {
              type: 'RateLimitError',
              status_code: 429,
              message: '请求过多',
            },
          }),
          createAttempt({
            attempt_id: 'att-empty-error',
            status: 'mystery',
            duration_ms: 100,
            error: { type: '', status_code: null, message: '' },
          }),
          createAttempt({
            attempt_id: 'att-http-0',
            status: 'failed',
            error: { status_code: 0, message: 'zero-code' },
          }),
        ]}
      />
    )

    expect(screen.getAllByText('本次失败').length).toBeGreaterThan(0)
    expect(screen.getByText('12.3 ms')).toBeInTheDocument()
    expect(screen.getByText('100 ms')).toBeInTheDocument()
    expect(screen.getByText('用途：未知')).toBeInTheDocument()
    expect(screen.getByText('Provider：未知 / 未知客户端')).toBeInTheDocument()
    expect(screen.getByText('操作：未知 / 未知协议')).toBeInTheDocument()
    expect(screen.getByText('Endpoint：未知')).toBeInTheDocument()
    expect(screen.getByText('未知模型')).toBeInTheDocument()
    expect(screen.getByText('RateLimitError')).toBeInTheDocument()
    expect(screen.getByText('HTTP 429')).toBeInTheDocument()
    expect(screen.getByText('请求过多')).toBeInTheDocument()
    expect(screen.getByText('mystery')).toBeInTheDocument()
    expect(screen.getByText('HTTP 0')).toBeInTheDocument()
    expect(screen.getByText('zero-code')).toBeInTheDocument()
    expect(screen.queryByTestId('tool-definitions')).not.toBeInTheDocument()
  })

  it('duration 为 0 时展示开始时间；非法时间原样显示', () => {
    const startedAt = '2026-08-01T12:00:00.000Z'
    render(
      <GenerationAttemptTimeline
        attempts={[
          createAttempt({
            attempt_id: 'att-time',
            status: undefined,
            duration_ms: 0,
            started_at: startedAt,
          }),
          createAttempt({
            attempt_id: 'att-invalid',
            duration_ms: Number.NaN,
            started_at: 'not-a-date',
          }),
          createAttempt({
            attempt_id: 'att-empty-time',
            duration_ms: 0,
            started_at: '',
          }),
        ]}
      />
    )

    expect(screen.getByText(new Date(startedAt).toLocaleString('zh-CN'))).toBeInTheDocument()
    expect(screen.getByText('not-a-date')).toBeInTheDocument()
    expect(screen.getByText('状态未知')).toBeInTheDocument()
  })

  it('高亮新增、变更和移除的请求 Items', () => {
    const unchangedItem = createItem('item-1', '保持不变')
    const changedItem = createItem('item-2', '修改后')
    const addedItem = createItem('item-4', '新增')
    const removedItem = createItem('item-3', '被移除')

    render(
      <GenerationAttemptTimeline
        attempts={[
          createAttempt({
            request_items: [unchangedItem, changedItem, addedItem],
          }),
        ]}
        referenceRequestItems={[unchangedItem, createItem('item-2', '修改前'), removedItem]}
      />
    )

    expect(screen.getByText('实际请求差异')).toBeInTheDocument()
    expect(screen.getByText('3 Items')).toBeInTheDocument()
    expect(screen.getByText('新增或变更 Items：item-2,item-4')).toBeInTheDocument()
    expect(screen.getByText('未进入实际请求的 Items：item-3')).toBeInTheDocument()
  })

  it('仅新增或仅移除时只渲染对应差异列表', () => {
    const kept = createItem('keep', 'same')
    const added = createItem('added', 'new')
    const removed = createItem('removed', 'gone')

    const { rerender } = render(
      <GenerationAttemptTimeline
        attempts={[createAttempt({ request_items: [kept, added] })]}
        referenceRequestItems={[kept]}
      />
    )
    expect(screen.getByText('新增或变更 Items：added')).toBeInTheDocument()
    expect(screen.queryByText(/未进入实际请求的 Items/)).not.toBeInTheDocument()

    rerender(
      <GenerationAttemptTimeline
        attempts={[createAttempt({ request_items: [kept] })]}
        referenceRequestItems={[kept, removed]}
      />
    )
    expect(screen.queryByText(/新增或变更 Items/)).not.toBeInTheDocument()
    expect(screen.getByText('未进入实际请求的 Items：removed')).toBeInTheDocument()
  })

  it('无参考 Items 或内容一致时不展示差异', () => {
    const items = [createItem('item-1', '相同内容')]
    const { rerender } = render(
      <GenerationAttemptTimeline attempts={[createAttempt({ request_items: items })]} />
    )
    expect(screen.queryByText('实际请求差异')).not.toBeInTheDocument()

    rerender(
      <GenerationAttemptTimeline
        attempts={[createAttempt({ request_items: structuredClone(items) })]}
        referenceRequestItems={items}
      />
    )
    expect(screen.queryByText('实际请求差异')).not.toBeInTheDocument()
  })
})
