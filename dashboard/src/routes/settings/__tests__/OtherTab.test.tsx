import type { ReactNode } from 'react'

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/http'

import { OtherTab } from '../OtherTab'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  toast: vi.fn(),
  backendPost: vi.fn(),
  clearLogs: vi.fn(),
  clearLocalCache: vi.fn(() => ({ clearedKeys: ['one', 'two'] })),
  exportSettings: vi.fn(() => ({ theme: 'dark', logCacheSize: 1200 })),
  getSetting: vi.fn((key: string) => {
    const values: Record<string, string | number | boolean> = {
      logCacheSize: 1200,
      wsReconnectInterval: 4000,
      wsMaxReconnectAttempts: 8,
      dataSyncInterval: 45,
      enableAvatarFetch: true,
      enableFocusCompanion: false,
      alwaysShowUpdateNotice: false,
    }
    return values[key]
  }),
  getStorageUsage: vi.fn(() => ({ used: 2048, items: 6 })),
  importSettings: vi.fn(() => ({
    success: true,
    imported: ['theme', 'logCacheSize'],
    skipped: ['unknown'],
  })),
  resetAllSettings: vi.fn(),
  setSetting: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, variables?: Record<string, unknown>) =>
      variables ? `${key}:${JSON.stringify(variables)}` : key,
  }),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}))

vi.mock('@/lib/log-websocket', () => ({
  logWebSocket: { clearLogs: mocks.clearLogs },
}))

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: { post: mocks.backendPost },
  }
})

vi.mock('@/lib/settings-manager', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/settings-manager')>()
  return {
    ...actual,
    clearLocalCache: mocks.clearLocalCache,
    exportSettings: mocks.exportSettings,
    formatBytes: (value: number) => `${value} bytes`,
    getSetting: mocks.getSetting,
    getStorageUsage: mocks.getStorageUsage,
    importSettings: mocks.importSettings,
    resetAllSettings: mocks.resetAllSettings,
    setSetting: mocks.setSetting,
  }
})

vi.mock('@/components/ui/slider', () => ({
  Slider: ({
    min,
    value,
    onValueChange,
  }: {
    min: number
    value: number[]
    onValueChange: (value: number[]) => void
  }) => {
    const nextValues: Record<number, number> = {
      3: 12,
      10: 60,
      100: 1500,
      1000: 6500,
    }
    return (
      <button
        type="button"
        aria-label={`slider-${min}`}
        data-value={value[0]}
        onClick={() => onValueChange([nextValues[min]])}
      >
        调整
      </button>
    )
  },
}))

vi.mock('@/components/ui/switch', () => ({
  Switch: ({
    checked,
    onCheckedChange,
    ...props
  }: {
    checked: boolean
    onCheckedChange: (checked: boolean) => void
    'aria-label'?: string
    id?: string
  }) => (
    <input
      {...props}
      type="checkbox"
      checked={checked}
      onChange={(event) => onCheckedChange(event.target.checked)}
    />
  ),
}))

