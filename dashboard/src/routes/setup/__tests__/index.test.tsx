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
    loadBasicSetupConfig: vi.fn(),
    loadPersonalityConfig: vi.fn(),
    loadApiProviderSetupConfig: vi.fn(),
    loadModelSetupConfig: vi.fn(),
    saveBotBasicConfig: vi.fn(),
    saveBasicSetupConfig: vi.fn(),
    savePersonalityConfig: vi.fn(),
    saveApiProviderSetupConfig: vi.fn(),
    saveModelSetupConfig: vi.fn(),
    completeSetup: vi.fn(),
    updateAccessToken: vi.fn(),
    fetchSetupModels: vi.fn(),
    testSetupModel: vi.fn(),
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
  loadBasicSetupConfig: mocks.loadBasicSetupConfig,
  loadPersonalityConfig: mocks.loadPersonalityConfig,
  loadApiProviderSetupConfig: mocks.loadApiProviderSetupConfig,
  loadModelSetupConfig: mocks.loadModelSetupConfig,
  saveBotBasicConfig: mocks.saveBotBasicConfig,
  saveBasicSetupConfig: mocks.saveBasicSetupConfig,
  savePersonalityConfig: mocks.savePersonalityConfig,
  saveApiProviderSetupConfig: mocks.saveApiProviderSetupConfig,
  saveModelSetupConfig: mocks.saveModelSetupConfig,
  completeSetup: mocks.completeSetup,
  updateAccessToken: mocks.updateAccessToken,
  fetchSetupModels: mocks.fetchSetupModels,
  testSetupModel: mocks.testSetupModel,
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
  behavior_style: '自然参与聊天',
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
  mocks.loadBasicSetupConfig.mockResolvedValue({ inevitable_at_reply: true, private_reply_paused: false })
  mocks.loadPersonalityConfig.mockResolvedValue({ ...defaultPersonality })
  mocks.loadApiProviderSetupConfig.mockResolvedValue({ ...defaultApiProvider })
  mocks.loadModelSetupConfig.mockResolvedValue({ ...defaultModelSetup })
  mocks.saveBotBasicConfig.mockResolvedValue({ success: true })
  mocks.saveBasicSetupConfig.mockResolvedValue({ success: true })
  mocks.savePersonalityConfig.mockResolvedValue({ success: true })
  mocks.saveApiProviderSetupConfig.mockResolvedValue({ success: true })
  mocks.saveModelSetupConfig.mockImplementation(async (config: ModelSetupConfig) => ({
    planner: config.planner_model_identifier,
    replyer: config.replyer_model_identifier,
  }))
  mocks.completeSetup.mockResolvedValue({ success: true })
  mocks.updateAccessToken.mockResolvedValue({ success: true, message: 'ok' })
  mocks.fetchSetupModels.mockReset()
  mocks.fetchSetupModels.mockResolvedValue([{ id: 'deepseek-v4' }])
  mocks.testSetupModel.mockReset()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
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

  it('Token 不满足真实校验规则时禁用提交并显示未满足规则', async () => {
    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.label'), {
      target: { value: 'short' },
    })
    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.confirmation'), {
      target: { value: 'short' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.saveToken' }))

    expect(screen.getByRole('button', { name: 'setupPage.actions.saveToken' })).toBeDisabled()
    expect(screen.getAllByLabelText('setupPage.forms.customToken.unmet')).toHaveLength(3)
    expect(mocks.toast).not.toHaveBeenCalled()
    expect(mocks.updateAccessToken).not.toHaveBeenCalled()
  })

  it('两次密码一致后才提交，保存成功后直接进入下一步', async () => {
    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.label'), {
      target: { value: ' Abcdefghij!123 ' },
    })
    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.confirmation'), {
      target: { value: 'Different!Password' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.saveToken' }))
    expect(mocks.updateAccessToken).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.confirmation'), {
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
    expect(await screen.findByRole('button', { name: 'setupPage.actions.next' })).toBeInTheDocument()
    expect(mocks.navigate).not.toHaveBeenCalled()
  })

  it('后端拒绝新 Token 时提示后端返回的消息且不跳转', async () => {
    mocks.updateAccessToken.mockResolvedValue({ success: false, message: 'Token 太弱' })

    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.label'), {
      target: { value: 'Abcdefghij!123' },
    })
    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.confirmation'), {
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

