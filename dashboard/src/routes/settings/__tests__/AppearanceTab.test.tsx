/**
 * AppearanceTab 特征化测试
 *
 * 策略：
 * - mock use-theme / use-animation 上下文钩子，注入可控状态并断言回调参数（请求形状）；
 * - mock 重量级子组件（CodeEditor / 背景上传器 / 背景效果 / 组件 CSS 编辑器），
 *   通过暴露按钮驱动父组件的编排逻辑；
 * - applyThemePipeline 打桩避免污染 jsdom 文档样式，getComputedTokens 保留真实实现；
 * - Radix Select / Slider 通过 combobox/option 与键盘箭头驱动，锁定 token 写入形状；
 * - FileReader 用同步桩替换，规避 jsdom 异步读文件与 location.reload 的时序问题。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppearanceTab } from '../AppearanceTab'
import type { AnimationSettings } from '@/lib/animation-context'
import type { ThemeProviderState } from '@/lib/theme-context'
import { DEFAULT_ACCENT_COLOR_HEX, DEFAULT_ACCENT_COLOR_HSL, hexToHSL } from '@/lib/theme/palette'
import { applyThemePipeline } from '@/lib/theme/pipeline'
import { exportThemeJSON, importThemeJSON } from '@/lib/theme/storage'
import {
  DEFAULT_FUTURE_RETRO_STYLE_CONFIG,
  defaultBackgroundConfig,
  defaultBackgroundEffects,
  defaultLightTokens,
} from '@/lib/theme/tokens'
import type { BackgroundEffects, ThemeTokens, UserThemeConfig } from '@/lib/theme/tokens'

// Radix Select / Slider 在 jsdom 里会读 pointer capture；setup 未补，用普通函数避免 restoreMocks 清空
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}

const toastMock = vi.fn()

// 每个用例在 beforeEach 中重建，测试体内可按需覆盖后再 render
let themeState: ThemeProviderState
let animationState: AnimationSettings

vi.mock('react-i18next', () => {
  // t 在工厂内只创建一次，保持稳定引用，避免依赖 t 的 memo/effect 无限重渲染
  const t = (key: string) => key
  return { useTranslation: () => ({ t, i18n: { language: 'zh-CN' } }) }
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/components/use-theme', () => ({ useTheme: () => themeState }))

vi.mock('@/hooks/use-animation', () => ({ useAnimation: () => animationState }))

// applyThemePipeline 打桩（避免向 jsdom 注入样式），getComputedTokens 保留真实实现
vi.mock('@/lib/theme/pipeline', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/theme/pipeline')>()
  return { ...actual, applyThemePipeline: vi.fn() }
})

vi.mock('@/lib/theme/storage', () => ({
  exportThemeJSON: vi.fn(),
  importThemeJSON: vi.fn(),
}))

// CodeMirror 编辑器桩：暴露受控 textarea 以驱动 onChange
vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({
    value,
    onChange,
    placeholder,
  }: {
    value: string
    onChange?: (value: string) => void
    placeholder?: string
  }) => (
    <textarea
      aria-label="mock-css-editor"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}))

// 背景上传器桩：展示当前资源 ID，并提供按钮触发选择回调
vi.mock('@/components/background-uploader', () => ({
  BackgroundUploader: ({
    assetId,
    onAssetSelect,
    disabled,
  }: {
    assetId?: string
    onAssetSelect: (id: string | undefined) => void
    disabled?: boolean
  }) => (
    <div>
      <span data-testid="bg-asset-id">{assetId ?? '(无资源)'}</span>
      <button type="button" disabled={disabled} onClick={() => onAssetSelect('asset-1')}>
        mock-select-asset
      </button>
    </div>
  ),
}))

// 背景效果控制桩：点击时基于当前 effects 修改 blur
vi.mock('@/components/background-effects-controls', () => ({
  BackgroundEffectsControls: ({
    effects,
    onChange,
    disabled,
  }: {
    effects: BackgroundEffects
    onChange: (effects: BackgroundEffects) => void
    disabled?: boolean
  }) => (
    <button type="button" disabled={disabled} onClick={() => onChange({ ...effects, blur: 6 })}>
      mock-change-effects
    </button>
  ),
}))

// 组件级 CSS 编辑器桩：点击时回传带组件 ID 的 CSS 片段
vi.mock('@/components/component-css-editor', () => ({
  ComponentCSSEditor: ({
    componentId,
    onChange,
    disabled,
  }: {
    componentId: string
    value: string
    onChange: (css: string) => void
    disabled?: boolean
  }) => (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(`.${componentId} { color: red; }`)}
    >
      mock-edit-component-css
    </button>
  ),
}))

/** 同步触发 onload 的 FileReader 桩，内容由 mockFileText 控制 */
let mockFileText = ''
class MockFileReader {
  onload: ((ev: ProgressEvent<FileReader>) => void) | null = null
  readAsText(_file: Blob): void {
    const event = { target: { result: mockFileText } } as unknown as ProgressEvent<FileReader>
    this.onload?.(event)
  }
}

