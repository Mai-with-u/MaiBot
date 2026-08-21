import type { ReactNode } from 'react'

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SetupPage } from '../index'
import type { SetupStatus } from '../api'
import type {
  ApiProviderSetupConfig,
  BotBasicConfig,
  ModelSetupConfig,
  PersonalityConfig,
} from '../types'

const mocks = vi.hoisted(() => {
  // t 必须是稳定引用，返回 key（带参数时附加序列化参数）
  const t = (key: string, variables?: Record<string, unknown>) =>
    variables ? `${key}:${JSON.stringify(variables)}` : key
  return {
    t,
    navigate: vi.fn(),
    toast: vi.fn(),
    changeLanguage: vi.fn(),
    loadSetupStatus: vi.fn(),
    loadBotBasicConfig: vi.fn(),
    loadPersonalityConfig: vi.fn(),
    loadApiProviderSetupConfig: vi.fn(),
    loadModelSetupConfig: vi.fn(),
    saveBotBasicConfig: vi.fn(),
    savePersonalityConfig: vi.fn(),
    saveApiProviderSetupConfig: vi.fn(),
    saveModelSetupConfig: vi.fn(),
    completeSetup: vi.fn(),
    updateAccessToken: vi.fn(),
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: mocks.t,
    i18n: {
      resolvedLanguage: 'zh',
      language: 'zh',
      changeLanguage: mocks.changeLanguage,
    },
  }),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}))

vi.mock('../api', () => ({
  loadSetupStatus: mocks.loadSetupStatus,
  loadBotBasicConfig: mocks.loadBotBasicConfig,
  loadPersonalityConfig: mocks.loadPersonalityConfig,
  loadApiProviderSetupConfig: mocks.loadApiProviderSetupConfig,
  loadModelSetupConfig: mocks.loadModelSetupConfig,
  saveBotBasicConfig: mocks.saveBotBasicConfig,
  savePersonalityConfig: mocks.savePersonalityConfig,
  saveApiProviderSetupConfig: mocks.saveApiProviderSetupConfig,
  saveModelSetupConfig: mocks.saveModelSetupConfig,
  completeSetup: mocks.completeSetup,
  updateAccessToken: mocks.updateAccessToken,
}))

// Radix DropdownMenu 在 jsdom 有 pointer-capture 限制，桩成始终展开的普通元素
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  DropdownMenuContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({ children, onClick }: { children: ReactNode; onClick?: () => void }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
}))

