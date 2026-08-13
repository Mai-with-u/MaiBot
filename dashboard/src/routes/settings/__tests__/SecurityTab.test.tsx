import type { ReactNode } from 'react'

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SecurityTab } from '../SecurityTab'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  toast: vi.fn(),
  validateToken: vi.fn((token: string) => ({
    isValid: token === 'ValidToken!123',
    rules: [
      {
        id: 'length',
        label: '长度规则',
        passed: token.length >= 8,
      },
      {
        id: 'strength',
        label: '强度规则',
        passed: token === 'ValidToken!123',
      },
    ],
  })),
}))

vi.mock('react-i18next', () => {
  const t = (key: string, variables?: Record<string, unknown>) =>
    variables ? `${key}:${JSON.stringify(variables)}` : key
  return {
    useTranslation: () => ({ t }),
  }
})

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}))

vi.mock('@/lib/token-validator', () => ({
  validateToken: mocks.validateToken,
}))

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    children,
    open,
    onOpenChange,
  }: {
    children: ReactNode
    open: boolean
    onOpenChange: (open: boolean) => void
  }) =>
    open ? (
      <div data-testid="token-dialog">
        <button type="button" onClick={() => onOpenChange(false)}>
          模拟关闭弹窗
        </button>
        {children}
      </div>
    ) : null,
  DialogContent: ({ children }: { children: ReactNode }) => <section>{children}</section>,
  DialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogFooter: ({ children }: { children: ReactNode }) => <footer>{children}</footer>,
}))