/** 构造可控的主题上下文状态（默认 modern 风格、system 模式、未持久化主题色） */
function makeThemeState(
  configPartial: Partial<UserThemeConfig> = {},
  theme: 'dark' | 'light' | 'system' = 'system'
): ThemeProviderState {
  return {
    theme,
    resolvedTheme: 'light',
    setTheme: vi.fn(),
    themeConfig: {
      selectedPreset: 'light',
      accentColor: '',
      styleTokenOverrides: {},
      styleCustomCSS: {},
      styleBackgroundConfig: {},
      dashboardStyle: 'modern',
      styleConfig: { futureRetro: { ...DEFAULT_FUTURE_RETRO_STYLE_CONFIG } },
      ...configPartial,
    },
    updateThemeConfig: vi.fn(),
    resetTheme: vi.fn(),
  }
}

/** 向隐藏的文件输入框投递一个 JSON 文件（内容实际由 MockFileReader 决定） */
function uploadThemeFile(container: HTMLElement) {
  const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]')
  if (!fileInput) throw new Error('未找到隐藏的文件输入框')
  const file = new File([mockFileText], 'theme.json', { type: 'application/json' })
  fireEvent.change(fileInput, { target: { files: [file] } })
}

beforeEach(() => {
  themeState = makeThemeState()
  animationState = { enableAnimations: true, setEnableAnimations: vi.fn() }
  vi.mocked(exportThemeJSON).mockReturnValue('{"mock":"theme"}')
  vi.mocked(importThemeJSON).mockReturnValue({ success: true, errors: [] })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('AppearanceTab 主题模式与界面风格', () => {
  it('渲染三种主题模式并高亮当前选项，点击其他模式调用 setTheme', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)

    expect(screen.getByRole('tab', { name: /systemDesc/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /darkDesc/ })).toHaveAttribute('aria-selected', 'false')

    await user.click(screen.getByRole('tab', { name: /darkDesc/ }))
    expect(themeState.setTheme).toHaveBeenCalledWith('dark')
  })

  it('界面风格卡片：点击未来复古调用 updateThemeConfig 切换 dashboardStyle', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)

    expect(screen.getByRole('button', { name: /原版 Dashboard/ })).toHaveAttribute(
      'aria-pressed',
      'true'
    )

    await user.click(screen.getByRole('button', { name: /未来复古/ }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({ dashboardStyle: 'future-retro' })
  })

  it('modern 风格显示主题色/自定义 CSS/导入导出区块与色板预览', () => {
    render(<AppearanceTab />)

    expect(screen.getByText('settings.appearance.accentColor')).toBeInTheDocument()
    expect(screen.getByText('settings.appearance.customCss')).toBeInTheDocument()
    expect(screen.getByText('settings.appearance.importExportTheme')).toBeInTheDocument()
    // 8 个色板 token 预览
    for (const tokenName of [
      'primary',
      'secondary',
      'muted',
      'accent',
      'destructive',
      'background',
      'card',
      'border',
    ]) {
      expect(screen.getByText(tokenName)).toBeInTheDocument()
    }
    // 未来复古专属配置不应出现
    expect(screen.queryByText('settings.appearance.retroConfig')).not.toBeInTheDocument()
    // 自定义 CSS 为空时清除按钮禁用
    expect(screen.getByRole('button', { name: /clearCss/ })).toBeDisabled()
  })

  it('未来复古风格隐藏 modern 区块并可切换纸面纹理', async () => {
    const user = userEvent.setup()
    themeState = makeThemeState({ dashboardStyle: 'future-retro' })
    render(<AppearanceTab />)

    expect(screen.getByText('settings.appearance.retroConfig')).toBeInTheDocument()
    expect(screen.queryByText('settings.appearance.customCss')).not.toBeInTheDocument()
    expect(screen.queryByText('settings.appearance.importExportTheme')).not.toBeInTheDocument()

    expect(
      screen.getByRole('button', { name: 'settings.appearance.retroTextureFine' })
    ).toHaveAttribute('aria-pressed', 'true')
    await user.click(
      screen.getByRole('button', { name: 'settings.appearance.retroTextureDots' })
    )
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleConfig: {
        futureRetro: {
          ...DEFAULT_FUTURE_RETRO_STYLE_CONFIG,
          textureStyle: 'dot-grid',
        },
      },
    })
  })
})

