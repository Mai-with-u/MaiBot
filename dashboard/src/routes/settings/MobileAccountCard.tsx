import { ChevronsUpDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { LANGUAGE_CODES, LANGUAGE_NAMES } from '@/components/layout/language-options'
import type { LanguageCode } from '@/components/layout/language-options'
import { Card } from '@/components/ui/card'

/**
 * 手机端的语言切换：桌面端在顶栏里，手机上顶栏隐藏后收进「WebUI 设置」页底部。
 */
export function MobileAccountCard() {
  const { t, i18n } = useTranslation()
  const currentLang = (i18n.resolvedLanguage ?? i18n.language).split('-')[0] as LanguageCode

  return (
    <div data-settings-mobile-account="true" className="mt-6 space-y-4 lg:hidden">
      <Card className="overflow-hidden p-0">
        <label className="relative flex h-12 items-center gap-3 px-4 text-base">
          <span className="flex-1">{t('header.switchLanguage')}</span>
          <span className="text-muted-foreground">
            {LANGUAGE_NAMES[currentLang] ?? currentLang}
          </span>
          <ChevronsUpDown className="text-muted-foreground h-4 w-4" aria-hidden="true" />
          {/* 原生下拉在手机上会弹出系统选择器 */}
          <select
            value={currentLang}
            onChange={(event) => void i18n.changeLanguage(event.target.value)}
            aria-label={t('header.switchLanguage')}
            className="absolute inset-0 cursor-pointer opacity-0"
          >
            {LANGUAGE_CODES.map((code) => (
              <option key={code} value={code}>
                {LANGUAGE_NAMES[code]}
              </option>
            ))}
          </select>
        </label>
      </Card>
    </div>
  )
}