vi.mock('@/components/ui/alert-dialog', () => ({
  AlertDialog: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  AlertDialogTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  AlertDialogContent: ({ children }: { children: ReactNode }) => (
    <section data-testid="confirm-dialog">{children}</section>
  ),
  AlertDialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  AlertDialogTitle: ({ children }: { children: ReactNode }) => <h3>{children}</h3>,
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

describe('SecurityTab', () => {
  const clipboardWrite = vi.fn()
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: clipboardWrite },
    })
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
  })

  afterEach(() => {
    cleanup()
    mocks.navigate.mockReset()
    mocks.toast.mockReset()
    mocks.validateToken.mockClear()
    clipboardWrite.mockReset()
    fetchMock.mockReset()
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('展示格式校验，并成功更新合法 Token 后跳转登录页', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ success: true }),
    })
    render(<SecurityTab />)

    fireEvent.click(screen.getAllByTitle('settings.security.show')[0])
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'settings.security.cannotView' })
    )

    const input = screen.getByLabelText('settings.security.newTokenLabel')
    fireEvent.change(input, { target: { value: 'short' } })
    expect(screen.getByText('强度规则')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'settings.security.updateBtn' })).toBeDisabled()

    fireEvent.change(input, { target: { value: 'ValidToken!123' } })
    expect(screen.getByText('settings.security.tokenValid')).toBeInTheDocument()

    vi.useFakeTimers()
    fireEvent.click(screen.getByRole('button', { name: 'settings.security.updateBtn' }))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/webui/auth/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ new_token: 'ValidToken!123' }),
    })
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'settings.security.updateSuccess' })
    )

    act(() => vi.advanceTimersByTime(1500))
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/auth' })
  })

  it('保留后端更新失败信息，并将网络异常归一为连接失败提示', async () => {
    render(<SecurityTab />)
    const input = screen.getByLabelText('settings.security.newTokenLabel')
    fireEvent.change(input, { target: { value: 'ValidToken!123' } })

    fetchMock.mockResolvedValueOnce({
      ok: false,
      json: vi.fn().mockResolvedValue({ success: false, message: '后端拒绝更新' }),
    })
    fireEvent.click(screen.getByRole('button', { name: 'settings.security.updateBtn' }))
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'settings.security.updateFailed',
          description: '后端拒绝更新',
          variant: 'destructive',
        })
      )
    )

    fetchMock.mockRejectedValueOnce(new Error('offline'))
    fireEvent.click(screen.getByRole('button', { name: 'settings.security.updateBtn' }))
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ description: 'settings.security.updateFailedConn' })
      )
    )
  })

  it('重新生成 Token 后支持复制，并在关闭弹窗后清理并跳转', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ success: true, token: 'generated-token' }),
    })
    clipboardWrite.mockResolvedValue(undefined)
    render(<SecurityTab />)

    fireEvent.click(screen.getByRole('button', { name: 'settings.security.confirmGenerate' }))
    expect(await screen.findByText('generated-token')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/webui/auth/regenerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
    })

    fireEvent.click(screen.getByRole('button', { name: 'settings.security.copyToken' }))
    await waitFor(() => expect(clipboardWrite).toHaveBeenCalledWith('generated-token'))
    expect(screen.getByRole('button', { name: 'settings.security.copied' })).toBeInTheDocument()

    vi.useFakeTimers()
    fireEvent.click(screen.getByRole('button', { name: 'settings.security.savedClose' }))
    act(() => vi.advanceTimersByTime(500))

    expect(screen.queryByTestId('token-dialog')).not.toBeInTheDocument()
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/auth' })
  })

  it('复制失败和重新生成失败均给出破坏性提示', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: vi.fn().mockResolvedValue({ success: true, token: 'generated-token' }),
    })
    clipboardWrite.mockRejectedValue(new Error('clipboard denied'))
    render(<SecurityTab />)

    fireEvent.click(screen.getByRole('button', { name: 'settings.security.confirmGenerate' }))
    await screen.findByText('generated-token')
    fireEvent.click(screen.getByRole('button', { name: 'settings.security.copyToken' }))
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'settings.security.copyFailed',
          variant: 'destructive',
        })
      )
    )

    fetchMock.mockResolvedValueOnce({
      ok: false,
      json: vi.fn().mockResolvedValue({ success: false, message: '无法重新生成' }),
    })
    fireEvent.click(screen.getByRole('button', { name: 'settings.security.confirmGenerate' }))
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'settings.security.generateFailed',
          description: '无法重新生成',
        })
      )
    )
  })

  it('未生成 Token 时复制按钮禁用；生成后复制成功并在超时后还原图标', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ success: true, token: 'generated-token' }),
    })
    clipboardWrite.mockResolvedValue(undefined)
    render(<SecurityTab />)

    const currentCopyButton = screen.getByTitle('settings.security.copyTip')
    expect(currentCopyButton).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'settings.security.confirmGenerate' }))
    await screen.findByText('generated-token')
    expect(currentCopyButton).toBeEnabled()

    vi.useFakeTimers()
    fireEvent.click(currentCopyButton)
    await act(async () => {
      await Promise.resolve()
    })

    expect(clipboardWrite).toHaveBeenCalledWith('generated-token')
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'settings.security.copySuccess' })
    )
    expect(currentCopyButton.querySelector('.lucide-check')).not.toBeNull()

    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(currentCopyButton.querySelector('.lucide-copy')).not.toBeNull()

    fireEvent.click(screen.getAllByTitle('settings.security.show')[0])
    expect(screen.getByLabelText('settings.security.yourToken')).toHaveAttribute('type', 'text')
    expect(screen.getByLabelText('settings.security.yourToken')).toHaveValue('generated-token')
    fireEvent.click(screen.getByTitle('settings.security.hide'))
    expect(screen.getByLabelText('settings.security.yourToken')).toHaveAttribute('type', 'password')
  })

  it('复制当前 Token 失败时给出破坏性提示', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ success: true, token: 'generated-token' }),
    })
    clipboardWrite.mockRejectedValue(new Error('denied'))
    render(<SecurityTab />)

    fireEvent.click(screen.getByRole('button', { name: 'settings.security.confirmGenerate' }))
    await screen.findByText('generated-token')

    fireEvent.click(screen.getByTitle('settings.security.copyTip'))
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'settings.security.copyFailed',
          description: 'settings.security.copyFailedDesc',
          variant: 'destructive',
        })
      )
    )
  })

  it('通过 Dialog onOpenChange(false) 关闭生成弹窗后跳转登录页', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ success: true, token: 'generated-token' }),
    })
    render(<SecurityTab />)

    fireEvent.click(screen.getByRole('button', { name: 'settings.security.confirmGenerate' }))
    await screen.findByText('generated-token')

    vi.useFakeTimers()
    fireEvent.click(screen.getByRole('button', { name: '模拟关闭弹窗' }))
    act(() => {
      vi.advanceTimersByTime(500)
    })

    expect(screen.queryByTestId('token-dialog')).not.toBeInTheDocument()
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/auth' })
  })

  it('重新生成接口抛错时提示连接失败', async () => {
    fetchMock.mockRejectedValue(new Error('offline'))
    render(<SecurityTab />)

    fireEvent.click(screen.getByRole('button', { name: 'settings.security.confirmGenerate' }))
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'settings.security.generateFailed',
          description: 'settings.security.generateFailedConn',
          variant: 'destructive',
        })
      )
    )
  })

  it('重新生成失败且无 message 时回退默认文案', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      json: vi.fn().mockResolvedValue({ success: false }),
    })
    render(<SecurityTab />)

    fireEvent.click(screen.getByRole('button', { name: 'settings.security.confirmGenerate' }))
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'settings.security.generateFailed',
          description: 'settings.security.generateFailedDesc',
        })
      )
    )
  })

  it('仅空白的新 Token 通过校验时仍按输入错误拦截更新', async () => {
    mocks.validateToken.mockReturnValue({
      isValid: true,
      rules: [{ id: 'length', label: '长度规则', passed: true }],
    })
    render(<SecurityTab />)

    fireEvent.change(screen.getByLabelText('settings.security.newTokenLabel'), {
      target: { value: '   ' },
    })
    expect(screen.getByRole('button', { name: 'settings.security.updateBtn' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: 'settings.security.updateBtn' }))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'settings.security.inputError',
        description: 'settings.security.inputErrorDesc',
        variant: 'destructive',
      })
    )
  })
})