describe('AppearanceTab 主题色', () => {
  it('输入合法 hex 会转大写、立即更新预览并防抖持久化 accentColor', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)

    const input = screen.getByRole('textbox', { name: 'settings.appearance.accentPrimary' })
    expect(input).toHaveValue(DEFAULT_ACCENT_COLOR_HEX)

    await user.clear(input)
    await user.type(input, '#ff0000')
    // 文本输入被规范化为大写
    expect(input).toHaveValue('#FF0000')

    // 预览管线立即以新颜色重新执行（浅色模式 isDark=false）
    await waitFor(() => {
      const lastCall = vi.mocked(applyThemePipeline).mock.calls.at(-1)
      expect(lastCall?.[0].accentColor).toBe(hexToHSL('#FF0000'))
      expect(lastCall?.[1]).toBe(false)
    })

    // 防抖（160ms）后写入配置
    await waitFor(() =>
      expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
        accentColor: hexToHSL('#FF0000'),
      })
    )
  })

  it('非法 hex 输入不触发持久化，预览保持默认主题色', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)

    const input = screen.getByRole('textbox', { name: 'settings.appearance.accentPrimary' })
    await user.clear(input)
    await user.type(input, '#GGGGGG')
    expect(input).toHaveValue('#GGGGGG')

    // 等待超过防抖窗口，确认没有任何持久化调用
    await new Promise((resolve) => setTimeout(resolve, 260))
    expect(themeState.updateThemeConfig).not.toHaveBeenCalled()

    // 预览仍是默认主题色
    const lastCall = vi.mocked(applyThemePipeline).mock.calls.at(-1)
    expect(lastCall?.[0].accentColor).toBe(DEFAULT_ACCENT_COLOR_HSL)
  })

  it('重置主题色按钮立即回写默认 accentColor 并还原输入框', async () => {
    const user = userEvent.setup()
    themeState = makeThemeState({ accentColor: '0 100% 50%' })
    render(<AppearanceTab />)

    // 手风琴全部收起时页面上只有主题色区块这一个"重置默认"按钮
    const resetButton = screen.getByRole('button', { name: /resetDefault/ })
    expect(resetButton).toBeEnabled()

    await user.click(resetButton)
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      accentColor: DEFAULT_ACCENT_COLOR_HSL,
    })
    expect(screen.getByRole('textbox', { name: 'settings.appearance.accentPrimary' })).toHaveValue(
      DEFAULT_ACCENT_COLOR_HEX
    )
  })

  it('主题色已是默认值时重置按钮禁用', () => {
    themeState = makeThemeState({ accentColor: DEFAULT_ACCENT_COLOR_HSL })
    render(<AppearanceTab />)

    expect(screen.getByRole('button', { name: /resetDefault/ })).toBeDisabled()
  })
})

describe('AppearanceTab 自定义 CSS', () => {
  it('录入危险 CSS 展示安全警告并防抖保存原文', async () => {
    render(<AppearanceTab />)

    const dangerousCSS = 'body { color: red; }\n@import url("https://evil.example/x.css");'
    fireEvent.change(screen.getByLabelText('mock-css-editor'), {
      target: { value: dangerousCSS },
    })

    // sanitizeCSS 实时产生警告（真实实现）
    expect(screen.getByText('settings.appearance.cssWarningTitle')).toBeInTheDocument()
    expect(screen.getByText(/移除 @import 语句/)).toBeInTheDocument()

    // 防抖（500ms）后按当前风格键保存
    await waitFor(
      () =>
        expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
          styleCustomCSS: { modern: dangerousCSS },
        }),
      { timeout: 3000 }
    )
  })

  it('清除按钮清空当前风格的自定义 CSS 与编辑器内容', async () => {
    const user = userEvent.setup()
    themeState = makeThemeState({ styleCustomCSS: { modern: 'body { color: red; }' } })
    render(<AppearanceTab />)

    const editor = screen.getByLabelText('mock-css-editor')
    expect(editor).toHaveValue('body { color: red; }')

    await user.click(screen.getByRole('button', { name: /clearCss/ }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleCustomCSS: { modern: '' },
    })
    expect(editor).toHaveValue('')
  })
})

