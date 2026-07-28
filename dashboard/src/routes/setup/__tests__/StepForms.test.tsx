import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiProviderSetupForm,
  BotBasicForm,
  CustomTokenForm,
  ModelSetupForm,
  PersonalityForm,
} from '../StepForms'
import type {
  ApiProviderSetupConfig,
  BotBasicConfig,
  ModelSetupConfig,
  PersonalityConfig,
} from '../types'

// t 必须是稳定引用，返回 key（带参数时附加序列化参数）
const i18nMocks = vi.hoisted(() => {
  const t = (key: string, variables?: Record<string, unknown>) =>
    variables ? `${key}:${JSON.stringify(variables)}` : key
  return { t }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: i18nMocks.t }),
}))

afterEach(() => {
  cleanup()
})

describe('CustomTokenForm', () => {
  it('默认以密码形式隐藏 Token，点击按钮后切换为明文', () => {
    render(<CustomTokenForm token="secret" onChange={vi.fn()} />)

    const input = screen.getByLabelText('setupPage.forms.customToken.label')
    expect(input).toHaveAttribute('type', 'password')

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.forms.customToken.show' }))

    expect(input).toHaveAttribute('type', 'text')
    // 切换后按钮标签变为“隐藏”
    expect(
      screen.getByRole('button', { name: 'setupPage.forms.customToken.hide' })
    ).toBeInTheDocument()
  })

  it('输入 Token 时把输入值原样回调', () => {
    const onChange = vi.fn()
    render(<CustomTokenForm token="" onChange={onChange} />)

    fireEvent.change(screen.getByLabelText('setupPage.forms.customToken.label'), {
      target: { value: 'Abc!123456' },
    })

    expect(onChange).toHaveBeenCalledWith('Abc!123456')
  })

  it('按真实校验规则区分通过与未通过的样式', () => {
    // "abc" 只满足小写字母规则
    render(<CustomTokenForm token="abc" onChange={vi.fn()} />)

    expect(screen.getByText('包含小写字母')).toHaveClass('text-green-600')
    expect(screen.getByText('长度至少 10 位')).toHaveClass('text-muted-foreground')
    expect(screen.getByText('包含大写字母')).toHaveClass('text-muted-foreground')
    expect(screen.getByText('包含特殊符号')).toHaveClass('text-muted-foreground')
  })

  it('Token 满足全部规则时四条规则均显示通过', () => {
    render(<CustomTokenForm token="Abcdefghij!" onChange={vi.fn()} />)

    for (const label of ['长度至少 10 位', '包含大写字母', '包含小写字母', '包含特殊符号']) {
      expect(screen.getByText(label)).toHaveClass('text-green-600')
    }
  })
})

describe('BotBasicForm', () => {
  const config: BotBasicConfig = {
    platform: 'qq',
    qq_account: '10001',
    platforms: [],
    nickname: '麦麦',
    alias_names: [],
  }

  it('回显昵称并在修改时携带完整配置回调', () => {
    const onChange = vi.fn()
    render(<BotBasicForm config={config} onChange={onChange} />)

    const input = screen.getByLabelText('setupPage.forms.botBasic.nickname.label')
    expect(input).toHaveValue('麦麦')

    fireEvent.change(input, { target: { value: '小麦' } })

    expect(onChange).toHaveBeenCalledWith({ ...config, nickname: '小麦' })
  })
})

describe('PersonalityForm', () => {
  const config: PersonalityConfig = {
    personality: '活泼开朗',
    reply_style: '简短口语',
    multiple_reply_style: [],
    multiple_probability: 0.2,
  }

  it('修改人格描述时只更新 personality 字段', () => {
    const onChange = vi.fn()
    render(<PersonalityForm config={config} onChange={onChange} />)

    const textarea = screen.getByLabelText('setupPage.forms.personality.personality.label')
    expect(textarea).toHaveValue('活泼开朗')

    fireEvent.change(textarea, { target: { value: '沉稳理性' } })

    expect(onChange).toHaveBeenCalledWith({ ...config, personality: '沉稳理性' })
  })

  it('修改回复风格时只更新 reply_style 字段', () => {
    const onChange = vi.fn()
    render(<PersonalityForm config={config} onChange={onChange} />)

    fireEvent.change(screen.getByLabelText('setupPage.forms.personality.replyStyle.label'), {
      target: { value: '长文详细' },
    })

    expect(onChange).toHaveBeenCalledWith({ ...config, reply_style: '长文详细' })
  })
})

