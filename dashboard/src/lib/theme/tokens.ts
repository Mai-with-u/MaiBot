/**
 * Design Token Schema 定义
 * 集中管理所有设计令牌（颜色、排版、间距、阴影、动画等）
 */

// ============================================================================
// Color Tokens 类型定义
// ============================================================================

export type ColorTokens = {
  primary: string
  'primary-foreground': string
  'primary-gradient': string
  secondary: string
  'secondary-foreground': string
  muted: string
  'muted-foreground': string
  accent: string
  'accent-foreground': string
  destructive: string
  'destructive-foreground': string
  background: string
  foreground: string
  card: string
  'card-foreground': string
  popover: string
  'popover-foreground': string
  border: string
  input: string
  ring: string
  'chart-1': string
  'chart-2': string
  'chart-3': string
  'chart-4': string
  'chart-5': string
}

// ============================================================================
// Typography Tokens 类型定义
// ============================================================================

export type TypographyTokens = {
  'font-family-base': string
  'font-family-code': string
  'font-size-xs': string
  'font-size-sm': string
  'font-size-base': string
  'font-size-lg': string
  'font-size-xl': string
  'font-size-2xl': string
  'font-weight-normal': number
  'font-weight-medium': number
  'font-weight-semibold': number
  'font-weight-bold': number
  'line-height-tight': number
  'line-height-normal': number
  'line-height-relaxed': number
  'letter-spacing-tight': string
  'letter-spacing-normal': string
  'letter-spacing-wide': string
}

// ============================================================================
// Visual Tokens 类型定义
// ============================================================================

export type VisualTokens = {
  'radius-sm': string
  'radius-md': string
  'radius-lg': string
  'radius-xl': string
  'radius-full': string
  'shadow-sm': string
  'shadow-md': string
  'shadow-lg': string
  'shadow-xl': string
  'blur-sm': string
  'blur-md': string
  'blur-lg': string
  'opacity-disabled': number
  'opacity-hover': number
  'opacity-overlay': number
}

// ============================================================================
// Layout Tokens 类型定义
// ============================================================================

export type LayoutTokens = {
  'space-xs': string
  'space-sm': string
  'space-md': string
  'space-lg': string
  'space-xl': string
  'space-2xl': string
  'sidebar-width': string
  'sidebar-logo-height': string
  'sidebar-logo-padding-x': string
  'sidebar-nav-padding': string
  'sidebar-nav-padding-collapsed': string
  'sidebar-section-gap': string
  'sidebar-section-title-height': string
  'sidebar-section-title-margin-bottom': string
  'sidebar-section-title-margin-bottom-collapsed': string
  'sidebar-nav-item-gap': string
  'sidebar-collapsed-width': string
  'sidebar-nav-item-height': string
  'sidebar-nav-item-padding-x': string
  'sidebar-nav-item-collapsed-width': string
  'header-height': string
}

// ============================================================================
// Animation Tokens 类型定义
// ============================================================================

export type AnimationTokens = {
  'anim-duration-fast': string
  'anim-duration-normal': string
  'anim-duration-slow': string
  'anim-easing-default': string
  'anim-easing-in': string
  'anim-easing-out': string
  'anim-easing-in-out': string
  'transition-colors': string
  'transition-transform': string
  'transition-opacity': string
}

// ============================================================================
// Aggregated Theme Tokens
// ============================================================================

export type ThemeTokens = {
  color: ColorTokens
  typography: TypographyTokens
  visual: VisualTokens
  layout: LayoutTokens
  animation: AnimationTokens
}

// ============================================================================
// Theme Preset & Config Types
// ============================================================================

export type ThemePreset = {
  id: string
  name: string
  description: string
  tokens: ThemeTokens
  isDark: boolean
}

export type DashboardStyle = 'modern' | 'future-retro'

export type StyleTokenOverrides = Partial<Record<DashboardStyle, Partial<ThemeTokens>>>
export type StyleCustomCSS = Partial<Record<DashboardStyle, string>>
export type StyleBackgroundConfigMap = Partial<Record<DashboardStyle, BackgroundConfigMap>>

export type FutureRetroStyleConfig = {
  paperTexture: boolean
}

export type DashboardStyleConfig = {
  futureRetro: FutureRetroStyleConfig
}