// AlertDialog 桩成始终渲染内容，便于直接点击确认按钮
vi.mock('@/components/ui/alert-dialog', () => ({
  AlertDialog: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  AlertDialogTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  AlertDialogContent: ({ children }: { children: ReactNode }) => (
    <section data-testid="skip-dialog">{children}</section>
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

const defaultStatus: SetupStatus = {
  is_first_setup: true,
  token_source: 'custom',
  requires_custom_token: false,
}

const defaultBotBasic: BotBasicConfig = {
  platform: 'qq',
  qq_account: '10001',
  platforms: [],
  nickname: '麦麦',
  alias_names: [],
}

const defaultPersonality: PersonalityConfig = {
  personality: '活泼开朗',
  reply_style: '简短口语',
  multiple_reply_style: [],
  multiple_probability: 0.2,
}

const defaultApiProvider: ApiProviderSetupConfig = {
  provider_name: 'DeepSeek',
  base_url: 'https://api.deepseek.com',
  api_key: 'sk-test',
}

const defaultModelSetup: ModelSetupConfig = {
  planner_model_name: 'deepseek-v4',
  planner_model_identifier: 'deepseek-v4',
  planner_visual: false,
  planner_thinking: true,
  replyer_model_name: 'glm-5',
  replyer_model_identifier: 'glm-5',
  replyer_visual: false,
  replyer_thinking: false,
}

/** 等待加载态结束 */
async function waitLoadingDone() {
  await waitFor(() => {
    expect(screen.queryByText('setupPage.loading.title')).not.toBeInTheDocument()
  })
}

/** 步骤计数器文案（t 的 mock 会把参数序列化拼在 key 后） */
function stepCounterText(current: number, total: number) {
  return `setupPage.progress.stepCounter:${JSON.stringify({ current, total })}`
}

/** 点击“下一步”并等待推进到指定步骤 */
async function clickNextAndWaitStep(current: number, total = 3) {
  fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.next' }))
  await screen.findByText(stepCounterText(current, total))
}

beforeEach(() => {
  mocks.loadSetupStatus.mockResolvedValue({ ...defaultStatus })
  mocks.loadBotBasicConfig.mockResolvedValue({ ...defaultBotBasic })
  mocks.loadPersonalityConfig.mockResolvedValue({ ...defaultPersonality })
  mocks.loadApiProviderSetupConfig.mockResolvedValue({ ...defaultApiProvider })
  mocks.loadModelSetupConfig.mockResolvedValue({ ...defaultModelSetup })
  mocks.saveBotBasicConfig.mockResolvedValue({ success: true })
  mocks.savePersonalityConfig.mockResolvedValue({ success: true })
  mocks.saveApiProviderSetupConfig.mockResolvedValue({ success: true })
  mocks.saveModelSetupConfig.mockResolvedValue({ success: true })
  mocks.completeSetup.mockResolvedValue({ success: true })
  mocks.updateAccessToken.mockResolvedValue({ success: true, message: 'ok' })
})

afterEach(() => {
  cleanup()
})

describe('SetupPage 加载', () => {
  it.skip('配置未返回前显示加载态，返回后渲染第一步并回显昵称', async () => {
    let resolveStatus: (status: SetupStatus) => void = () => undefined
    mocks.loadSetupStatus.mockImplementation(
      () =>
        new Promise<SetupStatus>((resolve) => {
          resolveStatus = resolve
        })
    )

    render(<SetupPage />)

    expect(screen.getByText('setupPage.loading.title')).toBeInTheDocument()

    resolveStatus({ ...defaultStatus })
    await waitLoadingDone()

    // 第一步同时渲染 Bot 基础表单与人格表单，并回显已加载配置
    expect(screen.getByLabelText('setupPage.forms.botBasic.nickname.label')).toHaveValue('麦麦')
    expect(screen.getByLabelText('setupPage.forms.personality.personality.label')).toHaveValue(
      '活泼开朗'
    )
  })

  it.skip('任一配置加载失败时弹出错误提示并结束加载态', async () => {
    mocks.loadBotBasicConfig.mockRejectedValue(new Error('网络炸了'))

    render(<SetupPage />)
    await waitLoadingDone()

    expect(mocks.toast).toHaveBeenCalledWith({
      title: 'setupPage.toast.loadFailedTitle',
      description: '网络炸了',
      variant: 'destructive',
    })
    // 加载失败后仍渲染向导本体（使用默认空配置）
    expect(screen.getByText(stepCounterText(1, 3))).toBeInTheDocument()
  })
})

describe('SetupPage 步骤推进', () => {
  it.skip('昵称为空时下一步被校验拦截且不触发保存', async () => {
    mocks.loadBotBasicConfig.mockResolvedValue({ ...defaultBotBasic, nickname: '  ' })

    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.next' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.validationFailedTitle',
        description: 'setupPage.validation.enterNickname',
        variant: 'destructive',
      })
    })
    expect(mocks.saveBotBasicConfig).not.toHaveBeenCalled()
    expect(screen.getByText(stepCounterText(1, 3))).toBeInTheDocument()
  })

  it.skip('第一步通过校验后保存 Bot 与人格配置并推进到第二步', async () => {
    render(<SetupPage />)
    await waitLoadingDone()

    await clickNextAndWaitStep(2)

    expect(mocks.saveBotBasicConfig).toHaveBeenCalledWith(defaultBotBasic)
    expect(mocks.savePersonalityConfig).toHaveBeenCalledWith(defaultPersonality)
    expect(mocks.toast).toHaveBeenCalledWith({
      title: 'setupPage.toast.saveSuccessTitle',
      description: `setupPage.toast.saveSuccessDescription:${JSON.stringify({
        step: 'setupPage.steps.botProfile.title',
      })}`,
      duration: 1000,
    })
    // 第二步渲染 API 提供商表单并回显配置
    expect(screen.getByLabelText('setupPage.forms.apiProvider.providerName.label')).toHaveValue(
      'DeepSeek'
    )
  })

  it.skip('保存失败时提示错误并停留在当前步骤', async () => {
    mocks.saveBotBasicConfig.mockRejectedValue(new Error('磁盘满了'))

    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.next' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.saveFailedTitle',
        description: '磁盘满了',
        variant: 'destructive',
      })
    })
    expect(screen.getByText(stepCounterText(1, 3))).toBeInTheDocument()
  })

  it.skip('第二步缺少接口地址时被校验拦截', async () => {
    mocks.loadApiProviderSetupConfig.mockResolvedValue({ ...defaultApiProvider, base_url: '' })

    render(<SetupPage />)
    await waitLoadingDone()
    await clickNextAndWaitStep(2)

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.next' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.validationFailedTitle',
        description: 'setupPage.validation.enterBaseUrl',
        variant: 'destructive',
      })
    })
    expect(mocks.saveApiProviderSetupConfig).not.toHaveBeenCalled()
    expect(screen.getByText(stepCounterText(2, 3))).toBeInTheDocument()
  })

  it.skip('第一步上一步按钮禁用，第二步可回退到第一步', async () => {
    render(<SetupPage />)
    await waitLoadingDone()

    const previousButton = screen.getByRole('button', { name: 'setupPage.actions.previous' })
    expect(previousButton).toBeDisabled()

    await clickNextAndWaitStep(2)
    expect(previousButton).toBeEnabled()

    fireEvent.click(previousButton)
    expect(screen.getByText(stepCounterText(1, 3))).toBeInTheDocument()
  })
})