describe('ApiProviderSetupForm', () => {
  const config: ApiProviderSetupConfig = {
    provider_name: 'DeepSeek',
    base_url: 'https://api.deepseek.com',
    api_key: 'sk-test',
  }

  it('三个字段分别回调合并后的配置', () => {
    const onChange = vi.fn()
    render(<ApiProviderSetupForm config={config} onChange={onChange} />)

    fireEvent.change(screen.getByLabelText('setupPage.forms.apiProvider.providerName.label'), {
      target: { value: 'Zhipu' },
    })
    expect(onChange).toHaveBeenLastCalledWith({ ...config, provider_name: 'Zhipu' })

    fireEvent.change(screen.getByLabelText('setupPage.forms.apiProvider.baseUrl.label'), {
      target: { value: 'https://zhipu.example/v1' },
    })
    expect(onChange).toHaveBeenLastCalledWith({ ...config, base_url: 'https://zhipu.example/v1' })

    fireEvent.change(screen.getByLabelText('setupPage.forms.apiProvider.apiKey.label'), {
      target: { value: 'sk-new' },
    })
    expect(onChange).toHaveBeenLastCalledWith({ ...config, api_key: 'sk-new' })
  })

  it('API Key 默认隐藏且可切换明文显示', () => {
    render(<ApiProviderSetupForm config={config} onChange={vi.fn()} />)

    const input = screen.getByLabelText('setupPage.forms.apiProvider.apiKey.label')
    expect(input).toHaveAttribute('type', 'password')

    fireEvent.click(screen.getByRole('button', { name: 'setupPage.forms.apiProvider.apiKey.show' }))

    expect(input).toHaveAttribute('type', 'text')
    expect(
      screen.getByRole('button', { name: 'setupPage.forms.apiProvider.apiKey.hide' })
    ).toBeInTheDocument()
  })
})

describe('ModelSetupForm', () => {
  const config: ModelSetupConfig = {
    planner_model_name: 'planner-model',
    planner_model_identifier: 'deepseek-v4',
    planner_visual: false,
    planner_thinking: true,
    replyer_model_name: 'replyer-model',
    replyer_model_identifier: 'glm-5',
    replyer_visual: false,
    replyer_thinking: false,
  }

  it('修改 planner 标识符时同步名称，且按标识符推断关闭思考', () => {
    const onChange = vi.fn()
    render(<ModelSetupForm config={config} onChange={onChange} />)

    // "gpt-4.1-mini" 不含 deepseek-v4-pro，思考开关被推断为关闭
    fireEvent.change(
      screen.getByLabelText('setupPage.forms.modelSetup.planner.identifier.label'),
      { target: { value: 'gpt-4.1-mini' } }
    )

    expect(onChange).toHaveBeenCalledWith({
      ...config,
      planner_model_identifier: 'gpt-4.1-mini',
      planner_model_name: 'gpt-4.1-mini',
      planner_thinking: false,
    })
  })

  it('replyer 标识符包含 deepseek-v4-pro（忽略大小写与空白）时推断开启思考', () => {
    const onChange = vi.fn()
    render(<ModelSetupForm config={config} onChange={onChange} />)

    fireEvent.change(
      screen.getByLabelText('setupPage.forms.modelSetup.replyer.identifier.label'),
      { target: { value: ' Deepseek-V4-Pro ' } }
    )

    expect(onChange).toHaveBeenCalledWith({
      ...config,
      replyer_model_identifier: ' Deepseek-V4-Pro ',
      replyer_model_name: ' Deepseek-V4-Pro ',
      replyer_thinking: true,
    })
  })

  it('切换视觉与思考开关分别回调对应字段', () => {
    const onChange = vi.fn()
    render(<ModelSetupForm config={config} onChange={onChange} />)

    // planner 视觉开关：false -> true
    fireEvent.click(
      screen.getByRole('switch', { name: 'setupPage.forms.modelSetup.planner.visual.label' })
    )
    expect(onChange).toHaveBeenLastCalledWith({ ...config, planner_visual: true })

    // “启用思考”开关有两个：前者属于 planner（true -> false），后者属于 replyer（false -> true）
    const thinkingSwitches = screen.getAllByRole('switch', { name: '启用思考' })
    expect(thinkingSwitches).toHaveLength(2)

    fireEvent.click(thinkingSwitches[0])
    expect(onChange).toHaveBeenLastCalledWith({ ...config, planner_thinking: false })

    fireEvent.click(thinkingSwitches[1])
    expect(onChange).toHaveBeenLastCalledWith({ ...config, replyer_thinking: true })
  })
})
