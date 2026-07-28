import type { ReactNode } from 'react'

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BackgroundEffectsControls } from '@/components/background-effects-controls'
import { BackgroundLayer } from '@/components/background-layer'
import { ThemeProvider } from '@/components/theme-provider'
import { useTheme } from '@/components/use-theme'
import type { BackgroundConfig, BackgroundEffects, UserThemeConfig } from '@/lib/theme/tokens'
import { DEFAULT_DASHBOARD_STYLE_CONFIG, defaultBackgroundEffects } from '@/lib/theme/tokens'

const mocks = vi.hoisted(() => ({
  getAssetUrl: vi.fn(),
  getBotConfig: vi.fn(),
  updateBotConfigSection: vi.fn(),
  loadThemeConfig: vi.fn(),
  resetThemeToDefault: vi.fn(),
  saveThemePartial: vi.fn(),
  applyThemePipeline: vi.fn(),
  removeCustomCSS: vi.fn(),
}))

vi.mock('@/lib/asset-store-context', () => ({
  useAssetStore: () => ({ getAssetUrl: mocks.getAssetUrl }),
}))

vi.mock('@/lib/config-api', () => ({
  getBotConfig: mocks.getBotConfig,
  updateBotConfigSection: mocks.updateBotConfigSection,
}))

vi.mock('@/lib/theme/storage', () => ({
  THEME_STORAGE_KEYS: { MODE: 'maibot-theme-mode' },
  loadThemeConfig: mocks.loadThemeConfig,
  resetThemeToDefault: mocks.resetThemeToDefault,
  saveThemePartial: mocks.saveThemePartial,
}))

vi.mock('@/lib/theme/pipeline', () => ({
  applyThemePipeline: mocks.applyThemePipeline,
  removeCustomCSS: mocks.removeCustomCSS,
}))

vi.mock('@/components/ui/slider', () => ({
  Slider: ({
    value,
    onValueChange,
    disabled,
  }: {
    value: number[]
    onValueChange: (value: number[]) => void
    disabled?: boolean
  }) => (
    <button
      type="button"
      aria-label="模拟滑块"
      data-value={value[0]}
      disabled={disabled}
      onClick={() => onValueChange([value[0] + 10])}
    >
      调整
    </button>
  ),
}))

