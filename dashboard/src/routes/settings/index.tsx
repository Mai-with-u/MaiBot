import { useNavigate, useRouterState } from '@tanstack/react-router'
import { Info, Palette, Settings, Shield, SlidersHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { AboutTab } from './AboutTab'
import { AppearanceTab } from './AppearanceTab'
import { GeneralTab } from './GeneralTab'
import { OtherTab } from './OtherTab'
import { SecurityTab } from './SecurityTab'

type SettingsTab = 'general' | 'appearance' | 'security' | 'other' | 'about'
const SETTINGS_TABS: SettingsTab[] = ['general', 'appearance', 'security', 'other', 'about']

// 独立设置页，滚动由主内容区统一管理。
export function SettingsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const search = useRouterState({ select: (state) => state.location.searchStr })
  const hash = useRouterState({ select: (state) => state.location.hash })
  const searchTab = new URLSearchParams(search).get('tab') ?? hash.replace(/^#/, '')
  const activeTab: SettingsTab = SETTINGS_TABS.includes(searchTab as SettingsTab)
    ? (searchTab as SettingsTab)
    : 'general'

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(search)
    if (value === 'general') {
      params.delete('tab')
    } else {
      params.set('tab', value)
    }
    params.delete('mode')
    const nextSearch = params.size ? `?${params.toString()}` : ''
    void navigate({ href: `/settings${nextSearch}`, replace: true })
  }

  return (
    <div className="min-w-0 p-4 sm:p-6">
      {/* WebUI 设置标签页 */}
      <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full min-w-0">
        <div className="-mx-1 shrink-0 overflow-x-auto px-1 pb-1 sm:mx-0 sm:overflow-visible sm:p-0">
          <TabsList className="inline-grid h-auto w-max min-w-full grid-cols-5 gap-1 p-1 sm:w-full">
            <TabsTrigger
              data-dashboard-settings-tabs="true"
              value="general"
              className="min-w-[5.5rem] gap-1 px-3 text-sm sm:min-w-0 sm:gap-2 sm:text-base"
            >
              <SlidersHorizontal className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
              <span>{t('settings.tabs.general')}</span>
            </TabsTrigger>
            <TabsTrigger
              data-dashboard-settings-tabs="true"
              value="appearance"
              className="min-w-[5.5rem] gap-1 px-3 text-sm sm:min-w-0 sm:gap-2 sm:text-base"
            >
              <Palette className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
              <span>{t('settings.tabs.appearance')}</span>
            </TabsTrigger>
            <TabsTrigger
              data-dashboard-settings-tabs="true"
              value="security"
              className="min-w-[5.5rem] gap-1 px-3 text-sm sm:min-w-0 sm:gap-2 sm:text-base"
            >
              <Shield className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
              <span>{t('settings.tabs.security')}</span>
            </TabsTrigger>
            <TabsTrigger
              data-dashboard-settings-tabs="true"
              value="other"
              className="min-w-[5.5rem] gap-1 px-3 text-sm sm:min-w-0 sm:gap-2 sm:text-base"
            >
              <Settings className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
              <span>{t('settings.tabs.other')}</span>
            </TabsTrigger>
            <TabsTrigger
              data-dashboard-settings-tabs="true"
              value="about"
              className="min-w-[5.5rem] gap-1 px-3 text-sm sm:min-w-0 sm:gap-2 sm:text-base"
            >
              <Info className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
              <span>{t('settings.tabs.about')}</span>
            </TabsTrigger>
          </TabsList>
        </div>

        <div className="mt-4 pb-16 sm:mt-6 sm:pb-20">
          <TabsContent value="general" className="mt-0">
            <GeneralTab />
          </TabsContent>

          <TabsContent value="appearance" className="mt-0">
            <AppearanceTab />
          </TabsContent>

          <TabsContent value="security" className="mt-0">
            <SecurityTab />
          </TabsContent>

          <TabsContent value="other" className="mt-0">
            <OtherTab />
          </TabsContent>

          <TabsContent value="about" className="mt-0">
            <AboutTab />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  )
}
