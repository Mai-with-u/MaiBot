/**
 * 重放编辑组件测试：模型列表加载、正文/JSON 编辑、空列表、重放成功/失败。
 * 只桩 config-api / reasoning-process-api / toast / CodeEditor。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState, type ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'

import { getModelConfig } from '@/lib/config-api'
import {
  replayReasoningPrompt,
  type ReasoningPromptFile,
  type ReasoningReplayResponse,
} from '@/lib/reasoning-process-api'

import {
  createBlankReplayItem,
  ReasoningReplayPanel,
  ReplayItemEditorColumn,
  ReplayResultItem,
  type EditableReplayItem,
  type ReplayRunResult,
} from './replay-editor'
import type { StructuredPromptPayload } from './schema'

const { toastMock } = vi.hoisted(() => ({ toastMock: vi.fn() }))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

vi.mock('@/lib/config-api', () => ({
  getModelConfig: vi.fn(),
}))

vi.mock('@/lib/reasoning-process-api', () => ({
  replayReasoningPrompt: vi.fn(),
}))

vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({
    value,
    onChange,
  }: {
    value: string
    onChange?: (next: string) => void
  }) => (
    <textarea
      aria-label="可编辑编辑器"
      data-testid="replay-json-editor"
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}))

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function makeReplayItem(overrides: Partial<EditableReplayItem> = {}): EditableReplayItem {
  const item = {
    item_type: 'UserMessageItem',
    meta: {
      item_id: 'item-1',
      logical_turn_id: null,
      timestamp: '2026-08-06T00:00:00.000Z',
    },
    parts: [{ type: 'text', text: '你好' }],
  }
  return {
    id: 'item-1',
    itemType: 'UserMessageItem',
    jsonText: JSON.stringify(item, null, 2),
    ...overrides,
  }
}

function makePrompt(overrides: Partial<StructuredPromptPayload> = {}): StructuredPromptPayload {
  return {
    request: { kind: 'planner' },
    metadata: { model_name: 'gpt-test' },
    request_items: [],
    output_items: [],
    generation_attempts: [],
    ...overrides,
  }
}

function makeFile(overrides: Partial<ReasoningPromptFile> = {}): ReasoningPromptFile {
  return {
    stage: 'planner',
    session_id: 'sess-1',
    resolved_session_id: 'sess-1',
    session_display_name: '测试会话',
    platform: 'qq',
    chat_type: 'group',
    target_id: 'g1',
    stem: 'stem-1',
    timestamp: 1,
    text_path: null,
    html_path: null,
    json_path: '/tmp/replay.json',
    output_preview: 'preview',
    action_preview: null,
    display_title: '测试记录',
    related_json_paths: [],
    has_behavior_choice_insert: false,
    model_name: 'gpt-test',
    duration_ms: 10,
    prompt_tokens: 1,
    completion_tokens: 1,
    total_tokens: 2,
    size: 10,
    modified_at: 1,
    ...overrides,
  }
}

function makeReplayResponse(
  overrides: Partial<ReasoningReplayResponse> = {}
): ReasoningReplayResponse {
  return {
    schema_version: 6,
    success: true,
    output_items: [],
    generation_attempts: [],
    model_name: 'gpt-test',
    prompt_tokens: 10,
    completion_tokens: 4,
    total_tokens: 14,
    prompt_cache_hit_tokens: 0,
    prompt_cache_miss_tokens: 0,
    duration_ms: 12,
    error: null,
    ...overrides,
  }
}

function EditorColumnHarness({
  initialItems,
  onClose = vi.fn(),
}: {
  initialItems: EditableReplayItem[]
  onClose?: () => void
}) {
  const [items, setItems] = useState(initialItems)
  return (
    <ReplayItemEditorColumn
      selectedTitle="群聊记录"
      items={items}
      updateItem={(id, patch) => {
        setItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)))
      }}
      addItem={() => {
        setItems((current) => [...current, createBlankReplayItem()])
      }}
      deleteItem={(id) => {
        setItems((current) => current.filter((item) => item.id !== id))
      }}
      onClose={onClose}
    />
  )
}

function renderPanel(
  props: Partial<ComponentProps<typeof ReasoningReplayPanel>> = {}
) {
  const onClose = vi.fn()
  const view = render(
    <ReasoningReplayPanel
      open
      onClose={onClose}
      selected={makeFile()}
      selectedTitle="测试记录"
      structuredPrompt={makePrompt()}
      items={[makeReplayItem()]}
      {...props}
    />
  )
  return { ...view, onClose }
}

async function waitUntilReplayEnabled() {
  await waitFor(() => {
    expect(screen.getByRole('button', { name: '执行重放' })).toBeEnabled()
  })
}

beforeEach(() => {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }

  vi.mocked(getModelConfig).mockResolvedValue({
    models: [{ name: 'gpt-test' }, { name: 'other-model' }],
  })
  vi.mocked(replayReasoningPrompt).mockResolvedValue(makeReplayResponse())
})

afterEach(() => {
  cleanup()
})

describe('ReplayItemEditorColumn', () => {
  it('空列表展示不可重放提示，添加/关闭按钮可用', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<EditorColumnHarness initialItems={[]} onClose={onClose} />)

    expect(screen.getByText('这条记录没有可重放的 request Items。')).toBeInTheDocument()
    expect(screen.getByText('群聊记录')).toBeInTheDocument()
    expect(screen.getByText('0 个')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '添加 Item' }))
    expect(screen.getByText('1 个')).toBeInTheDocument()
    expect(screen.getByText('正文')).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '退出重放编辑' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('编辑正文、切换 JSON，并同步 item_type', async () => {
    const user = userEvent.setup()
    render(
      <EditorColumnHarness
        initialItems={[
          makeReplayItem({
            jsonText: JSON.stringify(
              {
                item_type: 'UserMessageItem',
                meta: {
                  item_id: 'item-1',
                  logical_turn_id: null,
                  timestamp: '2026-08-06T00:00:00.000Z',
                },
                parts: [
                  { type: 'text', text: '第一段' },
                  { type: 'image', image_format: 'png' },
                  { type: 'text', text: '第二段' },
                ],
              },
              null,
              2
            ),
          }),
        ]}
      />
    )

    expect(screen.getByText('正文 1')).toBeInTheDocument()
    expect(screen.getByText('正文 2')).toBeInTheDocument()
    fireEvent.change(screen.getByDisplayValue('第一段'), { target: { value: '改写后' } })
    expect(screen.getByDisplayValue('改写后')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'JSON 编辑' }))
    const jsonEditor = screen.getByTestId('replay-json-editor')
    fireEvent.change(jsonEditor, {
      target: {
        value: JSON.stringify(
          {
            item_type: 'SystemMessageItem',
            meta: {
              item_id: 'item-1',
              logical_turn_id: null,
              timestamp: '2026-08-06T00:00:00.000Z',
            },
            parts: [{ type: 'text', text: '系统' }],
          },
          null,
          2
        ),
      },
    })
    expect(screen.getByText('SystemMessageItem')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '正文编辑' }))
    expect(screen.getByRole('textbox')).toHaveValue('系统')
  })

  it('没有可读正文时直接展示 JSON 编辑器，删除 Item 后回到空态', async () => {
    const user = userEvent.setup()
    render(
      <EditorColumnHarness
        initialItems={[
          makeReplayItem({
            jsonText: JSON.stringify({
              item_type: 'UserMessageItem',
              meta: {
                item_id: 'item-1',
                logical_turn_id: null,
                timestamp: '2026-08-06T00:00:00.000Z',
              },
              parts: [{ type: 'image', image_format: 'png' }],
            }),
          }),
        ]}
      />
    )

    expect(screen.getByTestId('replay-json-editor')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'JSON 编辑' })).not.toBeInTheDocument()

    fireEvent.change(screen.getByTestId('replay-json-editor'), {
      target: { value: '{"foo":1}' },
    })
    expect(screen.getByText('UserMessageItem')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '删除第 1 个 Item' }))
    expect(screen.getByText('这条记录没有可重放的 request Items。')).toBeInTheDocument()
  })
})

describe('ReplayResultItem', () => {
  it('请求失败时展示错误，缺省文案可兜底', () => {
    const failed: ReplayRunResult = {
      id: 'r1',
      index: 2,
      result: null,
      error: '网络断开',
    }
    const { rerender } = render(<ReplayResultItem item={failed} />)
    expect(screen.getByText('#2 失败')).toBeInTheDocument()
    expect(screen.getByText('网络断开')).toBeInTheDocument()

    rerender(<ReplayResultItem item={{ ...failed, error: null }} />)
    expect(screen.getByText('请求重放接口失败')).toBeInTheDocument()
  })

  it('成功结果展示 token 摘要与输出 Items，失败结果展示接口 error', () => {
    render(
      <ReplayResultItem
        item={{
          id: 'ok',
          index: 1,
          error: null,
          result: makeReplayResponse({
            success: true,
            model_name: 'gpt-test',
            prompt_cache_hit_tokens: 3,
            duration_ms: 1500,
            output_items: [
              {
                item_type: 'AssistantMessageItem',
                meta: {
                  item_id: 'out-1',
                  logical_turn_id: null,
                  timestamp: '2026-08-06T00:00:00.000Z',
                },
                parts: [{ type: 'text', text: '重放回答' }],
              },
            ],
          }),
        }}
      />
    )
    expect(screen.getByText('#1 完成')).toBeInTheDocument()
    expect(screen.getByText('gpt-test')).toBeInTheDocument()
    expect(screen.getByText(/输入 10 · 输出 4 · 总计 14 · 缓存命中 3 · 耗时 1.50 s/)).toBeInTheDocument()
    expect(screen.getByText('重放输出')).toBeInTheDocument()
    expect(screen.getByText('重放回答')).toBeInTheDocument()
    cleanup()

    render(
      <ReplayResultItem
        item={{
          id: 'fail',
          index: 3,
          error: null,
          result: makeReplayResponse({
            success: false,
            error: '模型拒绝',
            duration_ms: 0,
          }),
        }}
      />
    )
    expect(screen.getByText('#3 失败')).toBeInTheDocument()
    expect(screen.getByText('模型拒绝')).toBeInTheDocument()
  })
})

describe('ReasoningReplayPanel', () => {
  it('关闭时隐藏边栏且不请求模型配置', () => {
    renderPanel({ open: false })
    expect(screen.getByRole('complementary', { hidden: true })).toHaveAttribute('aria-hidden', 'true')
    expect(getModelConfig).not.toHaveBeenCalled()
  })

  it('打开后加载模型并选中快照中的模型', async () => {
    const user = userEvent.setup()
    renderPanel()
    await waitUntilReplayEnabled()

    expect(screen.getByRole('combobox', { name: '模型名称' })).toHaveTextContent('gpt-test')
    await user.click(screen.getByRole('combobox', { name: '模型名称' }))
    expect(await screen.findByRole('option', { name: 'other-model' })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'other-model' }))
    expect(screen.getByRole('combobox', { name: '模型名称' })).toHaveTextContent('other-model')
  })

  it('快照模型不在列表时回退到第一项，并支持包裹 config 的载荷', async () => {
    vi.mocked(getModelConfig).mockResolvedValue({
      config: { models: [{ name: 'first-model' }, { name: 'second-model' }] },
    })
    renderPanel({
      selected: makeFile({ model_name: 'missing-model' }),
      structuredPrompt: makePrompt({ metadata: { model_name: 'missing-model' } }),
    })
    await waitUntilReplayEnabled()
    expect(screen.getByRole('combobox', { name: '模型名称' })).toHaveTextContent('first-model')
  })

  it('没有 metadata 时用 selected.model_name 作为快照', async () => {
    renderPanel({
      structuredPrompt: makePrompt({ metadata: undefined }),
      selected: makeFile({ model_name: 'other-model' }),
    })
    await waitUntilReplayEnabled()
    expect(screen.getByRole('combobox', { name: '模型名称' })).toHaveTextContent('other-model')
  })

  it('模型列表为空时禁用执行并展示提示', async () => {
    vi.mocked(getModelConfig).mockResolvedValue({ models: [] })
    renderPanel()
    await waitFor(() => {
      expect(screen.getByText('model_config.toml 中没有可选模型。')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '执行重放' })).toBeDisabled()
  })

  it('加载模型失败时展示错误，非 Error 使用兜底文案', async () => {
    vi.mocked(getModelConfig).mockRejectedValue(new Error('配置服务宕机'))
    renderPanel()
    await waitFor(() => {
      expect(screen.getByText('配置服务宕机')).toBeInTheDocument()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '加载模型列表失败', variant: 'destructive' })
    )
    cleanup()
    toastMock.mockClear()

    vi.mocked(getModelConfig).mockRejectedValue('oops')
    renderPanel()
    await waitFor(() => {
      expect(screen.getByText('读取模型配置失败')).toBeInTheDocument()
    })
  })

  it('卸载后忽略迟到的模型配置成功', async () => {
    const pending = deferred<Record<string, unknown>>()
    vi.mocked(getModelConfig).mockReturnValue(pending.promise)
    const { unmount } = renderPanel()
    unmount()
    pending.resolve({ models: [{ name: 'late-model' }] })
    await Promise.resolve()
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('卸载后忽略迟到的模型配置失败', async () => {
    const pending = deferred<Record<string, unknown>>()
    vi.mocked(getModelConfig).mockReturnValue(pending.promise)
    const { unmount } = renderPanel()
    unmount()
    pending.reject(new Error('too late'))
    await Promise.resolve()
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('关闭边栏会取消进行中的模型加载', async () => {
    const pending = deferred<Record<string, unknown>>()
    vi.mocked(getModelConfig).mockReturnValue(pending.promise)
    const { rerender, onClose } = renderPanel({
      selected: makeFile({ model_name: null }),
      structuredPrompt: makePrompt({ metadata: {} }),
    })
    expect(screen.getByText('加载模型中...')).toBeInTheDocument()

    rerender(
      <ReasoningReplayPanel
        open={false}
        onClose={onClose}
        selected={makeFile({ model_name: null })}
        selectedTitle="测试记录"
        structuredPrompt={makePrompt({ metadata: {} })}
        items={[makeReplayItem()]}
      />
    )
    pending.resolve({ models: [{ name: 'late-model' }] })
    await Promise.resolve()
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('没有 Items 时禁用执行重放', async () => {
    renderPanel({ items: [] })
    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: '模型名称' })).not.toBeDisabled()
    })
    expect(screen.getByRole('button', { name: '执行重放' })).toBeDisabled()
    expect(
      screen.getByText('执行重放后，模型返回的完整 output Items 会显示在这里。')
    ).toBeInTheDocument()
  })

  it('重放次数无效时 toast 提示', async () => {
    const user = userEvent.setup()
    renderPanel()
    await waitUntilReplayEnabled()

    fireEvent.change(screen.getByLabelText('次数'), { target: { value: '0' } })
    await user.click(screen.getByRole('button', { name: '执行重放' }))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '重放次数无效', variant: 'destructive' })
    )

    fireEvent.change(screen.getByLabelText('次数'), { target: { value: '21' } })
    await user.click(screen.getByRole('button', { name: '执行重放' }))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '重放次数无效' })
    )

    fireEvent.change(screen.getByLabelText('次数'), { target: { value: '1.5' } })
    await user.click(screen.getByRole('button', { name: '执行重放' }))
    expect(replayReasoningPrompt).not.toHaveBeenCalled()
  })

  it('Item JSON 无效时 toast 提示', async () => {
    const user = userEvent.setup()
    renderPanel({
      items: [{ id: 'bad', itemType: 'UserMessageItem', jsonText: '{' }],
    })
    await waitUntilReplayEnabled()
    await user.click(screen.getByRole('button', { name: '执行重放' }))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Item JSON 无效',
        description: '第 1 个 Item 不是有效 JSON',
        variant: 'destructive',
      })
    )
    expect(replayReasoningPrompt).not.toHaveBeenCalled()
  })

  it('执行重放成功后展示结果，并带上温度、token 与工具定义', async () => {
    const user = userEvent.setup()
    vi.mocked(replayReasoningPrompt).mockResolvedValue(
      makeReplayResponse({
        output_items: [
          {
            item_type: 'AssistantMessageItem',
            meta: {
              item_id: 'out-1',
              logical_turn_id: null,
              timestamp: '2026-08-06T00:00:00.000Z',
            },
            parts: [{ type: 'text', text: '模型输出' }],
          },
        ],
      })
    )
    renderPanel({
      structuredPrompt: makePrompt({
        tool_definitions: [{ name: 'lookup' }, 'skip', null],
      }),
    })
    await waitUntilReplayEnabled()

    fireEvent.change(screen.getByLabelText('温度'), { target: { value: '0.2' } })
    fireEvent.change(screen.getByLabelText('最大 Token'), { target: { value: '64' } })
    await user.click(screen.getByRole('button', { name: '执行重放' }))

    await waitFor(() => {
      expect(screen.getByText('#1 完成')).toBeInTheDocument()
    })
    expect(screen.getByText('模型输出')).toBeInTheDocument()
    expect(replayReasoningPrompt).toHaveBeenCalledWith(
      expect.objectContaining({
        source_path: '/tmp/replay.json',
        stage: 'planner',
        model_name: 'gpt-test',
        item_schema_version: 1,
        temperature: 0.2,
        max_tokens: 64,
        tool_definitions: [{ name: 'lookup' }],
      })
    )
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '批量重放完成',
        description: '成功 1/1 次。',
        variant: 'default',
      })
    )
  })

  it('接口返回失败与抛错分别记入结果，批量 toast 用 destructive', async () => {
    const user = userEvent.setup()
    vi.mocked(replayReasoningPrompt)
      .mockResolvedValueOnce(makeReplayResponse({ success: false, error: '内容过滤' }))
      .mockRejectedValueOnce(new Error('网关超时'))
      .mockRejectedValueOnce('boom')

    renderPanel()
    await waitUntilReplayEnabled()
    fireEvent.change(screen.getByLabelText('次数'), { target: { value: '3' } })
    await user.click(screen.getByRole('button', { name: '执行重放' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '批量重放完成',
          description: '成功 0/3 次。',
          variant: 'destructive',
        })
      )
    })
    expect(screen.getByText('#1 失败')).toBeInTheDocument()
    expect(screen.getByText('内容过滤')).toBeInTheDocument()
    expect(screen.getByText('#2 失败')).toBeInTheDocument()
    expect(screen.getByText('网关超时')).toBeInTheDocument()
    expect(screen.getByText('#3 失败')).toBeInTheDocument()
    expect(screen.getByText('请求重放接口失败')).toBeInTheDocument()
  })

  it('selected 为空时用 structuredPrompt.request.kind 作为 stage', async () => {
    const user = userEvent.setup()
    renderPanel({
      selected: null,
      structuredPrompt: makePrompt({ request: { kind: 'replyer' } }),
    })
    await waitUntilReplayEnabled()
    await user.click(screen.getByRole('button', { name: '执行重放' }))
    await waitFor(() => {
      expect(replayReasoningPrompt).toHaveBeenCalledWith(
        expect.objectContaining({
          source_path: null,
          stage: 'replyer',
          temperature: null,
          max_tokens: null,
        })
      )
    })
  })

  it('提交中展示进度并禁用关闭，完成后恢复', async () => {
    const user = userEvent.setup()
    const pending = deferred<ReasoningReplayResponse>()
    vi.mocked(replayReasoningPrompt).mockReturnValue(pending.promise)
    const { onClose } = renderPanel()
    await waitUntilReplayEnabled()
    await user.click(screen.getByRole('button', { name: '执行重放' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '执行中 1/1' })).toBeDisabled()
    })
    expect(screen.getByText(/第 1 次/)).toBeInTheDocument()
    const closeButton = screen.getByRole('button', { name: '关闭重放边栏' })
    expect(closeButton).toBeDisabled()
    await user.click(closeButton)
    expect(onClose).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('次数'), { target: { value: '' } })
    expect(screen.getByRole('button', { name: '执行中 1/?' })).toBeInTheDocument()

    pending.resolve(makeReplayResponse())
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '执行重放' })).toBeEnabled()
    })
    await user.click(screen.getByRole('button', { name: '关闭重放边栏' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('切换记录时重置表单与结果', async () => {
    const user = userEvent.setup()
    const { rerender, onClose } = renderPanel()
    await waitUntilReplayEnabled()
    fireEvent.change(screen.getByLabelText('温度'), { target: { value: '0.9' } })
    await user.click(screen.getByRole('button', { name: '执行重放' }))
    await waitFor(() => {
      expect(screen.getByText('#1 完成')).toBeInTheDocument()
    })

    rerender(
      <ReasoningReplayPanel
        open
        onClose={onClose}
        selected={makeFile({ stem: 'stem-2', json_path: '/tmp/b.json', model_name: 'other-model' })}
        selectedTitle="另一条记录"
        structuredPrompt={makePrompt({ metadata: { model_name: 'other-model' } })}
        items={[makeReplayItem()]}
      />
    )
    await waitUntilReplayEnabled()
    expect(screen.getByLabelText('温度')).toHaveValue(null)
    expect(screen.getByLabelText('次数')).toHaveValue(1)
    expect(
      screen.getByText('执行重放后，模型返回的完整 output Items 会显示在这里。')
    ).toBeInTheDocument()
    expect(screen.getByText('另一条记录')).toBeInTheDocument()
  })

  it('快照为空时加载过程展示占位文案', () => {
    const pending = deferred<Record<string, unknown>>()
    vi.mocked(getModelConfig).mockReturnValue(pending.promise)
    renderPanel({
      selected: makeFile({ model_name: null }),
      structuredPrompt: makePrompt({ metadata: {} }),
    })
    expect(screen.getByText('加载模型中...')).toBeInTheDocument()
  })
})
