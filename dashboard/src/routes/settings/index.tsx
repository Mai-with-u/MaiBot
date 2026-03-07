import { Info, Palette, Settings, Shield } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { AboutTab } from './AboutTab'
import { AppearanceTab } from './AppearanceTab'
import { OtherTab } from './OtherTab'
import { SecurityTab } from './SecurityTab'

export function SettingsPage() {
  const { t } = useTranslation()
  return (
    <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">{t('settings.title')}</h1>
          <p className="text-muted-foreground mt-1 sm:mt-2 text-sm sm:text-base">{t('settings.description')}</p>
        </div>
      </div>

      {/* 标签页 */}
      <Tabs defaultValue="appearance" className="w-full">
        <TabsList className="grid w-full grid-cols-2 sm:grid-cols-4 gap-0.5 sm:gap-1 h-auto p-1">
          <TabsTrigger value="appearance" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <Palette className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>{t('settings.tabs.appearance')}</span>
          </TabsTrigger>
          <TabsTrigger value="security" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <Shield className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>{t('settings.tabs.security')}</span>
          </TabsTrigger>
          <TabsTrigger value="other" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <Settings className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>{t('settings.tabs.other')}</span>
          </TabsTrigger>
          <TabsTrigger value="about" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <Info className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>{t('settings.tabs.about')}</span>
          </TabsTrigger>
        </TabsList>

        <ScrollArea className="h-[calc(100vh-240px)] sm:h-[calc(100vh-280px)] mt-4 sm:mt-6">
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
        </ScrollArea>
      </Tabs>
    </div>
  )
}
