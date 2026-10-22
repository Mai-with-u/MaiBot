import { ChevronsUpDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { LANGUAGE_CODES, LANGUAGE_NAMES } from '@/components/layout/language-options'
import type { LanguageCode } from '@/components/layout/language-options'
import { Card } from '@/components/ui/card'

/**
 * 通用设置：桌面端与手机端统一在此切换界面语言。
 */
export function GeneralTab() {
  const { t, i18n } = useTranslation()
  const currentLang = (i18n.resolvedLanguage ?? i18n.language).split('-')[0] as LanguageCode

  return (
    <div className="space-y-6 sm:space-y-8">
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
