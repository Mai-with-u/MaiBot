import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getPluginHomeCards } from '@/lib/plugin-api'
import type { PluginHomeCard } from '@/lib/plugin-api'

import { usePluginHomeCards } from './usePluginHomeCards'

vi.mock('@/lib/plugin-api', () => ({
  getPluginHomeCards: vi.fn().mockResolvedValue([]),
}))

const getPluginHomeCardsMock = vi.mocked(getPluginHomeCards)

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  let reject: (reason?: unknown) => void = () => {}
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function makeHomeCard(id: string): PluginHomeCard {
  return {
    id,
    name: '状态卡片',
    plugin_id: 'demo-plugin',
    title: '运行状态',
    show_title: true,
    description: '展示插件运行状态',
    content: '一切正常',
    link_url: 'https://example.com',
    link_label: '查看详情',
    icon: 'activity',
    width: 'medium',
    order: 1,
    enabled: true,
  }
}

describe('usePluginHomeCards', () => {
  beforeEach(() => {
    getPluginHomeCardsMock.mockReset()
    getPluginHomeCardsMock.mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('挂载后拉取卡片并结束加载', async () => {
    const cards = [makeHomeCard('card-1')]
    getPluginHomeCardsMock.mockResolvedValue(cards)

    const { result } = renderHook(() => usePluginHomeCards())

    await waitFor(() => {
      expect(result.current.isPluginHomeCardsLoading).toBe(false)
      expect(result.current.pluginHomeCards).toEqual(cards)
    })
    expect(getPluginHomeCardsMock).toHaveBeenCalledTimes(1)
  })

  it('请求进行中把 isPluginHomeCardsLoading 置为 true', async () => {
    const pending = createDeferred<PluginHomeCard[]>()
    getPluginHomeCardsMock.mockReturnValue(pending.promise)

    const { result } = renderHook(() => usePluginHomeCards())

    await waitFor(() => {
      expect(result.current.isPluginHomeCardsLoading).toBe(true)
    })
    expect(result.current.pluginHomeCards).toEqual([])

    await act(async () => {
      pending.resolve([makeHomeCard('later')])
      await pending.promise
    })

    await waitFor(() => {
      expect(result.current.isPluginHomeCardsLoading).toBe(false)
      expect(result.current.pluginHomeCards).toEqual([makeHomeCard('later')])
    })
  })

  it('拉取失败时记录错误并回空列表', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    getPluginHomeCardsMock.mockRejectedValue(new Error('网络错误'))

    const { result } = renderHook(() => usePluginHomeCards())

    await waitFor(() => {
      expect(error).toHaveBeenCalledWith('加载插件首页卡片失败:', expect.any(Error))
    })
    expect(result.current.pluginHomeCards).toEqual([])
    expect(result.current.isPluginHomeCardsLoading).toBe(false)
    error.mockRestore()
  })

  it('手动 fetchPluginHomeCards 会重新请求', async () => {
    getPluginHomeCardsMock
      .mockResolvedValueOnce([makeHomeCard('first')])
      .mockResolvedValueOnce([makeHomeCard('second')])

    const { result } = renderHook(() => usePluginHomeCards())

    await waitFor(() => {
      expect(result.current.pluginHomeCards).toEqual([makeHomeCard('first')])
    })

    await act(async () => {
      await result.current.fetchPluginHomeCards()
    })

    expect(result.current.pluginHomeCards).toEqual([makeHomeCard('second')])
    expect(getPluginHomeCardsMock).toHaveBeenCalledTimes(2)
  })
})
