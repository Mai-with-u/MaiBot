import { useTranslation } from 'react-i18next'

/**
 * Skip-to-content 无障碍导航链接
 *
 * 默认视觉上隐藏（sr-only），当键盘用户 Tab 聚焦时显示，
 * 允许屏幕阅读器/键盘用户跳过重复的导航区域直达主内容。
 *
 * 使用 focus-visible 而非 focus，鼠标点击不触发显示。
 */
export function SkipNav() {
  const { t } = useTranslation()

  return (
    <a
      href="#main-content"
      className={[
        'sr-only',
        'focus-visible:not-sr-only',
        'focus-visible:fixed',
        'focus-visible:left-4',
        'focus-visible:top-4',
        'focus-visible:z-[9999]',
        'focus-visible:rounded-md',
        'focus-visible:bg-background',
        'focus-visible:px-4',
        'focus-visible:py-2',
        'focus-visible:text-sm',
        'focus-visible:font-medium',
        'focus-visible:text-foreground',
        'focus-visible:shadow-md',
        'focus-visible:outline-none',
        'focus-visible:ring-2',
        'focus-visible:ring-ring',
      ].join(' ')}
    >
      {t('a11y.skipToContent')}
    </a>
  )
}