describe('AppearanceTab 主题导入/导出与重置', () => {
  it('导出主题：生成 JSON Blob 触发下载并释放对象 URL', async () => {
    const user = userEvent.setup()
    const createUrlSpy = vi.spyOn(URL, 'createObjectURL')
    const revokeUrlSpy = vi.spyOn(URL, 'revokeObjectURL')
    const anchorClickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})
    render(<AppearanceTab />)

    await user.click(screen.getByRole('button', { name: 'settings.appearance.exportTheme' }))

    expect(exportThemeJSON).toHaveBeenCalledTimes(1)
    expect(createUrlSpy).toHaveBeenCalledTimes(1)
    const blob = createUrlSpy.mock.calls[0][0] as Blob
    expect(blob.type).toBe('application/json')

    // 通过 spy.mock.contexts 取 click 时的 this（下载用的 <a> 元素）
    expect(anchorClickSpy).toHaveBeenCalledTimes(1)
    const anchor = anchorClickSpy.mock.contexts[0] as HTMLAnchorElement
    expect(anchor.download).toMatch(/^maibot-theme-\d+\.json$/)

    // 用完即释放对象 URL
    expect(revokeUrlSpy).toHaveBeenCalledWith(createUrlSpy.mock.results[0].value)
  })

  it('导入主题成功：调用 importThemeJSON 并提示成功', () => {
    // 成功路径会注册 1 秒后 window.location.reload 的定时器，
    // 用假定时器接管并在断言后丢弃，避免 jsdom 报导航未实现
    vi.useFakeTimers()
    try {
      vi.stubGlobal('FileReader', MockFileReader)
      mockFileText = '{"version":1}'
      vi.mocked(importThemeJSON).mockReturnValue({ success: true, errors: [] })

      const { container } = render(<AppearanceTab />)
      uploadThemeFile(container)

      expect(importThemeJSON).toHaveBeenCalledWith('{"version":1}')
      expect(toastMock).toHaveBeenCalledWith({
        title: 'settings.appearance.importSuccess',
        description: 'settings.appearance.importSuccessDesc',
      })
    } finally {
      vi.clearAllTimers()
      vi.useRealTimers()
    }
  })

  it('导入主题失败：toast 以 destructive 形式拼接错误详情', () => {
    vi.stubGlobal('FileReader', MockFileReader)
    mockFileText = '{"broken":true}'
    vi.mocked(importThemeJSON).mockReturnValue({
      success: false,
      errors: ['版本不兼容', '字段缺失'],
    })

    const { container } = render(<AppearanceTab />)
    uploadThemeFile(container)

    expect(importThemeJSON).toHaveBeenCalledWith('{"broken":true}')
    expect(toastMock).toHaveBeenCalledWith({
      title: 'settings.appearance.importFailed',
      description: '版本不兼容; 字段缺失',
      variant: 'destructive',
    })
  })

  it('重置主题对话框：确认后调用 resetTheme 并提示成功', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)

    await user.click(screen.getByRole('button', { name: 'settings.appearance.resetTheme' }))
    expect(await screen.findByText('settings.appearance.confirmResetTheme')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'settings.appearance.confirmResetAction' }))
    expect(themeState.resetTheme).toHaveBeenCalledTimes(1)
    expect(toastMock).toHaveBeenCalledWith({
      title: 'settings.appearance.resetSuccess',
      description: 'settings.appearance.resetSuccessDesc',
    })
  })
})

describe('AppearanceTab 样式微调与背景设置', () => {
  it('字体排版分组：展示覆盖后的字号并支持整组重置', async () => {
    const user = userEvent.setup()
    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: { typography: { 'font-size-base': '1.125rem' } as ThemeTokens['typography'] },
      },
    })
    render(<AppearanceTab />)

    await user.click(screen.getByRole('button', { name: 'settings.appearance.typographyGroup' }))
    // 覆盖值 1.125rem 换算为 18px 展示
    expect(await screen.findByText('18px')).toBeInTheDocument()

    // 分组内的重置按钮可用，点击后删除该分组覆盖
    const region = screen.getByRole('region')
    const resetButton = within(region).getByRole('button', { name: /resetDefault/ })
    expect(resetButton).toBeEnabled()

    await user.click(resetButton)
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: { modern: {} },
    })
  })

  it('背景设置：选择资源立即更新草稿并防抖写入 styleBackgroundConfig', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)

    await user.click(screen.getByRole('button', { name: 'settings.appearance.backgroundGroup' }))
    const panel = await screen.findByRole('tabpanel')

    await user.click(within(panel).getByRole('button', { name: 'mock-select-asset' }))
    // 本地草稿立即回显新资源 ID
    expect(within(panel).getByTestId('bg-asset-id')).toHaveTextContent('asset-1')

    // 防抖（180ms）后以 page 层写入配置
    await waitFor(() =>
      expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
        styleBackgroundConfig: {
          modern: { page: { ...defaultBackgroundConfig, assetId: 'asset-1', type: 'image' } },
        },
      })
    )
  })

  it('背景设置：效果与组件 CSS 修改在草稿上累积后持久化', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)

    await user.click(screen.getByRole('button', { name: 'settings.appearance.backgroundGroup' }))
    const panel = await screen.findByRole('tabpanel')

    // 第一步：调整效果（blur 0 -> 6）
    await user.click(within(panel).getByRole('button', { name: 'mock-change-effects' }))
    await waitFor(() =>
      expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
        styleBackgroundConfig: {
          modern: {
            page: {
              ...defaultBackgroundConfig,
              effects: { ...defaultBackgroundEffects, blur: 6 },
            },
          },
        },
      })
    )

    // 第二步：编辑组件 CSS，草稿保留上一步的效果修改
    await user.click(within(panel).getByRole('button', { name: 'mock-edit-component-css' }))
    await waitFor(() =>
      expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
        styleBackgroundConfig: {
          modern: {
            page: {
              ...defaultBackgroundConfig,
              effects: { ...defaultBackgroundEffects, blur: 6 },
              customCSS: '.page { color: red; }',
            },
          },
        },
      })
    )
  })

  it('背景设置：sidebar 层开启继承后禁用调节并写入 inherit', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)

    await user.click(screen.getByRole('button', { name: 'settings.appearance.backgroundGroup' }))
    await user.click(screen.getByRole('tab', { name: 'settings.appearance.bgSidebar' }))
    const panel = await screen.findByRole('tabpanel')

    // sidebar 层唯一的开关即"继承父级背景"
    await user.click(within(panel).getByRole('switch'))

    // 继承提示与禁用状态立即生效（草稿状态驱动）
    expect(within(panel).getByText(/该层当前直接继承界面背景/)).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'mock-select-asset' })).toBeDisabled()

    await waitFor(() =>
      expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
        styleBackgroundConfig: {
          modern: { sidebar: { ...defaultBackgroundConfig, inherit: true } },
        },
      })
    )
  })
})