vi.mock('@/components/ui/select', () => ({
  Select: ({
    children,
    onValueChange,
    disabled,
  }: {
    children: ReactNode
    onValueChange: (value: string) => void
    disabled?: boolean
  }) => (
    <div>
      <button
        type="button"
        aria-label="选择拉伸"
        disabled={disabled}
        onClick={() => onValueChange('stretch')}
      >
        拉伸
      </button>
      {children}
    </div>
  ),
  SelectTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))

function makeThemeConfig(overrides: Partial<UserThemeConfig> = {}): UserThemeConfig {
  return {
    selectedPreset: 'light',
    accentColor: '',
    styleTokenOverrides: {},
    styleCustomCSS: {},
    styleBackgroundConfig: {},
    dashboardStyle: 'modern',
    styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
    ...overrides,
  }
}

function makeBackgroundConfig(
  type: BackgroundConfig['type'],
  overrides: Partial<BackgroundConfig> = {}
): BackgroundConfig {
  return {
    type,
    assetId: type === 'none' ? undefined : 'asset-1',
    effects: { ...defaultBackgroundEffects },
    customCSS: '',
    ...overrides,
  }
}

function ThemeProbe() {
  const { resetTheme, resolvedTheme, setTheme, theme, themeConfig, updateThemeConfig } = useTheme()

  return (
    <div>
      <span data-testid="theme-state">
        {theme}:{resolvedTheme}:{themeConfig.dashboardStyle}
      </span>
      <button type="button" onClick={() => setTheme('light')}>
        切到亮色
      </button>
      <button type="button" onClick={() => updateThemeConfig({ dashboardStyle: 'future-retro' })}>
        切到复古
      </button>
      <button type="button" onClick={resetTheme}>
        重置主题
      </button>
    </div>
  )
}

describe('ThemeProvider', () => {
  const mediaListeners = new Map<string, Set<() => void>>()
  let darkMode = false

  beforeEach(() => {
    darkMode = false
    mediaListeners.clear()
    localStorage.clear()
    window.history.replaceState(null, '', '/')
    mocks.loadThemeConfig.mockReturnValue(makeThemeConfig())
    mocks.getBotConfig.mockResolvedValue({ webui: {} })
    mocks.updateBotConfigSection.mockResolvedValue(undefined)
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => {
        const listeners = mediaListeners.get(query) ?? new Set<() => void>()
        mediaListeners.set(query, listeners)
        return {
          matches: query.includes('prefers-color-scheme') ? darkMode : false,
          media: query,
          addEventListener: (_event: string, listener: () => void) => listeners.add(listener),
          removeEventListener: (_event: string, listener: () => void) => listeners.delete(listener),
        }
      })
    )
    vi.spyOn(console, 'debug').mockImplementation(() => undefined)
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  })

  afterEach(() => {
    cleanup()
    document.documentElement.classList.remove('light', 'dark')
    delete document.documentElement.dataset.dashboardStyle
    delete document.documentElement.dataset.retroFocusHighlight
    delete document.documentElement.dataset.retroPaperTexture
    vi.clearAllMocks()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('解析系统主题、应用主题流水线并持久化显式主题模式', () => {
    darkMode = true
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    )

    expect(screen.getByTestId('theme-state')).toHaveTextContent('system:dark:modern')
    expect(document.documentElement).toHaveClass('dark')
    expect(document.documentElement.dataset.dashboardStyle).toBe('modern')
    expect(mocks.applyThemePipeline).toHaveBeenCalledWith(
      expect.objectContaining({ dashboardStyle: 'modern' }),
      true
    )

    fireEvent.click(screen.getByRole('button', { name: '切到亮色' }))
    expect(localStorage.getItem('maibot-theme-mode')).toBe('light')
    expect(screen.getByTestId('theme-state')).toHaveTextContent('light:light:modern')
    expect(document.documentElement).toHaveClass('light')
  })

  it('系统配色变化时重新解析 system 模式', () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    )
    expect(screen.getByTestId('theme-state')).toHaveTextContent('system:light:modern')

    darkMode = true
    act(() => {
      for (const listener of mediaListeners.get('(prefers-color-scheme: dark)') ?? []) {
        listener()
      }
    })
    expect(screen.getByTestId('theme-state')).toHaveTextContent('system:dark:modern')
  })

  it('读取远端 WebUI 风格，并在本地切换时写回数值协议', async () => {
    mocks.getBotConfig.mockResolvedValue({ webui: { webui_style: 1 } })
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    )

    await waitFor(() =>
      expect(screen.getByTestId('theme-state')).toHaveTextContent('system:light:future-retro')
    )
    expect(mocks.saveThemePartial).toHaveBeenCalledWith({
      dashboardStyle: 'future-retro',
    })

    fireEvent.click(screen.getByRole('button', { name: '切到复古' }))
    await waitFor(() =>
      expect(mocks.updateBotConfigSection).toHaveBeenCalledWith('webui', {
        webui_style: 1,
      })
    )
  })

  it('重置主题时清理自定义 CSS，并把默认风格同步到远端', async () => {
    mocks.loadThemeConfig
      .mockReturnValueOnce(makeThemeConfig({ dashboardStyle: 'future-retro' }))
      .mockReturnValueOnce(makeThemeConfig({ dashboardStyle: 'modern' }))
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: '重置主题' }))

    expect(mocks.resetThemeToDefault).toHaveBeenCalledOnce()
    expect(mocks.removeCustomCSS).toHaveBeenCalledOnce()
    await waitFor(() =>
      expect(mocks.updateBotConfigSection).toHaveBeenCalledWith('webui', {
        webui_style: 0,
      })
    )
  })

  it('认证页跳过远端读取和写入', async () => {
    window.history.replaceState(null, '', '/auth')
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: '切到复古' }))
    await act(async () => {
      await Promise.resolve()
    })

    expect(mocks.getBotConfig).not.toHaveBeenCalled()
    expect(mocks.updateBotConfigSection).not.toHaveBeenCalled()
  })
})

