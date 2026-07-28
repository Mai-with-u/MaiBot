import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { formatRelativeTime, formatTimestamp, useAutoRefresh, useChatNameMap } from './use-monitor'
import { getChatList } from '@/lib/expression-api'
import type { ChatInfo } from '@/types/expression'

// 拦截表达方式 API，避免真实网络请求
vi.mock('@/lib/expression-api', () => ({
  getChatList: vi.fn(),
}))

const mockGetChatList = vi.mocked(getChatList)

/** 构造一个最小可用的聊天信息对象 */
function makeChat(overrides: Partial<ChatInfo> = {}): ChatInfo {
  return {
    chat_id: 'chat-1',
    chat_name: '测试群',
    platform: 'qq',
    is_group: true,
    use_expression: true,
    enable_learning: true,
    ...overrides,
  }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('useChatNameMap', () => {
  it('挂载后拉取聊天列表并构建 chat_id -> chat_name 映射', async () => {
    mockGetChatList.mockResolvedValue([
      makeChat(),
      makeChat({ chat_id: 'chat-2', chat_name: '小明的私聊', is_group: false }),
    ])
    const { result } = renderHook(() => useChatNameMap())

    // 初始处于加载态
    expect(result.current.loading).toBe(true)

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(mockGetChatList).toHaveBeenCalledTimes(1)
    expect(result.current.chatNameMap.get('chat-1')).toBe('测试群')
    expect(result.current.chatNameMap.get('chat-2')).toBe('小明的私聊')
  })

  it('getChatName 命中映射返回聊天名称，未命中回退为 chat_id 本身', async () => {
    mockGetChatList.mockResolvedValue([makeChat()])
    const { result } = renderHook(() => useChatNameMap())
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.getChatName('chat-1')).toBe('测试群')
    expect(result.current.getChatName('unknown-id')).toBe('unknown-id')
  })

  it('拉取失败时打日志、结束加载态且映射保持为空', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const loadError = new Error('网络错误')
    mockGetChatList.mockRejectedValue(loadError)

    const { result } = renderHook(() => useChatNameMap())
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.chatNameMap.size).toBe(0)
    expect(errorSpy).toHaveBeenCalledWith('加载聚天列表失败:', loadError)
  })

  it('reload 重新拉取并用新数据覆盖映射', async () => {
    mockGetChatList.mockResolvedValue([makeChat()])
    const { result } = renderHook(() => useChatNameMap())
    await waitFor(() => expect(result.current.loading).toBe(false))

    // 后端数据更新后手动 reload
    mockGetChatList.mockResolvedValue([makeChat({ chat_id: 'chat-3', chat_name: '新群聊' })])
    await act(async () => {
      await result.current.reload()
    })

    expect(mockGetChatList).toHaveBeenCalledTimes(2)
    expect(result.current.chatNameMap.get('chat-3')).toBe('新群聊')
    // 新映射整体替换旧映射
    expect(result.current.chatNameMap.has('chat-1')).toBe(false)
  })
})

describe('formatTimestamp', () => {
  it('将秒级时间戳转换为 zh-CN 本地时间字符串', () => {
    // 1700000000 秒 = 2023-11-14T22:13:20Z，任何时区下年份都是 2023
    const text = formatTimestamp(1700000000)
    expect(text).toContain('2023')
    // zh-CN 日期格式使用斜杠分隔：yyyy/M/d
    expect(text).toMatch(/2023\/11\/1[45]/)
  })
})

describe('formatRelativeTime', () => {
  it('按时间差返回「刚刚 / 分钟前 / 小时前 / 天前」', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-26T12:00:00Z'))
    const now = Date.now() / 1000

    expect(formatRelativeTime(now - 30)).toBe('刚刚')
    expect(formatRelativeTime(now - 59)).toBe('刚刚')
    expect(formatRelativeTime(now - 60)).toBe('1 分钟前')
    expect(formatRelativeTime(now - 150)).toBe('2 分钟前')
    expect(formatRelativeTime(now - 3600)).toBe('1 小时前')
    expect(formatRelativeTime(now - 7500)).toBe('2 小时前')
    expect(formatRelativeTime(now - 86400)).toBe('1 天前')
    expect(formatRelativeTime(now - 200000)).toBe('2 天前')
  })
})

describe('useAutoRefresh', () => {
  it('启用时按指定间隔周期性触发回调', () => {
    vi.useFakeTimers()
    const callback = vi.fn()
    renderHook(() => useAutoRefresh(true, callback, 1000))

    // 未到间隔不触发
    act(() => vi.advanceTimersByTime(999))
    expect(callback).not.toHaveBeenCalled()

    act(() => vi.advanceTimersByTime(1))
    expect(callback).toHaveBeenCalledTimes(1)

    // 继续推进两个周期，累计触发三次
    act(() => vi.advanceTimersByTime(2000))
    expect(callback).toHaveBeenCalledTimes(3)
  })

  it('未启用时不注册定时器', () => {
    vi.useFakeTimers()
    const callback = vi.fn()
    renderHook(() => useAutoRefresh(false, callback, 1000))

    act(() => vi.advanceTimersByTime(5000))
    expect(callback).not.toHaveBeenCalled()
  })

  it('默认间隔为 10000 毫秒', () => {
    vi.useFakeTimers()
    const callback = vi.fn()
    renderHook(() => useAutoRefresh(true, callback))

    act(() => vi.advanceTimersByTime(9999))
    expect(callback).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(1))
    expect(callback).toHaveBeenCalledTimes(1)
  })

  it('卸载时清理定时器，不再触发回调', () => {
    vi.useFakeTimers()
    const callback = vi.fn()
    const { unmount } = renderHook(() => useAutoRefresh(true, callback, 1000))

    act(() => vi.advanceTimersByTime(1000))
    expect(callback).toHaveBeenCalledTimes(1)

    unmount()
    act(() => vi.advanceTimersByTime(5000))
    expect(callback).toHaveBeenCalledTimes(1)
  })

  it('enabled 由 true 切到 false 时停止轮询', () => {
    vi.useFakeTimers()
    const callback = vi.fn()
    const { rerender } = renderHook(({ enabled }) => useAutoRefresh(enabled, callback, 1000), {
      initialProps: { enabled: true },
    })

    act(() => vi.advanceTimersByTime(1000))
    expect(callback).toHaveBeenCalledTimes(1)

    rerender({ enabled: false })
    act(() => vi.advanceTimersByTime(5000))
    expect(callback).toHaveBeenCalledTimes(1)
  })
})