describe('AppearanceTab 动效设置', () => {
  it('全局动画开关切换调用 setEnableAnimations', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)

    const animationSwitch = screen.getByRole('switch', {
      name: 'settings.appearance.enableAnimations',
    })
    expect(animationSwitch).toBeChecked()

    await user.click(animationSwitch)
    expect(animationState.setEnableAnimations).toHaveBeenCalledWith(false)
  })
})

/** 打开样式微调手风琴分组，返回展开后的 region */
async function openStyleGroup(user: ReturnType<typeof userEvent.setup>, groupKey: string) {
  await user.click(screen.getByRole('button', { name: groupKey }))
  return screen.getByRole('region')
}

/** aria-label 打在 Slider 根节点上，role=slider 在内部 Thumb */
function getLabeledSlider(label: string) {
  const root = document.querySelector(`[aria-label="${label}"]`)
  if (!root) {
    throw new Error(`未找到 aria-label=${label} 的滑块`)
  }
  return within(root as HTMLElement).getByRole('slider')
}

describe('AppearanceTab 主题色原生选择器与导入按钮', () => {
  it('原生 color 输入立即更新预览并防抖持久化 accentColor', async () => {
    const { container } = render(<AppearanceTab />)
    const colorInput = container.querySelector<HTMLInputElement>('input[type="color"]')
    expect(colorInput).not.toBeNull()

    fireEvent.change(colorInput!, { target: { value: '#00aaff' } })
    expect(colorInput).toHaveValue('#00aaff')

    await waitFor(() => {
      const lastCall = vi.mocked(applyThemePipeline).mock.calls.at(-1)
      expect(lastCall?.[0].accentColor).toBe(hexToHSL('#00aaff'))
    })

    await waitFor(() =>
      expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
        accentColor: hexToHSL('#00aaff'),
      })
    )
  })

  it('导入主题按钮点击隐藏文件框，成功导入后 1 秒刷新页面', () => {
    vi.useFakeTimers()
    try {
      vi.stubGlobal('FileReader', MockFileReader)
      mockFileText = '{"version":1}'
      vi.mocked(importThemeJSON).mockReturnValue({ success: true, errors: [] })

      const { container } = render(<AppearanceTab />)
      const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]')
      expect(fileInput).not.toBeNull()
      const inputClick = vi.spyOn(fileInput!, 'click')

      fireEvent.click(screen.getByRole('button', { name: 'settings.appearance.importTheme' }))
      expect(inputClick).toHaveBeenCalledTimes(1)

      // 未选择文件时 handleImport 直接返回
      fireEvent.change(fileInput!, { target: { files: null } })
      expect(importThemeJSON).not.toHaveBeenCalled()

      uploadThemeFile(container)
      expect(importThemeJSON).toHaveBeenCalledWith('{"version":1}')
      expect(toastMock).toHaveBeenCalledWith({
        title: 'settings.appearance.importSuccess',
        description: 'settings.appearance.importSuccessDesc',
      })

      // 导入成功后延迟整页刷新，使 ThemeProvider 重读 localStorage
      vi.advanceTimersByTime(1000)
    } finally {
      vi.clearAllTimers()
      vi.useRealTimers()
    }
  })
})