export const DEFAULT_DASHBOARD_STYLE: DashboardStyle = 'future-retro'

export const DEFAULT_FUTURE_RETRO_STYLE_CONFIG: FutureRetroStyleConfig = {
  paperTexture: true,
}

export const DEFAULT_DASHBOARD_STYLE_CONFIG: DashboardStyleConfig = {
  futureRetro: DEFAULT_FUTURE_RETRO_STYLE_CONFIG,
}

export type UserThemeConfig = {
  selectedPreset: string
  accentColor: string
  styleTokenOverrides: StyleTokenOverrides
  styleCustomCSS: StyleCustomCSS
  styleBackgroundConfig?: StyleBackgroundConfigMap
  dashboardStyle: DashboardStyle
  styleConfig: DashboardStyleConfig
}

// ============================================================================
// Default Light Tokens (from index.css :root)
// ============================================================================

export const defaultLightTokens: ThemeTokens = {
  color: {
    primary: '28.9 94.8% 45.1%',
    'primary-foreground': '210 40% 98%',
    'primary-gradient': 'none',
    secondary: '188.5 35% 96%',
    'secondary-foreground': '222.2 47.4% 11.2%',
    muted: '188.5 12% 96%',
    'muted-foreground': '188.5 20% 46.9%',
    accent: '112.7 40.2% 47.8%',
    'accent-foreground': '210 40% 98%',
    destructive: '0 84.2% 60.2%',
    'destructive-foreground': '210 40% 98%',
    background: '0 0% 100%',
    foreground: '222.2 84% 4.9%',
    card: '188.5 14% 98.6%',
    'card-foreground': '222.2 84% 4.9%',
    popover: '188.5 16% 99.3%',
    'popover-foreground': '222.2 84% 4.9%',
    border: '188.5 20% 91.4%',
    input: '188.5 20% 91.4%',
    ring: '28.9 94.8% 45.1%',
    'chart-1': '28.9 94.8% 45.1%',
    'chart-2': '160 60% 45%',
    'chart-3': '30 80% 55%',
    'chart-4': '280 65% 60%',
    'chart-5': '340 75% 55%',
  },
  typography: {
    'font-family-base':
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    'font-family-code': '"JetBrains Mono", "Monaco", "Courier New", monospace',
    'font-size-xs': '0.75rem',
    'font-size-sm': '0.875rem',
    'font-size-base': '1rem',
    'font-size-lg': '1.125rem',
    'font-size-xl': '1.25rem',
    'font-size-2xl': '1.5rem',
    'font-weight-normal': 400,
    'font-weight-medium': 500,
    'font-weight-semibold': 600,
    'font-weight-bold': 700,
    'line-height-tight': 1.2,
    'line-height-normal': 1.5,
    'line-height-relaxed': 1.75,
    'letter-spacing-tight': '-0.02em',
    'letter-spacing-normal': '0em',
    'letter-spacing-wide': '0.02em',
  },
  visual: {
    'radius-sm': '0.25rem',
    'radius-md': '0.375rem',
    'radius-lg': '0.5rem',
    'radius-xl': '0.75rem',
    'radius-full': '9999px',
    'shadow-sm': '0 1px 2px 0 rgba(0, 0, 0, 0.05)',
    'shadow-md': '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
    'shadow-lg': '0 10px 15px -3px rgba(0, 0, 0, 0.1)',
    'shadow-xl': '0 20px 25px -5px rgba(0, 0, 0, 0.1)',
    'blur-sm': '4px',
    'blur-md': '12px',
    'blur-lg': '24px',
    'opacity-disabled': 0.5,
    'opacity-hover': 0.8,
    'opacity-overlay': 0.75,
  },
  layout: {
    'space-xs': '0.5rem',
    'space-sm': '0.75rem',
    'space-md': '1rem',
    'space-lg': '1.5rem',
    'space-xl': '2rem',
    'space-2xl': '3rem',
    'sidebar-width': '13rem',
    'sidebar-logo-height': '5rem',
    'sidebar-logo-padding-x': '1rem',
    'sidebar-nav-padding': '1rem',
    'sidebar-nav-padding-collapsed': '0.5rem',
    'sidebar-section-gap': '0.75rem',
    'sidebar-section-title-height': '1.25rem',
    'sidebar-section-title-margin-bottom': '0.5rem',
    'sidebar-section-title-margin-bottom-collapsed': '0.25rem',
    'sidebar-nav-item-gap': '0.25rem',
    'sidebar-collapsed-width': '4rem',
    'sidebar-nav-item-height': '2.5rem',
    'sidebar-nav-item-padding-x': '0.75rem',
    'sidebar-nav-item-collapsed-width': '3rem',
    'header-height': '3.5rem',
  },
  animation: {
    'anim-duration-fast': '150ms',
    'anim-duration-normal': '300ms',
    'anim-duration-slow': '500ms',
    'anim-easing-default': 'cubic-bezier(0.4, 0, 0.2, 1)',
    'anim-easing-in': 'cubic-bezier(0.4, 0, 1, 1)',
    'anim-easing-out': 'cubic-bezier(0, 0, 0.2, 1)',
    'anim-easing-in-out': 'cubic-bezier(0.4, 0, 0.2, 1)',
    'transition-colors': 'color 300ms cubic-bezier(0.4, 0, 0.2, 1)',
    'transition-transform': 'transform 300ms cubic-bezier(0.4, 0, 0.2, 1)',
    'transition-opacity': 'opacity 300ms cubic-bezier(0.4, 0, 0.2, 1)',
  },
}

