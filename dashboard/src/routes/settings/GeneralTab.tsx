import { useTranslation } from 'react-i18next'

import { LANGUAGE_CODES, LANGUAGE_NAMES } from '@/components/layout/language-options'
import type { LanguageCode } from '@/components/layout/language-options'
import { Card } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import { HostUpdateSection } from '@/host/HostUpdateSection'
import { ComponentLibrary } from './component-library'

/**
 * 通用设置：桌面端与手机端统一在此切换界面语言和管理主程序更新。
 */
export function GeneralTab() {
  const { t, i18n } = useTranslation()
  const currentLang = (i18n.resolvedLanguage ?? i18n.language).split('-')[0] as LanguageCode

  return (
    <div className="space-y-6 sm:space-y-8">
      <Card className="overflow-hidden p-0">
        <div className="flex min-h-12 items-center gap-3 px-4 py-2 text-base">
          <span className="flex-1">{t('header.switchLanguage')}</span>
          <Select value={currentLang} onValueChange={(value) => void i18n.changeLanguage(value)}>
            <SelectTrigger aria-label={t('header.switchLanguage')} className="w-36 shrink-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LANGUAGE_CODES.map((code) => (
                <SelectItem key={code} value={code}>
                  {LANGUAGE_NAMES[code]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </Card>
      <ComponentLibrary />
      {/* 主程序更新 */}
      <HostUpdateSection />
    </div>
  )
}