describe('AppearanceTab 样式微调 token 写入', () => {
  it('字体排版：字号滑块按基准像素写入整组 rem token', async () => {
    const user = userEvent.setup()
    render(<AppearanceTab />)
    const region = await openStyleGroup(user, 'settings.appearance.typographyGroup')

    const fontSlider = within(region).getAllByRole('slider')[0]
    fireEvent.keyDown(fontSlider, { key: 'ArrowRight' })

    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-size-xs': '0.7969rem',
            'font-size-sm': '0.9297rem',
            'font-size-base': '1.0625rem',
            'font-size-lg': '1.1953rem',
            'font-size-xl': '1.3281rem',
            'font-size-2xl': '1.5938rem',
          },
        },
      },
    })
  })

  it('字体排版：字族与行高 Select 分别写入对应 token', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<AppearanceTab />)
    const region = await openStyleGroup(user, 'settings.appearance.typographyGroup')
    const comboboxes = within(region).getAllByRole('combobox')

    // 默认计算字体不含 ui-serif / ui-monospace，回落到 sans
    expect(comboboxes[0]).toHaveTextContent('settings.appearance.fontFamilySans')

    await user.click(comboboxes[0])
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.fontFamilySerif' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-family-base': 'ui-serif, Georgia, Cambria, "Times New Roman", Times, serif',
          },
        },
      },
    })

    await user.click(comboboxes[0])
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.fontFamilyMono' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-family-base':
              'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
          },
        },
      },
    })

    await user.click(comboboxes[0])
    await user.click(
      await screen.findByRole('option', { name: 'settings.appearance.fontFamilySystem' })
    )
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-family-base': defaultLightTokens.typography['font-family-base'],
          },
        },
      },
    })

    await user.click(comboboxes[1])
    await user.click(
      await screen.findByRole('option', { name: 'settings.appearance.lineHeightCompact' })
    )
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          typography: {
            'line-height-normal': 1.2,
          },
        },
      },
    })

    await user.click(comboboxes[1])
    await user.click(
      await screen.findByRole('option', { name: 'settings.appearance.lineHeightLoose' })
    )
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          typography: {
            'line-height-normal': 1.75,
          },
        },
      },
    })
  })

  it('字体排版：覆盖值分别映射 serif / mono 选项，缺字号时回落 16px', async () => {
    const user = userEvent.setup()
    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-family-base': 'ui-serif, Georgia, serif',
            'line-height-normal': 1.75,
          } as ThemeTokens['typography'],
        },
      },
    })
    const { rerender } = render(<AppearanceTab />)
    let region = await openStyleGroup(user, 'settings.appearance.typographyGroup')
    expect(within(region).getAllByRole('combobox')[0]).toHaveTextContent(
      'settings.appearance.fontFamilySerif'
    )
    // 覆盖里没有 font-size-base，回落到计算默认 16px
    expect(within(region).getByText('16px')).toBeInTheDocument()

    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-family-base': 'ui-monospace, Menlo, monospace',
          } as ThemeTokens['typography'],
        },
      },
    })
    rerender(<AppearanceTab />)
    region = screen.getByRole('region')
    expect(within(region).getAllByRole('combobox')[0]).toHaveTextContent(
      'settings.appearance.fontFamilyMono'
    )
  })

  it('视觉效果：圆角滑块、阴影 Select 与模糊开关写入 visual token', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<AppearanceTab />)
    const region = await openStyleGroup(user, 'settings.appearance.visualGroup')

    fireEvent.keyDown(within(region).getByRole('slider'), { key: 'ArrowRight' })
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          visual: { 'radius-md': '0.4375rem' },
        },
      },
    })

    const shadowSelect = within(region).getByRole('combobox')
    expect(shadowSelect).toHaveTextContent('settings.appearance.shadowMd')

    await user.click(shadowSelect)
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.shadowNone' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: { visual: { 'shadow-md': 'none' } },
      },
    })

    await user.click(shadowSelect)
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.shadowSm' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: { visual: { 'shadow-md': defaultLightTokens.visual['shadow-sm'] } },
      },
    })

    await user.click(shadowSelect)
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.shadowLg' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: { visual: { 'shadow-md': defaultLightTokens.visual['shadow-lg'] } },
      },
    })

    await user.click(shadowSelect)
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.shadowXl' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: { visual: { 'shadow-md': defaultLightTokens.visual['shadow-xl'] } },
      },
    })

    const blurSwitch = within(region).getByRole('switch', {
      name: 'settings.appearance.blurLabel',
    })
    expect(blurSwitch).toBeChecked()
    await user.click(blurSwitch)
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: { visual: { 'blur-md': '0px' } },
      },
    })
  })

  it('视觉效果：已有覆盖映射阴影档位，模糊为 0 时开关关闭并可恢复', async () => {
    const user = userEvent.setup()
    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          visual: {
            'shadow-md': 'none',
            'blur-md': '0px',
          } as ThemeTokens['visual'],
        },
      },
    })
    const { rerender } = render(<AppearanceTab />)
    let region = await openStyleGroup(user, 'settings.appearance.visualGroup')
    expect(within(region).getByRole('combobox')).toHaveTextContent(
      'settings.appearance.shadowNone'
    )
    const blurSwitch = within(region).getByRole('switch', {
      name: 'settings.appearance.blurLabel',
    })
    expect(blurSwitch).not.toBeChecked()

    await user.click(blurSwitch)
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          visual: {
            'shadow-md': 'none',
            'blur-md': defaultLightTokens.visual['blur-md'],
          },
        },
      },
    })

    const resetButton = within(region).getByRole('button', { name: /resetDefault/ })
    expect(resetButton).toBeEnabled()
    await user.click(resetButton)
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: { modern: {} },
    })

    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          visual: {
            'shadow-md': defaultLightTokens.visual['shadow-sm'],
          } as ThemeTokens['visual'],
        },
      },
    })
    rerender(<AppearanceTab />)
    expect(within(screen.getByRole('region')).getByRole('combobox')).toHaveTextContent(
      'settings.appearance.shadowSm'
    )

    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          visual: {
            'shadow-md': defaultLightTokens.visual['shadow-lg'],
          } as ThemeTokens['visual'],
        },
      },
    })
    rerender(<AppearanceTab />)
    expect(within(screen.getByRole('region')).getByRole('combobox')).toHaveTextContent(
      'settings.appearance.shadowLg'
    )

    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          visual: {
            'shadow-md': defaultLightTokens.visual['shadow-xl'],
          } as ThemeTokens['visual'],
        },
      },
    })
    rerender(<AppearanceTab />)
    expect(within(screen.getByRole('region')).getByRole('combobox')).toHaveTextContent(
      'settings.appearance.shadowXl'
    )
  })

  it('从非默认覆盖切回 sans / md / normal 时写入默认 token', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-family-base': 'ui-serif, Georgia, Cambria, "Times New Roman", Times, serif',
          } as ThemeTokens['typography'],
          visual: { 'shadow-md': 'none' } as ThemeTokens['visual'],
          animation: { 'anim-duration-normal': '0ms' } as ThemeTokens['animation'],
        },
      },
    })
    animationState = { enableAnimations: false, setEnableAnimations: vi.fn() }
    render(<AppearanceTab />)

    const typography = await openStyleGroup(user, 'settings.appearance.typographyGroup')
    await user.click(within(typography).getAllByRole('combobox')[0])
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.fontFamilySans' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-family-base':
              'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
          },
          visual: { 'shadow-md': 'none' },
          animation: { 'anim-duration-normal': '0ms' },
        },
      },
    })

    const visual = await openStyleGroup(user, 'settings.appearance.visualGroup')
    await user.click(within(visual).getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.shadowMd' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-family-base': 'ui-serif, Georgia, Cambria, "Times New Roman", Times, serif',
          },
          visual: { 'shadow-md': defaultLightTokens.visual['shadow-md'] },
          animation: { 'anim-duration-normal': '0ms' },
        },
      },
    })

    const animation = await openStyleGroup(user, 'settings.appearance.animationGroup')
    await user.click(within(animation).getByRole('combobox'))
    await user.click(
      await screen.findByRole('option', { name: 'settings.appearance.animationNormal' })
    )
    expect(animationState.setEnableAnimations).toHaveBeenCalledWith(true)
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          typography: {
            'font-family-base': 'ui-serif, Georgia, Cambria, "Times New Roman", Times, serif',
          },
          visual: { 'shadow-md': 'none' },
          animation: { 'anim-duration-normal': '300ms' },
        },
      },
    })
  })

  it('布局：侧栏宽度滑块写入 rem，并支持整组重置', async () => {
    const user = userEvent.setup()
    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: { layout: { 'sidebar-width': '14rem' } as ThemeTokens['layout'] },
      },
    })
    render(<AppearanceTab />)
    const region = await openStyleGroup(user, 'settings.appearance.layoutGroup')
    expect(within(region).getByText('14rem')).toBeInTheDocument()

    fireEvent.keyDown(within(region).getByRole('slider'), { key: 'ArrowRight' })
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: {
          layout: { 'sidebar-width': '14.5rem' },
        },
      },
    })

    await user.click(within(region).getByRole('button', { name: /resetDefault/ }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: { modern: {} },
    })
  })

  it('动画速度：关闭时同步停用全局动画，并写入 duration token', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<AppearanceTab />)
    const region = await openStyleGroup(user, 'settings.appearance.animationGroup')
    const speedSelect = within(region).getByRole('combobox')
    expect(speedSelect).toHaveTextContent('settings.appearance.animationNormal')

    await user.click(speedSelect)
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.animationOff' }))
    expect(animationState.setEnableAnimations).toHaveBeenCalledWith(false)
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: { animation: { 'anim-duration-normal': '0ms' } },
      },
    })

    await user.click(speedSelect)
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.animationFast' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: { animation: { 'anim-duration-normal': '100ms' } },
      },
    })

    await user.click(speedSelect)
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.animationSlow' }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: { animation: { 'anim-duration-normal': '500ms' } },
      },
    })
  })

  it('动画速度：全局动画已关时选择非 off 会重新开启，覆盖值映射档位', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    animationState = { enableAnimations: false, setEnableAnimations: vi.fn() }
    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          animation: { 'anim-duration-normal': '0ms' } as ThemeTokens['animation'],
        },
      },
    })
    const { rerender } = render(<AppearanceTab />)
    let region = await openStyleGroup(user, 'settings.appearance.animationGroup')
    expect(within(region).getByRole('combobox')).toHaveTextContent(
      'settings.appearance.animationOff'
    )

    await user.click(within(region).getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'settings.appearance.animationFast' }))
    expect(animationState.setEnableAnimations).toHaveBeenCalledWith(true)
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        modern: { animation: { 'anim-duration-normal': '100ms' } },
      },
    })

    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          animation: { 'anim-duration-normal': '100ms' } as ThemeTokens['animation'],
        },
      },
    })
    rerender(<AppearanceTab />)
    expect(within(screen.getByRole('region')).getByRole('combobox')).toHaveTextContent(
      'settings.appearance.animationFast'
    )

    themeState = makeThemeState({
      styleTokenOverrides: {
        modern: {
          animation: { 'anim-duration-normal': '500ms' } as ThemeTokens['animation'],
        },
      },
    })
    rerender(<AppearanceTab />)
    expect(within(screen.getByRole('region')).getByRole('combobox')).toHaveTextContent(
      'settings.appearance.animationSlow'
    )

    await user.click(within(screen.getByRole('region')).getByRole('button', { name: /resetDefault/ }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: { modern: {} },
    })
  })
})

