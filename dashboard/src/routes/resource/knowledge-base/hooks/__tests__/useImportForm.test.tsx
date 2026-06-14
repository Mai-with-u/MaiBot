import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useImportForm } from '../useImportForm'

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))

vi.mock('@/lib/memory-api', () => ({
  getMemoryImportSettings: vi.fn(),
  getMemoryImportPathAliases: vi.fn(),
  getMemoryImportChatTargets: vi.fn(),
  createMemoryUploadImport: vi.fn(),
  createMemoryPasteImport: vi.fn(),
  createMemoryRawScanImport: vi.fn(),
  createMemoryLpmmOpenieImport: vi.fn(),
  createMemoryLpmmConvertImport: vi.fn(),
  createMemoryTemporalBackfillImport: vi.fn(),
  createMemoryMaibotMigrationImport: vi.fn(),
  resolveMemoryImportPath: vi.fn(),
}))

import * as memoryApi from '@/lib/memory-api'

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function renderForm(onCreated = vi.fn()) {
  return {
    onCreated,
    ...renderHook(() => useImportForm({ active: true, onCreated }), { wrapper: makeWrapper() }),
  }
}

beforeEach(() => {
  vi.mocked(memoryApi.getMemoryImportPathAliases).mockResolvedValue({ success: true, path_aliases: {} } as never)
  vi.mocked(memoryApi.getMemoryImportChatTargets).mockResolvedValue({ success: true, data: [] } as never)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('useImportForm', () => {
  describe('默认值 seed', () => {
    it('settings 到达后把公共参数 seed 为服务端默认值（用户未改时）', async () => {
      vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
        success: true,
        settings: { default_file_concurrency: 8 },
      } as never)
      const { result } = renderForm()

      // 初始值为 '2'，settings 解析后被 seed 为 '8'
      expect(result.current.importCommonFileConcurrency).toBe('2')
      await waitFor(() => expect(result.current.importCommonFileConcurrency).toBe('8'))
    })

    it('用户已改过的字段不被服务端默认值覆盖', async () => {
      vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
        success: true,
        settings: { default_file_concurrency: 8 },
      } as never)
      // settings 延迟解析，给用户先改值的时间窗口
      let resolveSettings: (v: unknown) => void = () => {}
      vi.mocked(memoryApi.getMemoryImportSettings).mockReturnValue(
        new Promise((resolve) => {
          resolveSettings = resolve
        }) as never,
      )
      const { result } = renderForm()

      act(() => result.current.setImportCommonFileConcurrency('99'))
      await act(async () => {
        resolveSettings({ success: true, settings: { default_file_concurrency: 8 } })
      })

      // 用户改成的 '99' 不被 seed 的 '8' 覆盖
      await waitFor(() => expect(memoryApi.getMemoryImportSettings).toHaveBeenCalled())
      expect(result.current.importCommonFileConcurrency).toBe('99')
    })
  })

  describe('submitImportByMode 按模式分派', () => {
    beforeEach(() => {
      vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({ success: true, settings: {} } as never)
    })

    it('paste 模式调用 createMemoryPasteImport 并在成功后回调 onCreated', async () => {
      vi.mocked(memoryApi.createMemoryPasteImport).mockResolvedValue({
        success: true,
        task: { task_id: 'task-paste-1' },
      } as never)
      const { result, onCreated } = renderForm()

      act(() => {
        result.current.setImportCreateMode('paste')
        result.current.setPasteContent('要导入的内容')
      })
      await act(async () => {
        await result.current.submitImportByMode()
      })

      expect(memoryApi.createMemoryPasteImport).toHaveBeenCalledOnce()
      expect(memoryApi.createMemoryUploadImport).not.toHaveBeenCalled()
      expect(onCreated).toHaveBeenCalledWith('task-paste-1')
    })

    it('paste 内容为空时拦截，不调用任何 create 接口', async () => {
      const { result, onCreated } = renderForm()

      act(() => result.current.setImportCreateMode('paste'))
      await act(async () => {
        await result.current.submitImportByMode()
      })

      expect(memoryApi.createMemoryPasteImport).not.toHaveBeenCalled()
      expect(onCreated).not.toHaveBeenCalled()
    })
  })

  describe('buildCommonImportPayload', () => {
    beforeEach(() => {
      vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({ success: true, settings: {} } as never)
    })

    it('产出当前公共参数载荷，供队列重试复用', async () => {
      const { result } = renderForm()

      act(() => result.current.setImportCommonLlmEnabled(true))
      const payload = result.current.buildCommonImportPayload()

      expect(payload).toMatchObject({ llm_enabled: true })
    })
  })
})