vi.mock('@/components/ui/alert-dialog', () => ({
  AlertDialog: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  AlertDialogTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  AlertDialogContent: ({ children }: { children: ReactNode }) => (
    <section data-testid="alert-content">{children}</section>
  ),
  AlertDialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  AlertDialogTitle: ({ children }: { children: ReactNode }) => <h4>{children}</h4>,
  AlertDialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  AlertDialogFooter: ({ children }: { children: ReactNode }) => <footer>{children}</footer>,
  AlertDialogCancel: ({ children }: { children: ReactNode }) => (
    <button type="button">{children}</button>
  ),
  AlertDialogAction: ({ children, onClick }: { children: ReactNode; onClick?: () => void }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
}))

function actionFor(title: string, actionName: string) {
  const content = screen
    .getByRole('heading', { name: title })
    .closest('[data-testid="alert-content"]')
  if (!(content instanceof HTMLElement)) {
    throw new Error(`未找到确认框：${title}`)
  }
  return within(content).getByRole('button', { name: actionName })
}

describe('OtherTab', () => {
  const createObjectUrl = vi.fn(() => 'blob:settings')
  const revokeObjectUrl = vi.fn()

  beforeEach(() => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: createObjectUrl,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectUrl,
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    createObjectUrl.mockClear()
    revokeObjectUrl.mockClear()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('从设置管理器读取初值，并把开关和滑块变更写回对应键', () => {
    render(<OtherTab />)

    expect(screen.getByRole('button', { name: 'slider-100' })).toHaveAttribute('data-value', '1200')
    expect(screen.getByRole('button', { name: 'slider-1000' })).toHaveAttribute(
      'data-value',
      '4000'
    )

    fireEvent.click(screen.getByLabelText('settings.other.enableAvatarFetch'))
    fireEvent.click(screen.getByLabelText('专注陪伴入口'))
    fireEvent.click(screen.getByLabelText('settings.other.alwaysShowUpdateNotice'))
    fireEvent.click(screen.getByRole('button', { name: 'slider-100' }))
    fireEvent.click(screen.getByRole('button', { name: 'slider-10' }))
    fireEvent.click(screen.getByRole('button', { name: 'slider-1000' }))
    fireEvent.click(screen.getByRole('button', { name: 'slider-3' }))

    expect(mocks.setSetting).toHaveBeenCalledWith('enableAvatarFetch', false)
    expect(mocks.setSetting).toHaveBeenCalledWith('enableFocusCompanion', true)
    expect(mocks.setSetting).toHaveBeenCalledWith('alwaysShowUpdateNotice', true)
    expect(mocks.setSetting).toHaveBeenCalledWith('logCacheSize', 1500)
    expect(mocks.setSetting).toHaveBeenCalledWith('dataSyncInterval', 60)
    expect(mocks.setSetting).toHaveBeenCalledWith('wsReconnectInterval', 6500)
    expect(mocks.setSetting).toHaveBeenCalledWith('wsMaxReconnectAttempts', 12)
  })

  it('清理日志和本地缓存，并刷新存储统计', () => {
    render(<OtherTab />)

    fireEvent.click(screen.getByRole('button', { name: 'settings.other.clearLogCacheFn' }))
    fireEvent.click(actionFor('settings.other.confirmClearCache', 'settings.other.confirmClear'))
    fireEvent.click(
      screen.getByRole('button', {
        name: '',
      })
    )

    expect(mocks.clearLogs).toHaveBeenCalledOnce()
    expect(mocks.clearLocalCache).toHaveBeenCalledOnce()
    expect(mocks.getStorageUsage).toHaveBeenCalledTimes(3)
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'settings.other.cacheCleared',
        description: 'settings.other.cacheClearedDesc:{"count":2}',
      })
    )
  })

  it('导出设置为 JSON 文件并回收对象 URL', () => {
    render(<OtherTab />)

    fireEvent.click(screen.getByRole('button', { name: 'settings.other.exportSettings' }))

    expect(mocks.exportSettings).toHaveBeenCalledOnce()
    expect(createObjectUrl).toHaveBeenCalledWith(expect.any(Blob))
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:settings')
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'settings.other.exportSuccess' })
    )
  })

  it('导入合法设置后刷新状态，并提示跳过项和主题刷新', async () => {
    render(<OtherTab />)
    const input = document.querySelector('input[type="file"]')
    if (!(input instanceof HTMLInputElement)) {
      throw new Error('未找到设置导入输入框')
    }

    fireEvent.change(input, {
      target: {
        files: [new File(['{"theme":"dark"}'], 'settings.json', { type: 'application/json' })],
      },
    })

    await waitFor(() => expect(mocks.importSettings).toHaveBeenCalledWith({ theme: 'dark' }))
    expect(mocks.getSetting).toHaveBeenCalledWith('alwaysShowUpdateNotice')
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'settings.other.importSuccess',
        description:
          'settings.other.importSuccessDesc:{"imported":2}' +
          'settings.other.importSkippedSuffix:{"skipped":1}',
      })
    )
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'settings.other.importRefreshHint' })
    )
    expect(input.value).toBe('')
  })

  it('拒绝无法解析的导入文件并显示破坏性提示', async () => {
    render(<OtherTab />)
    const input = document.querySelector('input[type="file"]')
    if (!(input instanceof HTMLInputElement)) {
      throw new Error('未找到设置导入输入框')
    }

    fireEvent.change(input, {
      target: {
        files: [new File(['not-json'], 'invalid.json', { type: 'application/json' })],
      },
    })

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'settings.other.importFailed',
          description: 'settings.other.importInvalidDesc',
          variant: 'destructive',
        })
      )
    )
    expect(mocks.importSettings).not.toHaveBeenCalled()
  })

  it('重置前端设置后恢复默认值，并成功重新进入配置向导', async () => {
    mocks.backendPost.mockResolvedValue({ success: true })
    render(<OtherTab />)

    fireEvent.click(
      actionFor('settings.other.confirmResetAll', 'settings.other.resetAllSettingsConfirm')
    )
    expect(mocks.resetAllSettings).toHaveBeenCalledOnce()
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'settings.other.resetDone' })
    )

    vi.useFakeTimers()
    fireEvent.click(
      actionFor('settings.other.confirmRerunSetup', 'settings.other.resetAllSettingsConfirm')
    )
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(mocks.backendPost).toHaveBeenCalledWith('/api/webui/setup/reset', {
      errorMessage: 'settings.other.clearStorageFailed',
    })

    act(() => vi.advanceTimersByTime(1000))
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/setup' })
  })

  it('重置配置遇到 HTTP 错误时展示后端消息', async () => {
    mocks.backendPost.mockRejectedValue(new ApiError('后端拒绝重置', { status: 409 }))
    render(<OtherTab />)

    fireEvent.click(
      actionFor('settings.other.confirmRerunSetup', 'settings.other.resetAllSettingsConfirm')
    )

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'settings.other.resetFailed',
          description: '后端拒绝重置',
          variant: 'destructive',
        })
      )
    )
  })
})
