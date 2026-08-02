import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthPage } from '../auth'
import * as authLib from '@/lib/auth'
import { authApi } from '@/lib/http'

// navigate / setTheme 需要跨 vi.mock 工厂共享，用 vi.hoisted 提前创建
const { navigateMock, setThemeMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  setThemeMock: vi.fn(),
}))

// t 必须在工厂内只创建一次保持稳定引用，否则依赖 [t] 的 useCallback 失稳导致 effect 无限重跑
vi.mock('react-i18next', () => {
  const t = (key: string) => key
  return { useTranslation: () => ({ t }) }
})

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}))

// 主题固定为 light（非 system），避免走 matchMedia 分支；dashboardStyle 非 future-retro 不渲染齿轮装饰
vi.mock('@/components/use-theme', () => ({
  useTheme: () => ({
    theme: 'light',
    setTheme: setThemeMock,
    themeConfig: { dashboardStyle: 'default' },
  }),
}))

vi.mock('@/lib/auth', () => ({
  checkAuthStatus: vi.fn(),
  getSetupStatus: vi.fn(),
}))

vi.mock('@/lib/http', () => ({
  authApi: { post: vi.fn() },
}))

const checkAuthStatusMock = vi.mocked(authLib.checkAuthStatus)
const getSetupStatusMock = vi.mocked(authLib.getSetupStatus)
const postMock = vi.mocked(authApi.post)

beforeEach(() => {
  // 默认：未登录、无首次配置信息、URL 干净（不带 token）
  checkAuthStatusMock.mockResolvedValue(false)
  getSetupStatusMock.mockResolvedValue(null)
  window.history.replaceState(null, '', '/auth')
})

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
})

/** 获取 token 输入框（Label htmlFor 关联到 Input#token） */
function getTokenInput(): HTMLInputElement {
  return screen.getByLabelText('Access Token')
}

describe('AuthPage 认证状态检查', () => {
  it('认证状态检查未完成时显示加载文案', () => {
    // 永不 resolve 的 Promise 模拟检查进行中
    checkAuthStatusMock.mockReturnValue(new Promise<boolean>(() => {}))
    render(<AuthPage />)
    expect(screen.getByText('auth.checkingAuth')).toBeInTheDocument()
    // 表单尚未渲染
    expect(screen.queryByLabelText('Access Token')).not.toBeInTheDocument()
  })

  it('已认证且非首次配置时跳转首页，并清理 URL 中残留的 token', async () => {
    window.history.replaceState(null, '', '/auth?token=leftover')
    checkAuthStatusMock.mockResolvedValue(true)
    getSetupStatusMock.mockResolvedValue({
      is_first_setup: false,
      token_source: 'custom',
      requires_custom_token: false,
    })
    render(<AuthPage />)

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith({ to: '/' }))
    // 已登录场景下 URL 中的 token 也应被剥离，避免外泄
    expect(window.location.search).toBe('')
    // 不应触发 token 验证请求
    expect(postMock).not.toHaveBeenCalled()
  })

  it('已认证但需要首次配置时跳转到 /setup', async () => {
    checkAuthStatusMock.mockResolvedValue(true)
    getSetupStatusMock.mockResolvedValue({
      is_first_setup: true,
      token_source: 'generated',
      requires_custom_token: true,
    })
    render(<AuthPage />)

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith({ to: '/setup' }))
  })

  it('未认证时渲染登录表单', async () => {
    render(<AuthPage />)

    expect(await screen.findByText('auth.welcome')).toBeInTheDocument()
    expect(getTokenInput()).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'auth.verifyEnter' })).toBeEnabled()
    expect(navigateMock).not.toHaveBeenCalled()
  })
})