describe('SetupPage 完成与跳过', () => {
  it.skip('最后一步点击完成：保存模型配置、标记完成并跳转首页', async () => {
    render(<SetupPage />)
    await waitLoadingDone()
    await clickNextAndWaitStep(2)
    await clickNextAndWaitStep(3)

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.complete' }))

    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith({ to: '/' })
    })
    expect(mocks.saveModelSetupConfig).toHaveBeenCalledWith(defaultModelSetup, 'DeepSeek')
    expect(mocks.completeSetup).toHaveBeenCalledTimes(1)
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'setupPage.toast.completeSuccessTitle' })
    )
  })

  it.skip('完成时 planner 模型标识符为空则拦截且不标记完成', async () => {
    mocks.loadModelSetupConfig.mockResolvedValue({
      ...defaultModelSetup,
      planner_model_identifier: '',
    })

    render(<SetupPage />)
    await waitLoadingDone()
    await clickNextAndWaitStep(2)
    await clickNextAndWaitStep(3)

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.complete' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.validationFailedTitle',
        description: 'setupPage.validation.enterPlannerModelIdentifier',
        variant: 'destructive',
      })
    })
    expect(mocks.completeSetup).not.toHaveBeenCalled()
    expect(mocks.navigate).not.toHaveBeenCalled()
  })

  it('确认跳过向导后标记完成并跳转首页', async () => {
    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.skipDialog.confirm' }))

    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith({ to: '/' })
    })
    expect(mocks.completeSetup).toHaveBeenCalledTimes(1)
  })

  it('跳过时标记完成失败则提示错误且不跳转', async () => {
    mocks.completeSetup.mockRejectedValue(new Error('后端 500'))

    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.skipDialog.confirm' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.skipFailedTitle',
        description: '后端 500',
        variant: 'destructive',
      })
    })
    expect(mocks.navigate).not.toHaveBeenCalled()
  })
})

describe('SetupPage 自定义 Token 步骤', () => {
  beforeEach(() => {
    mocks.loadSetupStatus.mockResolvedValue({ ...defaultStatus, requires_custom_token: true })
  })

  it.skip('需要自定义 Token 时向导变为四步且跳过按钮禁用', async () => {
    render(<SetupPage />)
    await waitLoadingDone()

    expect(screen.getByText(stepCounterText(1, 4))).toBeInTheDocument()
    // 第一步是自定义 Token 表单
    expect(screen.getByLabelText('setupPage.forms.customToken.label')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /setupPage.actions.skip/ })).toBeDisabled()
    // 主按钮文案是“保存 Token”而非“下一步”
    expect(screen.getByRole('button', { name: 'setupPage.actions.saveToken' })).toBeInTheDocument()
  })

  it('Token 不满足真实校验规则时提示失败规则且不提交', async () => {
    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.label'), {
      target: { value: 'short' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.saveToken' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.validationFailedTitle',
        description: `setupPage.validation.customTokenInvalid:${JSON.stringify({
          failedRules: '长度至少 10 位, 包含大写字母, 包含特殊符号',
        })}`,
        variant: 'destructive',
      })
    })
    expect(mocks.updateAccessToken).not.toHaveBeenCalled()
  })

  it('合法 Token 去除空白后提交，成功后延时跳转登录页', async () => {
    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.label'), {
      target: { value: ' Abcdefghij!123 ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.saveToken' }))

    await waitFor(() => {
      expect(mocks.updateAccessToken).toHaveBeenCalledWith('Abcdefghij!123')
    })
    expect(mocks.toast).toHaveBeenCalledWith({
      title: 'setupPage.toast.customTokenSuccessTitle',
      description: 'setupPage.toast.customTokenSuccessDescription',
    })
    // 跳转在 1200ms 延时后发生
    expect(mocks.navigate).not.toHaveBeenCalled()
    await waitFor(
      () => {
        expect(mocks.navigate).toHaveBeenCalledWith({ to: '/auth' })
      },
      { timeout: 2500 }
    )
  })

  it('后端拒绝新 Token 时提示后端返回的消息且不跳转', async () => {
    mocks.updateAccessToken.mockResolvedValue({ success: false, message: 'Token 太弱' })

    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.label'), {
      target: { value: 'Abcdefghij!123' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.saveToken' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.saveFailedTitle',
        description: 'Token 太弱',
        variant: 'destructive',
      })
    })
    expect(mocks.navigate).not.toHaveBeenCalled()
  })
})

describe('SetupPage 语言切换', () => {
  it('点击语言菜单项调用 i18n.changeLanguage', async () => {
    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.click(screen.getByRole('button', { name: 'English' }))
    expect(mocks.changeLanguage).toHaveBeenCalledWith('en')

    fireEvent.click(screen.getByRole('button', { name: '한국어' }))
    expect(mocks.changeLanguage).toHaveBeenCalledWith('ko')
  })
})
