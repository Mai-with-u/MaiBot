import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useConfigForm } from '../useConfigForm'

interface RawConfig {
  config?: { section?: { value: number } }
}
interface Schema {
  nested?: { section?: { fields: string[] } }
}
interface Draft {
  value: number
}

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

const seed = (config: RawConfig): Draft => ({ value: config.config?.section?.value ?? 0 })

function renderConfigForm(overrides: {
  loadConfig?: () => Promise<RawConfig>
  loadSchema?: () => Promise<Schema>
} = {}) {
  const loadConfig = overrides.loadConfig ?? vi.fn(async () => ({ config: { section: { value: 1 } } }))
  return renderHook(
    () =>
      useConfigForm<Draft, RawConfig, Schema>({
        queryKey: ['test-config'],
        loadConfig,
        loadSchema: overrides.loadSchema,
        seed,
      }),
    { wrapper: makeWrapper() },
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('useConfigForm', () => {
  it('config 到达后 seed 草稿，初始 isDirty 为 false', async () => {
    const { result } = renderConfigForm()
    expect(result.current.isLoading).toBe(true)
    expect(result.current.draft).toBeUndefined()

    await waitFor(() => expect(result.current.draft).toEqual({ value: 1 }))
    expect(result.current.isDirty).toBe(false)
    expect(result.current.isLoading).toBe(false)
  })

  it('setDraft 改值后 isDirty 翻为 true', async () => {
    const { result } = renderConfigForm()
    await waitFor(() => expect(result.current.draft).toEqual({ value: 1 }))

    act(() => result.current.setDraft({ value: 2 }))
    expect(result.current.draft).toEqual({ value: 2 })
    expect(result.current.isDirty).toBe(true)
  })

  it('setDraft 支持更新函数', async () => {
    const { result } = renderConfigForm()
    await waitFor(() => expect(result.current.draft).toEqual({ value: 1 }))

    act(() => result.current.setDraft((prev) => ({ value: prev.value + 10 })))
    expect(result.current.draft).toEqual({ value: 11 })
    expect(result.current.isDirty).toBe(true)
  })

  it('reset 把草稿还原到 seed 快照', async () => {
    const { result } = renderConfigForm()
    await waitFor(() => expect(result.current.draft).toEqual({ value: 1 }))

    act(() => result.current.setDraft({ value: 99 }))
    expect(result.current.isDirty).toBe(true)

    act(() => result.current.reset())
    expect(result.current.draft).toEqual({ value: 1 })
    expect(result.current.isDirty).toBe(false)
  })

  it('reload 后以服务端最新数据重新 seed 并清脏', async () => {
    let current = 1
    const loadConfig = vi.fn(async () => ({ config: { section: { value: current } } }))
    const { result } = renderConfigForm({ loadConfig })
    await waitFor(() => expect(result.current.draft).toEqual({ value: 1 }))

    act(() => result.current.setDraft({ value: 50 }))
    expect(result.current.isDirty).toBe(true)

    // 服务端值更新后 reload：草稿被服务端新值覆盖、isDirty 归零
    current = 7
    act(() => result.current.reload())
    await waitFor(() => expect(result.current.draft).toEqual({ value: 7 }))
    expect(result.current.isDirty).toBe(false)
    expect(loadConfig).toHaveBeenCalledTimes(2)
  })

  it('未提供 loadSchema 时 schema 为 undefined 且不阻塞草稿', async () => {
    const { result } = renderConfigForm()
    await waitFor(() => expect(result.current.draft).toEqual({ value: 1 }))
    expect(result.current.schema).toBeUndefined()
  })

  it('提供 loadSchema 时暴露 schema，且 schema 失败不阻塞草稿', async () => {
    const okSchema = renderConfigForm({ loadSchema: vi.fn(async () => ({ nested: { section: { fields: ['a'] } } })) })
    await waitFor(() => expect(okSchema.result.current.schema).toEqual({ nested: { section: { fields: ['a'] } } }))
    expect(okSchema.result.current.draft).toEqual({ value: 1 })

    // schema 失败：草稿仍 seed 成功
    const failSchema = renderConfigForm({ loadSchema: vi.fn(async () => { throw new Error('schema 挂了') }) })
    await waitFor(() => expect(failSchema.result.current.draft).toEqual({ value: 1 }))
    expect(failSchema.result.current.schema).toBeUndefined()
  })

  it('config 加载失败时 error 暴露、草稿保持 undefined', async () => {
    const loadConfig = vi.fn(async () => { throw new Error('config 挂了') })
    const { result } = renderConfigForm({ loadConfig })
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.draft).toBeUndefined()
  })
})