describe('AuthPage 手动提交登录', () => {
  it('空 token 提交时显示必填错误且不发请求', async () => {
    const user = userEvent.setup()
    render(<AuthPage />)
    await screen.findByText('auth.welcome')

    await user.click(screen.getByRole('button', { name: 'auth.verifyEnter' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('auth.tokenRequired')
    expect(postMock).not.toHaveBeenCalled()
  })

  it('验证成功（非首次配置）时以正确参数请求后端并跳转首页', async () => {
    postMock.mockResolvedValue({ valid: true })
    const user = userEvent.setup()
    render(<AuthPage />)
    await screen.findByText('auth.welcome')

    await user.type(getTokenInput(), 'secret-token')
    await user.click(screen.getByRole('button', { name: 'auth.verifyEnter' }))

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith('/api/webui/auth/verify', {
        body: { token: 'secret-token' },
        errorMessage: 'auth.verifyFailed',
      })
    )
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith({ to: '/' }))
    // 验证成功后会再次探测认证状态（初次挂载 1 次 + 验证成功后 1 次）
    expect(checkAuthStatusMock).toHaveBeenCalledTimes(2)
  })

  it('验证成功且后端标记首次配置时跳转 /setup', async () => {
    postMock.mockResolvedValue({ valid: true, is_first_setup: true })
    const user = userEvent.setup()
    render(<AuthPage />)
    await screen.findByText('auth.welcome')

    await user.type(getTokenInput(), 'first-time-token')
    await user.click(screen.getByRole('button', { name: 'auth.verifyEnter' }))

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith({ to: '/setup' }))
  })

  it('后端返回 valid=false 时展示后端 message 且不跳转', async () => {
    postMock.mockResolvedValue({ valid: false, message: 'Token 无效' })
    const user = userEvent.setup()
    render(<AuthPage />)
    await screen.findByText('auth.welcome')

    await user.type(getTokenInput(), 'wrong-token')
    await user.click(screen.getByRole('button', { name: 'auth.verifyEnter' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Token 无效')
    expect(navigateMock).not.toHaveBeenCalled()
  })

  it('请求抛出 Error 时展示 err.message', async () => {
    postMock.mockRejectedValue(new Error('后端连接失败'))
    const user = userEvent.setup()
    render(<AuthPage />)
    await screen.findByText('auth.welcome')

    await user.type(getTokenInput(), 'any-token')
    await user.click(screen.getByRole('button', { name: 'auth.verifyEnter' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('后端连接失败')
    expect(navigateMock).not.toHaveBeenCalled()
  })

  it('请求抛出非 Error 值时回退到连接失败文案', async () => {
    postMock.mockRejectedValue('boom')
    const user = userEvent.setup()
    render(<AuthPage />)
    await screen.findByText('auth.welcome')

    await user.type(getTokenInput(), 'any-token')
    await user.click(screen.getByRole('button', { name: 'auth.verifyEnter' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('auth.connFailed')
  })
})

describe('AuthPage URL token 自动登录', () => {
  it('query 中的 token 触发自动登录并从 URL 剥离', async () => {
    window.history.replaceState(null, '', '/auth?token=url-query-token')
    postMock.mockResolvedValue({ valid: true })
    render(<AuthPage />)

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith('/api/webui/auth/verify', {
        body: { token: 'url-query-token' },
        errorMessage: 'auth.verifyFailed',
      })
    )
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith({ to: '/' }))
    // token 已从 URL 中移除，避免刷新/复制链接时再次暴露
    expect(window.location.search).toBe('')
  })

  it('hash 路径中的 token 也能识别，剥离后保留 hash 路径', async () => {
    window.history.replaceState(null, '', '/auth#/login?token=hash-token')
    postMock.mockResolvedValue({ valid: true })
    render(<AuthPage />)

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith('/api/webui/auth/verify', {
        body: { token: 'hash-token' },
        errorMessage: 'auth.verifyFailed',
      })
    )
    expect(window.location.hash).toBe('#/login')
  })

  it('URL token 验证失败时错误上屏，token 保留在输入框供修正', async () => {
    window.history.replaceState(null, '', '/auth?token=bad-url-token')
    postMock.mockResolvedValue({ valid: false, message: '自动登录失败' })
    render(<AuthPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('自动登录失败')
    expect(getTokenInput().value).toBe('bad-url-token')
    // 即使验证失败，URL 中的 token 也已被剥离
    expect(window.location.search).toBe('')
    expect(navigateMock).not.toHaveBeenCalled()
  })
})

describe('AuthPage 主题切换与帮助弹窗', () => {
  it('浅色主题下点击切换按钮调用 setTheme(dark)', async () => {
    const user = userEvent.setup()
    render(<AuthPage />)
    await screen.findByText('auth.welcome')

    await user.click(screen.getByTitle('auth.switchToDark'))
    expect(setThemeMock).toHaveBeenCalledWith('dark')
  })

  it('点击帮助链接打开获取 Token 说明弹窗', async () => {
    const user = userEvent.setup()
    render(<AuthPage />)
    await screen.findByText('auth.welcome')

    await user.click(screen.getByRole('button', { name: /auth\.helpLink/ }))

    expect(await screen.findByText('auth.helpTitle')).toBeInTheDocument()
    expect(screen.getByText('auth.method1Title')).toBeInTheDocument()
    expect(screen.getByText('auth.method2Title')).toBeInTheDocument()
  })
})