// ============================================================================
// Default Dark Tokens (from index.css .dark)
// ============================================================================

export const defaultDarkTokens: ThemeTokens = {
  color: {
    primary: '28.9 94.8% 45.1%',
    'primary-foreground': '210 40% 98%',
    'primary-gradient': 'none',
    secondary: '188.5 35% 17.5%',
    'secondary-foreground': '210 40% 98%',
    muted: '188.5 12% 17.5%',
    'muted-foreground': '188.5 20% 65.1%',
    accent: '112.7 40.2% 34%',
    'accent-foreground': '210 40% 98%',
    destructive: '0 62.8% 30.6%',
    'destructive-foreground': '210 40% 98%',
    background: '222.2 84% 4.9%',
    foreground: '210 40% 98%',
    card: '188.5 18% 8.8%',
    'card-foreground': '210 40% 98%',
    popover: '188.5 21% 10.5%',
    'popover-foreground': '210 40% 98%',
    border: '188.5 20% 17.5%',
    input: '188.5 20% 17.5%',
    ring: '28.9 94.8% 45.1%',
    'chart-1': '28.9 94.8% 45.1%',
    'chart-2': '160 60% 50%',
    'chart-3': '30 80% 60%',
    'chart-4': '280 65% 65%',
    'chart-5': '340 75% 60%',
  },
  typography: {
    'font-family-base':
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    'font-family-code': '"JetBrains Mono", "Monaco", "Courier New", monospace',
    'font-size-xs': '0.75rem',
    'font-size-sm': '0.875rem',
    'font-size-base': '1rem',
    'font-size-lg': '1.125rem',
    'font-size-xl': '1.25rem',
    'font-size-2xl': '1.5rem',
    'font-weight-normal': 400,
    'font-weight-medium': 500,
    'font-weight-semibold': 600,
    'font-weight-bold': 700,
    'line-height-tight': 1.2,
    'line-height-normal': 1.5,
    'line-height-relaxed': 1.75,
    'letter-spacing-tight': '-0.02em',
    'letter-spacing-normal': '0em',
    'letter-spacing-wide': '0.02em',
  },
  visual: {
    'radius-sm': '0.25rem',
    'radius-md': '0.375rem',
    'radius-lg': '0.5rem',
    'radius-xl': '0.75rem',
    'radius-full': '9999px',
    'shadow-sm': '0 1px 2px 0 rgba(0, 0, 0, 0.25)',
    'shadow-md': '0 4px 6px -1px rgba(0, 0, 0, 0.3)',
    'shadow-lg': '0 10px 15px -3px rgba(0, 0, 0, 0.4)',
    'shadow-xl': '0 20px 25px -5px rgba(0, 0, 0, 0.5)',
    'blur-sm': '4px',
    'blur-md': '12px',
    'blur-lg': '24px',
    'opacity-disabled': 0.5,
    'opacity-hover': 0.8,
    'opacity-overlay': 0.75,
  },
  layout: {
    'space-xs': '0.5rem',
    'space-sm': '0.75rem',
    'space-md': '1rem',
    'space-lg': '1.5rem',
    'space-xl': '2rem',
    'space-2xl': '3rem',
    'sidebar-width': '13rem',
    'sidebar-logo-height': '5rem',
    'sidebar-logo-padding-x': '1rem',
    'sidebar-nav-padding': '1rem',
    'sidebar-nav-padding-collapsed': '0.5rem',
    'sidebar-section-gap': '0.75rem',
    'sidebar-section-title-height': '1.25rem',
    'sidebar-section-title-margin-bottom': '0.5rem',
    'sidebar-section-title-margin-bottom-collapsed': '0.25rem',
    'sidebar-nav-item-gap': '0.25rem',
    'sidebar-collapsed-width': '4rem',
    'sidebar-nav-item-height': '2.5rem',
    'sidebar-nav-item-padding-x': '0.75rem',
    'sidebar-nav-item-collapsed-width': '3rem',
    'header-height': '3.5rem',
  },
  animation: {
    'anim-duration-fast': '150ms',
    'anim-duration-normal': '300ms',
    'anim-duration-slow': '500ms',
    'anim-easing-default': 'cubic-bezier(0.4, 0, 0.2, 1)',
    'anim-easing-in': 'cubic-bezier(0.4, 0, 1, 1)',
    'anim-easing-out': 'cubic-bezier(0, 0, 0.2, 1)',
    'anim-easing-in-out': 'cubic-bezier(0.4, 0, 0.2, 1)',
    'transition-colors': 'color 300ms cubic-bezier(0.4, 0, 0.2, 1)',
    'transition-transform': 'transform 300ms cubic-bezier(0.4, 0, 0.2, 1)',
    'transition-opacity': 'opacity 300ms cubic-bezier(0.4, 0, 0.2, 1)',
  },
}