describe('AppearanceTab 未来复古 token 与滑块', () => {
  it('基准字号滑块写入缩放 rem，其它滑块回写 styleConfig', async () => {
    themeState = makeThemeState({ dashboardStyle: 'future-retro' })
    render(<AppearanceTab />)

    fireEvent.keyDown(getLabeledSlider('settings.appearance.baseFontSize'), { key: 'ArrowRight' })
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: {
        'future-retro': {
          typography: {
            'font-size-xs': '0.7969rem',
            'font-size-sm': '0.9297rem',
            'font-size-base': '1.0625rem',
            'font-size-lg': '1.1953rem',
            'font-size-xl': '1.3281rem',
            'font-size-2xl': '1.5938rem',
          },
        },
      },
    })

    fireEvent.keyDown(getLabeledSlider('settings.appearance.retroPaperWarmth'), {
      key: 'ArrowLeft',
    })
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleConfig: {
        futureRetro: { ...DEFAULT_FUTURE_RETRO_STYLE_CONFIG, paperWarmth: 99 },
      },
    })

    fireEvent.keyDown(getLabeledSlider('settings.appearance.retroTextureIntensity'), {
      key: 'ArrowRight',
    })
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleConfig: {
        futureRetro: { ...DEFAULT_FUTURE_RETRO_STYLE_CONFIG, textureIntensity: 56 },
      },
    })

    fireEvent.keyDown(getLabeledSlider('settings.appearance.retroPanelDepth'), { key: 'ArrowLeft' })
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleConfig: {
        futureRetro: { ...DEFAULT_FUTURE_RETRO_STYLE_CONFIG, panelDepth: 99 },
      },
    })

    fireEvent.keyDown(getLabeledSlider('settings.appearance.retroStrokeScale'), { key: 'ArrowLeft' })
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleConfig: {
        futureRetro: { ...DEFAULT_FUTURE_RETRO_STYLE_CONFIG, strokeScale: 99 },
      },
    })
  })

  it('纹理为 none 时禁用强度滑块；重置同时清排版覆盖并恢复默认风格', async () => {
    const user = userEvent.setup()
    themeState = makeThemeState({
      dashboardStyle: 'future-retro',
      styleTokenOverrides: {
        'future-retro': { typography: { 'font-size-base': '1.125rem' } as ThemeTokens['typography'] },
      },
      styleConfig: {
        futureRetro: { ...DEFAULT_FUTURE_RETRO_STYLE_CONFIG, textureStyle: 'none' },
      },
    })
    themeState.resolvedTheme = 'dark'
    render(<AppearanceTab />)

    // Radix 禁用态写在 data-disabled 上，thumb 不是原生 disabled 控件
    expect(getLabeledSlider('settings.appearance.retroTextureIntensity')).toHaveAttribute(
      'data-disabled'
    )

    const noneOption = screen.getByRole('button', {
      name: 'settings.appearance.retroTextureNone',
    })
    expect(noneOption).toHaveAttribute('aria-pressed', 'true')
    const preview = noneOption.querySelector('[aria-hidden]') as HTMLElement
    expect(preview.style.backgroundColor).toBe('rgb(17, 9, 6)')
    expect(preview.style.backgroundImage).toBe('none')

    await user.click(screen.getByRole('button', { name: /resetDefault/ }))
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleTokenOverrides: { 'future-retro': {} },
    })
    expect(themeState.updateThemeConfig).toHaveBeenCalledWith({
      styleConfig: {
        futureRetro: { ...DEFAULT_FUTURE_RETRO_STYLE_CONFIG },
      },
    })
  })
})