describe('SetupPage 错误态与空态', () => {

  it('配置未返回前显示加载态，不渲染表单', () => {
    mocks.loadSetupStatus.mockImplementation(() => new Promise(() => {}))
    render(<SetupPage />)

    expect(screen.getByText('setupPage.loading.title')).toBeInTheDocument()
    expect(
      screen.queryByLabelText('setupPage.forms.botBasic.nickname.label')
    ).not.toBeInTheDocument()
  })

  it('任一配置加载失败时弹出错误提示并结束加载态', async () => {
    mocks.loadBotBasicConfig.mockRejectedValue(new Error('网络炸了'))
    render(<SetupPage />)
    await waitLoadingDone()

    expect(mocks.toast).toHaveBeenCalledWith({
      title: 'setupPage.toast.loadFailedTitle',
      description: '网络炸了',
      variant: 'destructive',
    })
    expect(screen.getByLabelText('setupPage.forms.botBasic.nickname.label')).toBeInTheDocument()
  })

  it('加载失败且错误不是 Error 时使用默认失败描述', async () => {
    mocks.loadPersonalityConfig.mockRejectedValue('boom')
    render(<SetupPage />)
    await waitLoadingDone()

    expect(mocks.toast).toHaveBeenCalledWith({
      title: 'setupPage.toast.loadFailedTitle',
      description: 'setupPage.toast.loadFailedDescription',
      variant: 'destructive',
    })
  })

  it('昵称为空时下一步被校验拦截且不触发保存', async () => {
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
    expect(screen.getByRole('button', { name: 'setupPage.actions.next' })).toBeInTheDocument()
  })

  it('保存失败时提示错误并停留在当前步骤', async () => {
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
    expect(screen.getByRole('button', { name: 'setupPage.actions.next' })).toBeInTheDocument()
  })

  it('planner 模型标识符为空则停留在模型配置阶段', async () => {
    render(<SetupPage />)
    await waitLoadingDone()
    await advanceTo('personality', 'defineModel', 'replyer', 'planner')
    fireEvent.change(screen.getByRole('textbox', { name: /setupPage\.forms\.modelSetup\.planner\.identifier\.label/ }), {
      target: { value: '   ' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.next' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.validationFailedTitle',
        description: 'setupPage.validation.enterPlannerModelIdentifier',
        variant: 'destructive',
      })
    })
    expect(mocks.completeSetup).not.toHaveBeenCalled()
  })

  it('完成时标记完成失败则提示错误且不跳转', async () => {
    mocks.completeSetup.mockRejectedValue(new Error('写入失败'))
    render(<SetupPage />)
    await waitLoadingDone()
    await advanceTo('personality', 'defineModel', 'replyer', 'planner', 'groupReplies', 'privateReplies')

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.complete' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.completeFailedTitle',
        description: '写入失败',
        variant: 'destructive',
      })
    })
    expect(mocks.navigate).not.toHaveBeenCalled()
  })

  it('保存 Token 时后端抛错则提示错误且不跳转', async () => {
    mocks.loadSetupStatus.mockResolvedValue({ ...defaultStatus, requires_custom_token: true })
    mocks.updateAccessToken.mockRejectedValue(new Error('网络断开'))
    render(<SetupPage />)
    await waitLoadingDone()

    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.label'), {
      target: { value: 'Abcdefghij!123' },
    })
    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.confirmation'), {
      target: { value: 'Abcdefghij!123' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.saveToken' }))

    await waitFor(() => {
      expect(mocks.toast).toHaveBeenCalledWith({
        title: 'setupPage.toast.saveFailedTitle',
        description: '网络断开',
        variant: 'destructive',
      })
    })
    expect(mocks.navigate).not.toHaveBeenCalled()
  })
})



async function advanceTo(...steps: string[]) {
  for (const step of steps) {
    const template = screen.queryByRole('button', { name: 'setupPage.steps.personality.template' })
    if (template) fireEvent.click(template)
    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.next' }))
    await screen.findByRole('heading', { name: `setupPage.steps.${step}.title` })
  }
}

it('先选择人设路径，返回切换时保留各自的草稿', async () => {
  render(<SetupPage />)
  await waitLoadingDone()
  await advanceTo('personality')
  const choose = (mode: 'manual' | 'template') => fireEvent.click(
    screen.getByRole('button', { name: `setupPage.steps.personality.${mode}` })
  )
  const field = () => screen.getByRole('textbox', { name: 'setupPage.forms.personality.personality.label' })
  const back = () => fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.previous' }))

  expect(screen.queryAllByRole('textbox')).toHaveLength(0)
  choose('manual')
  expect(field()).toHaveValue('')
  expect(screen.queryByRole('combobox', { name: 'setupPage.forms.personality.presets.label' })).not.toBeInTheDocument()
  fireEvent.change(field(), { target: { value: '手写草稿' } })
  back()
  choose('template')
  expect(field()).toHaveValue('setupPage.defaults.personality.personality')
  expect(screen.getByRole('combobox', { name: 'setupPage.forms.personality.presets.label' })).toBeInTheDocument()
  fireEvent.change(field(), { target: { value: '模板草稿' } })
  back()
  choose('manual')
  expect(field()).toHaveValue('手写草稿')
  back()
  choose('template')
  expect(field()).toHaveValue('模板草稿')
  await advanceTo('defineModel')
  expect(mocks.savePersonalityConfig).toHaveBeenCalledWith(expect.objectContaining({ personality: '模板草稿' }))
})