// ============================================================================
// Future Retro Tokens (MaiBotOneKey shell inspired)
// ============================================================================

const futureRetroBaseTypography = {
  'font-family-base': '"MaiRetroText", "Microsoft YaHei UI", system-ui, sans-serif',
  'font-family-code': '"Agency FB", "Cascadia Mono", "JetBrains Mono", Consolas, monospace',
  'font-weight-normal': 700,
  'font-weight-medium': 700,
  'font-weight-semibold': 800,
  'font-weight-bold': 800,
  'letter-spacing-tight': '0em',
  'letter-spacing-normal': '0em',
  'letter-spacing-wide': '0em',
} satisfies Partial<TypographyTokens>

const futureRetroBaseVisual = {
  'radius-sm': '2px',
  'radius-md': '3px',
  'radius-lg': '4px',
  'radius-xl': '4px',
  'shadow-sm': 'none',
  'shadow-md': 'none',
  'shadow-lg': 'none',
  'shadow-xl': 'none',
} satisfies Partial<VisualTokens>

const futureRetroBaseLayout = {
  'sidebar-width': '11rem',
  'sidebar-logo-height': '5rem',
  'sidebar-logo-padding-x': '0.75rem',
  'sidebar-nav-padding': '1rem',
  'sidebar-nav-padding-collapsed': '0.5rem',
  'sidebar-section-gap': '0.75rem',
  'sidebar-section-title-height': '1.25rem',
  'sidebar-section-title-margin-bottom': '0.5rem',
  'sidebar-section-title-margin-bottom-collapsed': '0.25rem',
  'sidebar-nav-item-gap': '0.25rem',
  'sidebar-collapsed-width': '4rem',
  'sidebar-nav-item-height': '2.4rem',
  'sidebar-nav-item-padding-x': '0.75rem',
  'sidebar-nav-item-collapsed-width': '3rem',
} satisfies Partial<LayoutTokens>

