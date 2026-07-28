import { MutationObserver, QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import { toast } from '@/hooks/use-toast'

import { createQueryClient, queryClient } from '../query'

vi.mock('@/hooks/use-toast', () => ({
  toast: vi.fn(),
}))

const toastMock = vi.mocked(toast)

/** 通过 MutationObserver 直接在 QueryClient 上执行一次 mutation（不经过 React 渲染） */
function runMutation(
  client: QueryClient,
  mutationFn: () => Promise<unknown>,
  meta?: { suppressErrorToast?: boolean; errorTitle?: string }
): Promise<unknown> {
  const observer = new MutationObserver(client, { mutationFn, meta })
  return observer.mutate()
}

describe('createQueryClient', () => {
  it('查询默认不重试、不随窗口聚焦刷新，staleTime 为 30 秒', () => {
    const client = createQueryClient()
    const defaults = client.getDefaultOptions()

    expect(defaults.queries).toMatchObject({
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    })
    expect(defaults.mutations).toMatchObject({ retry: false })
  })

  it('mutation 失败时默认弹出破坏性 toast，标题为「操作失败」', async () => {
    const client = createQueryClient()

    await expect(
      runMutation(client, async () => {
        throw new Error('写入配置失败')
      })
    ).rejects.toThrow('写入配置失败')

    expect(toastMock).toHaveBeenCalledTimes(1)
    expect(toastMock).toHaveBeenCalledWith({
      title: '操作失败',
      description: '写入配置失败',
      variant: 'destructive',
    })
  })

  it('meta.errorTitle 可定制全局错误 toast 的标题', async () => {
    const client = createQueryClient()

    await expect(
      runMutation(
        client,
        async () => {
          throw new Error('磁盘已满')
        },
        { errorTitle: '保存表达方式失败' }
      )
    ).rejects.toThrow('磁盘已满')

    expect(toastMock).toHaveBeenCalledWith({
      title: '保存表达方式失败',
      description: '磁盘已满',
      variant: 'destructive',
    })
  })

  it('meta.suppressErrorToast 为 true 时跳过全局错误 toast', async () => {
    const client = createQueryClient()

    await expect(
      runMutation(
        client,
        async () => {
          throw new Error('由调用方自行处理')
        },
        { suppressErrorToast: true }
      )
    ).rejects.toThrow('由调用方自行处理')

    expect(toastMock).not.toHaveBeenCalled()
  })

  it('非 Error 的拒绝值经 String() 转换后展示在 toast 描述中', async () => {
    const client = createQueryClient()

    await expect(runMutation(client, () => Promise.reject('后端拒绝'))).rejects.toBe('后端拒绝')

    expect(toastMock).toHaveBeenCalledWith({
      title: '操作失败',
      description: '后端拒绝',
      variant: 'destructive',
    })
  })

  it('mutation 成功时不触发任何 toast', async () => {
    const client = createQueryClient()

    await expect(runMutation(client, async () => 'ok')).resolves.toBe('ok')

    expect(toastMock).not.toHaveBeenCalled()
  })
})

describe('queryClient 单例', () => {
  it('是携带同一套默认配置的 QueryClient 实例', () => {
    expect(queryClient).toBeInstanceOf(QueryClient)
    expect(queryClient.getDefaultOptions().queries).toMatchObject({
      retry: false,
      refetchOnWindowFocus: false,
    })
  })
})
