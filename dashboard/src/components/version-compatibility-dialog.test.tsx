import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18next from '@/i18n'
import {
  getVersionCompatibility,
  type VersionCompatibilityResult,
} from '@/lib/version-compatibility-api'

import { VersionCompatibilityDialog } from './version-compatibility-dialog'

vi.mock('@/lib/version-compatibility-api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/version-compatibility-api')>()
  return {
    ...original,
    getVersionCompatibility: vi.fn(),
  }
})

const getVersionCompatibilityMock = vi.mocked(getVersionCompatibility)

async function renderDialog(result: VersionCompatibilityResult) {
  getVersionCompatibilityMock.mockResolvedValue(result)
  render(<VersionCompatibilityDialog />)
  await act(async () => {
    await Promise.resolve()
  })
}

describe('VersionCompatibilityDialog', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    localStorage.setItem('maibot-locale', 'zh')
    await i18next.changeLanguage('zh')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
    localStorage.removeItem('maibot-locale')
  })

  it('WebUI 版本过低时要求等待 5 秒才能关闭', async () => {
    await renderDialog({
      status: 'webui_outdated',
      main_program_version: '1.2.3',
      webui_version: '2.0.0.dev9',
      required_webui_version: '2.0.0.dev10',
    })

    expect(screen.getByText('WebUI 版本过低')).toBeInTheDocument()
    const confirmButton = screen.getByRole('button', { name: '5 秒后可确认' })
    expect(confirmButton).toBeDisabled()

    act(() => vi.advanceTimersByTime(5000))

    const enabledButton = screen.getByRole('button', { name: '关闭提示并继续' })
    expect(enabledButton).toBeEnabled()
    fireEvent.click(enabledButton)
    expect(screen.queryByText('WebUI 版本过低')).not.toBeInTheDocument()
  })

  it('WebUI 跨版本领先时明确要求更新主程序', async () => {
    await renderDialog({
      status: 'main_program_outdated',
      main_program_version: '1.2.3',
      webui_version: '2.0.1',
      required_webui_version: '2.0.0.dev10',
    })

    expect(screen.getByText('主程序版本过低')).toBeInTheDocument()
    expect(screen.getByText('需要更新：MaiBot 主程序')).toBeInTheDocument()
  })

  it('版本匹配时不显示提示', async () => {
    await renderDialog({
      status: 'compatible',
      main_program_version: '1.2.3',
      webui_version: '2.0.0',
      required_webui_version: '2.0.0.dev10',
    })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('兼容性接口不可用时不猜测版本结论', async () => {
    getVersionCompatibilityMock.mockRejectedValue(
      new Error('当前主程序未提供版本兼容性检查接口')
    )

    render(<VersionCompatibilityDialog />)
    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
