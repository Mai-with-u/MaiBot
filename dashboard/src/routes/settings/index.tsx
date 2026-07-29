import { Info, Palette, Settings, Shield } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { AboutTab } from './AboutTab'
import { AppearanceTab } from './AppearanceTab'
import { OtherTab } from './OtherTab'
import { SecurityTab } from './SecurityTab'

type SettingsTab = 'appearance' | 'security' | 'other' | 'about'
const SETTINGS_TABS: SettingsTab[] = ['appearance', 'security', 'other', 'about']
const TITLE_COLLAPSE_SCROLL_TOP = 64
const TITLE_EXPAND_SCROLL_TOP = 4

function getInitialSettingsTab(): SettingsTab {
  const fallback: SettingsTab = 'appearance'
  if (typeof window === 'undefined') {
    return fallback
  }

  const searchTab = new URLSearchParams(window.location.search).get('tab')
  const hashTab = window.location.hash.replace(/^#/, '')
  const targetTab = searchTab || hashTab
  return SETTINGS_TABS.includes(targetTab as SettingsTab) ? (targetTab as SettingsTab) : fallback
}

export function SettingsPage() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<SettingsTab>(getInitialSettingsTab)
  const [isTitleCollapsed, setIsTitleCollapsed] = useState(false)
  const titleCollapsedRef = useRef(false)
  const scrollViewportRef = useRef<HTMLDivElement | null>(null)
  const scrollCorrectionFrameRef = useRef<number | null>(null)

  const handleTabChange = (value: string) => {
    const nextTab = SETTINGS_TABS.includes(value as SettingsTab) ? (value as SettingsTab) : 'appearance'
    setActiveTab(nextTab)
    const nextUrl = nextTab === 'appearance' ? '/settings' : `/settings?tab=${nextTab}`
    window.history.replaceState(null, '', nextUrl)
    scrollViewportRef.current?.scrollTo({ top: 0 })
    titleCollapsedRef.current = false
    setIsTitleCollapsed(false)
  }

  useEffect(() => {
    const viewport = scrollViewportRef.current
    if (!viewport) {
      return
    }

    const handleScroll = () => {
      const shouldCollapse = titleCollapsedRef.current
        ? viewport.scrollTop > TITLE_EXPAND_SCROLL_TOP
        : viewport.scrollTop > TITLE_COLLAPSE_SCROLL_TOP

      if (shouldCollapse === titleCollapsedRef.current) {
        return
      }

      const scrollTopBeforeLayout = viewport.scrollTop
      const viewportHeightBeforeLayout = viewport.clientHeight
      titleCollapsedRef.current = shouldCollapse
      setIsTitleCollapsed(shouldCollapse)

      if (scrollCorrectionFrameRef.current !== null) {
        cancelAnimationFrame(scrollCorrectionFrameRef.current)
      }

      scrollCorrectionFrameRef.current = requestAnimationFrame(() => {
        scrollCorrectionFrameRef.current = null
        const viewportHeightDelta = viewport.clientHeight - viewportHeightBeforeLayout
        if (viewportHeightDelta === 0) {
          return
        }

        viewport.scrollTop = Math.max(0, scrollTopBeforeLayout + viewportHeightDelta)
      })
    }

    handleScroll()
    viewport.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      viewport.removeEventListener('scroll', handleScroll)
      if (scrollCorrectionFrameRef.current !== null) {
        cancelAnimationFrame(scrollCorrectionFrameRef.current)
      }
    }
  }, [])

  return (
    <div className="flex h-full min-h-0 flex-col p-4 sm:p-6">
      {/* 页面标题 */}
      <div
        className={`flex shrink-0 flex-col justify-between gap-4 overflow-hidden pt-1 transition-[max-height,opacity] duration-200 sm:flex-row sm:items-center ${
          isTitleCollapsed ? 'max-h-0 opacity-0' : 'max-h-24 opacity-100'
        }`}
      >
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">{t('settings.title')}</h1>
        </div>
      </div>

      {/* 标签页 */}
      <Tabs
        value={activeTab}
        onValueChange={handleTabChange}
        className={`flex min-h-0 w-full flex-1 flex-col transition-[margin] duration-200 ${
          isTitleCollapsed ? 'mt-0' : 'mt-4 sm:mt-6'
        }`}
      >
        <div className="-mx-1 shrink-0 overflow-x-auto px-1 pb-1 sm:mx-0 sm:overflow-visible sm:p-0">
          <TabsList
            className="inline-grid h-auto w-max min-w-full grid-cols-4 gap-1 p-1 sm:w-full"
          >
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

        <ScrollArea
          viewportRef={scrollViewportRef}
          contentClassName="pb-16 sm:pb-20"
          className="mt-4 min-h-0 flex-1 sm:mt-6"
        >
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