it('展开当前阶段，保存基础设置后进入带试聊引导的聊天页', async () => {
  render(<SetupPage />)
  await waitLoadingDone()
  expect(screen.getByRole('list', { name: 'setupPage.steps.botProfile.title' })).toBeInTheDocument()
  await advanceTo('personality', 'defineModel')
  expect(screen.queryByRole('list', { name: 'setupPage.steps.botProfile.title' })).not.toBeInTheDocument()
  expect(screen.getByRole('list', { name: 'setupPage.steps.modelSetup.title' })).toBeInTheDocument()
  await advanceTo('replyer', 'planner', 'groupReplies')
  expect(screen.queryByRole('list', { name: 'setupPage.steps.modelSetup.title' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('switch', { name: 'setupPage.steps.groupReplies.label' }))
  await advanceTo('privateReplies')
  fireEvent.click(screen.getByRole('switch', { name: 'setupPage.steps.privateReplies.label' }))
  fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.complete' }))
  await waitFor(() => {
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/chat', search: { setupTrial: true } })
  })
  expect(mocks.completeSetup).toHaveBeenCalledTimes(1)
  expect(mocks.saveBasicSetupConfig).toHaveBeenNthCalledWith(1, { inevitable_at_reply: false })
  expect(mocks.saveBasicSetupConfig).toHaveBeenNthCalledWith(2, { private_reply_paused: true })
})

describe('SetupPage 模型列表拉取', () => {
  it('拉取失败时最多重试到上限，不再持续请求服务商', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.fetchSetupModels.mockRejectedValue(new Error('401 Unauthorized'))
    render(<SetupPage />)
    await waitLoadingDone()
    await advanceTo('personality', 'defineModel')

    await vi.advanceTimersByTimeAsync(600 + 1000 + 2000)
    await waitFor(() => expect(mocks.fetchSetupModels).toHaveBeenCalledTimes(3))
    await vi.advanceTimersByTimeAsync(30_000)
    expect(mocks.fetchSetupModels).toHaveBeenCalledTimes(3)
    expect(screen.getByRole('alert')).toHaveTextContent('401 Unauthorized')
  })
})

describe('SetupPage 连通性测试', () => {
  // 选 DeepSeek 时向导会自动填入默认模型，这里用自定义提供商保留测试数据里的模型。
  const customProvider: ApiProviderSetupConfig = {
    provider_name: 'MyProvider',
    base_url: 'https://api.example.com/v1',
    api_key: 'sk-test',
  }

  async function goToModelStep(role: 'replyer' | 'planner' = 'planner') {
    mocks.loadApiProviderSetupConfig.mockResolvedValue({ ...customProvider })
    render(<SetupPage />)
    await waitLoadingDone()
    await advanceTo('personality', 'defineModel', 'replyer')
    if (role === 'planner') await advanceTo('planner')
    await screen.findByRole('button', { name: 'setupPage.connectionTest.button' })
  }

  it('只测试当前角色，返回上一步仍保留该角色的结果', async () => {
    mocks.testSetupModel.mockImplementation(async (name: string) => ({
      success: name === 'deepseek-v4',
      latency_ms: 1234,
      error: name === 'deepseek-v4' ? null : '401 invalid key',
    }))
    await goToModelStep('replyer')
    mocks.saveApiProviderSetupConfig.mockClear()
    mocks.saveModelSetupConfig.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.connectionTest.button' }))

    expect(await screen.findByText('401 invalid key')).toBeInTheDocument()
    expect(mocks.saveApiProviderSetupConfig).toHaveBeenCalledWith(customProvider)
    expect(mocks.saveModelSetupConfig).toHaveBeenCalledWith(defaultModelSetup, 'MyProvider', 'replyer')
    expect(mocks.testSetupModel).toHaveBeenCalledExactlyOnceWith('glm-5')
    expect(screen.getByText('setupPage.connectionTest.canContinue')).toBeInTheDocument()
    // 测试失败只作参考，仍可继续。
    expect(screen.getByRole('button', { name: 'setupPage.actions.next' })).toBeEnabled()
    await advanceTo('planner')
    expect(screen.queryByText('401 invalid key')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'setupPage.connectionTest.button' }))
    expect(await screen.findByText('setupPage.connectionTest.roles.planner')).toBeInTheDocument()
    expect(mocks.saveModelSetupConfig).toHaveBeenLastCalledWith(defaultModelSetup, 'MyProvider', 'planner')
    expect(mocks.testSetupModel).toHaveBeenNthCalledWith(2, 'deepseek-v4')
    fireEvent.click(screen.getByRole('button', { name: 'setupPage.actions.previous' }))
    expect(screen.getByText('401 invalid key')).toBeInTheDocument()
  })

  it('保存配置失败时提示错误且不调用模型测试', async () => {
    await goToModelStep()
    mocks.saveApiProviderSetupConfig.mockRejectedValueOnce(new Error('磁盘只读'))

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.connectionTest.button' }))

    expect(
      await screen.findByText('setupPage.connectionTest.saveFailed:{"error":"磁盘只读"}')
    ).toBeInTheDocument()
    expect(mocks.testSetupModel).not.toHaveBeenCalled()
  })
})