describe('BackgroundLayer', () => {
  const reduceListeners = new Set<() => void>()
  const play = vi.fn(() => Promise.resolve())
  const pause = vi.fn()

  beforeEach(() => {
    mocks.getAssetUrl.mockResolvedValue('blob:asset-1')
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({
        matches: false,
        addEventListener: (_event: string, listener: () => void) => reduceListeners.add(listener),
        removeEventListener: (_event: string, listener: () => void) =>
          reduceListeners.delete(listener),
      }))
    )
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(play)
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(pause)
  })

  afterEach(() => {
    cleanup()
    reduceListeners.clear()
    vi.clearAllMocks()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('none 类型不渲染任何背景节点', () => {
    const { container } = render(
      <BackgroundLayer config={makeBackgroundConfig('none')} layerId="page" />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('解析图片资源，组合滤镜、尺寸、自动遮罩和页面渐变', async () => {
    const config = makeBackgroundConfig('image', {
      effects: {
        ...defaultBackgroundEffects,
        blur: 4,
        brightness: 90,
        contrast: 110,
        saturate: 120,
        position: 'contain',
      },
    })
    const { container } = render(<BackgroundLayer config={config} layerId="page" />)

    await waitFor(() =>
      expect(container.querySelector('[style*="background-image"]')).toHaveStyle({
        backgroundImage: 'url(blob:asset-1)',
        backgroundSize: 'contain',
        filter: 'blur(4px) brightness(90%) contrast(110%) saturate(120%)',
      })
    )
    expect(container.querySelector('[style*="background-color"]')?.getAttribute('style')).toContain(
      'hsl(var(--background) / 0.62)'
    )
    expect(container.querySelector('[style*="linear-gradient"]')).toBeInTheDocument()
  })

  it('显式遮罩覆盖自动值，并为非页面层禁用自动渐变', async () => {
    const config = makeBackgroundConfig('image', {
      effects: {
        ...defaultBackgroundEffects,
        overlayColor: '10 20% 30%',
        overlayOpacity: 0.25,
      },
    })
    const { container } = render(<BackgroundLayer config={config} layerId="card" />)

    await waitFor(() =>
      expect(container.querySelector('[style*="background-color"]')).toHaveStyle({
        backgroundColor: 'rgba(92, 66, 61, 0.25)',
      })
    )
    expect(container.querySelector('[style*="linear-gradient"]')).not.toBeInTheDocument()
  })

  it('视频资源按位置映射 object-fit，并响应减少动态效果设置', async () => {
    const config = makeBackgroundConfig('video', {
      effects: { ...defaultBackgroundEffects, position: 'stretch' },
    })
    const view = render(<BackgroundLayer config={makeBackgroundConfig('image')} layerId="header" />)
    await waitFor(() =>
      expect(view.container.querySelector('[style*="background-image"]')).toBeInTheDocument()
    )
    view.rerender(<BackgroundLayer config={config} layerId="header" />)

    const video = await waitFor(() => {
      const element = view.container.querySelector('video')
      expect(element).toBeInTheDocument()
      return element as HTMLVideoElement
    })
    expect(video).toHaveAttribute('src', 'blob:asset-1')
    expect(video).toHaveStyle({ objectFit: 'fill' })
    expect(play).toHaveBeenCalled()

    fireEvent.error(video)
    expect(pause).toHaveBeenCalled()
  })
})

describe('BackgroundEffectsControls', () => {
  const effects: BackgroundEffects = {
    ...defaultBackgroundEffects,
    overlayColor: '0 100% 50%',
    gradientOverlay: 'linear-gradient(red, blue)',
  }

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('转换颜色并分别更新滑块、位置和渐变字段', () => {
    const onChange = vi.fn()
    render(<BackgroundEffectsControls effects={effects} onChange={onChange} />)

    expect(screen.getAllByDisplayValue('#ff0000')).toHaveLength(2)
    const sliders = screen.getAllByRole('button', { name: '模拟滑块' })
    fireEvent.click(sliders[0])
    fireEvent.click(sliders[1])
    fireEvent.click(sliders[2])
    expect(onChange).toHaveBeenCalledWith({ ...effects, blur: 10 })
    expect(onChange).toHaveBeenCalledWith({ ...effects, overlayOpacity: 0.1 })
    expect(onChange).toHaveBeenCalledWith({ ...effects, brightness: 110 })

    fireEvent.change(document.querySelector('input[type="color"]') as HTMLInputElement, {
      target: { value: '#00ff00' },
    })
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ overlayColor: '120 100% 50%' }))

    fireEvent.click(screen.getByRole('button', { name: '选择拉伸' }))
    expect(onChange).toHaveBeenCalledWith({ ...effects, position: 'stretch' })

    fireEvent.change(
      screen.getByPlaceholderText('e.g. linear-gradient(to bottom, transparent, black)'),
      { target: { value: 'linear-gradient(black, transparent)' } }
    )
    expect(onChange).toHaveBeenCalledWith({
      ...effects,
      gradientOverlay: 'linear-gradient(black, transparent)',
    })
  })

  it('重置返回默认效果，禁用态阻止所有变更', () => {
    const onChange = vi.fn()
    const { rerender } = render(<BackgroundEffectsControls effects={effects} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: '重置默认' }))
    expect(onChange).toHaveBeenCalledWith(defaultBackgroundEffects)

    onChange.mockClear()
    rerender(<BackgroundEffectsControls effects={effects} onChange={onChange} disabled />)
    fireEvent.click(screen.getByRole('button', { name: '重置默认' }))
    fireEvent.click(screen.getAllByRole('button', { name: '模拟滑块' })[0])
    fireEvent.click(screen.getByRole('button', { name: '选择拉伸' }))
    expect(onChange).not.toHaveBeenCalled()
  })
})