export const futureRetroLightTokens: Partial<ThemeTokens> = {
  color: {
    ...defaultLightTokens.color,
    primary: '15.6 68.7% 45.1%',
    'primary-foreground': '39.5 100% 92%',
    'primary-gradient': 'none',
    secondary: '34.1 54.8% 81.8%',
    'secondary-foreground': '189.1 59.6% 17.5%',
    muted: '34.9 48.3% 82.5%',
    'muted-foreground': '39.1 11.6% 39%',
    accent: '34.7 45.6% 75.5%',
    'accent-foreground': '189 72% 18.2%',
    background: '35.4 61.9% 87.6%',
    foreground: '189 72% 18.2%',
    card: '35.4 61.9% 87.6%',
    'card-foreground': '189 72% 18.2%',
    popover: '36 66% 89.6%',
    'popover-foreground': '189 72% 18.2%',
    border: '188.1 74% 19.6%',
    input: '39.1 11.6% 43.5%',
    ring: '15.6 68.7% 45.1%',
    'chart-1': '15.6 68.7% 45.1%',
    'chart-2': '189 72% 18.2%',
    'chart-3': '39.7 56.3% 51.6%',
    'chart-4': '39.1 11.6% 39%',
    'chart-5': '34.1 54.8% 81.8%',
  },
  typography: {
    ...defaultLightTokens.typography,
    ...futureRetroBaseTypography,
  },
  visual: {
    ...defaultLightTokens.visual,
    ...futureRetroBaseVisual,
  },
  layout: {
    ...defaultLightTokens.layout,
    ...futureRetroBaseLayout,
  },
}

export const futureRetroDarkTokens: Partial<ThemeTokens> = {
  color: {
    ...defaultDarkTokens.color,
    primary: '19.2 44.7% 42.5%',
    'primary-foreground': '34 40% 86%',
    'primary-gradient': 'none',
    secondary: '24 16% 18%',
    'secondary-foreground': '34 28% 76%',
    muted: '24 14% 17%',
    'muted-foreground': '34 19% 58%',
    accent: '40 24% 28%',
    'accent-foreground': '34 30% 78%',
    background: '17 25.9% 10.6%',
    foreground: '34.3 31.8% 74.3%',
    card: '20 21.3% 13.9%',
    'card-foreground': '34.3 31.8% 74.3%',
    popover: '24 20% 15%',
    'popover-foreground': '34.3 31.8% 74.3%',
    border: '31 18% 28%',
    input: '31 16% 30%',
    ring: '20 43% 45%',
    'chart-1': '19.2 44.7% 42.5%',
    'chart-2': '40.3 33.3% 46.9%',
    'chart-3': '91 14% 42%',
    'chart-4': '8 24% 43%',
    'chart-5': '34.3 31.8% 74.3%',
  },
  typography: {
    ...defaultDarkTokens.typography,
    ...futureRetroBaseTypography,
  },
  visual: {
    ...defaultDarkTokens.visual,
    ...futureRetroBaseVisual,
  },
  layout: {
    ...defaultDarkTokens.layout,
    ...futureRetroBaseLayout,
  },
}

// ============================================================================
// Token Utility Functions
// ============================================================================

/**
 * 将 Token 类别和 key 转换为 CSS 变量名
 * @example tokenToCSSVarName('color', 'primary') => '--color-primary'
 */
export function tokenToCSSVarName(
  category: keyof ThemeTokens | 'color' | 'typography' | 'visual' | 'layout' | 'animation',
  key: string
): string {
  return `--${category}-${key}`
}

// ============================================================================
// Background Config Types
// ============================================================================

export type BackgroundEffects = {
  blur: number // px, 0-50
  overlayColor: string // HSL string，如 '0 0% 0%'
  overlayOpacity: number // 0-1
  position: 'cover' | 'contain' | 'center' | 'stretch'
  brightness: number // 0-200, default 100
  contrast: number // 0-200, default 100
  saturate: number // 0-200, default 100
  gradientOverlay?: string // CSS gradient string（可选）
}

export type BackgroundConfig = {
  type: 'none' | 'image' | 'video'
  assetId?: string // IndexedDB asset ID
  inherit?: boolean // true = 继承页面背景
  effects: BackgroundEffects
  customCSS: string // 组件级自定义 CSS
}

export type BackgroundConfigMap = {
  page?: BackgroundConfig
  sidebar?: BackgroundConfig
  header?: BackgroundConfig
  card?: BackgroundConfig
  dialog?: BackgroundConfig
}

export const defaultBackgroundEffects: BackgroundEffects = {
  blur: 0,
  overlayColor: '0 0% 0%',
  overlayOpacity: 0,
  position: 'cover',
  brightness: 100,
  contrast: 100,
  saturate: 100,
}

export const defaultBackgroundConfig: BackgroundConfig = {
  type: 'none',
  effects: defaultBackgroundEffects,
  customCSS: '',
}
