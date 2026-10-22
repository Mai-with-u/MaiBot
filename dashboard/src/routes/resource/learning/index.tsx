import { useNavigate, useSearch } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMediaQuery } from '@/hooks/use-media-query'

import { EmojiManagementPage } from '../emoji'
import { ExpressionManagementPage } from '../expression'
import { JargonManagementPage } from '../jargon'

export function LearningPage() {
  const { t } = useTranslation()
  const { tab } = useSearch({ from: '/protected/resource/learning' })
  const navigate = useNavigate()
  const isMobile = useMediaQuery('(max-width: 1023.98px)')
  const browserHeader = (
    <TabsList aria-label={t('sidebar.menu.learning')} className="w-auto shrink-0">
      <TabsTrigger value="expression" size="sm" className="min-w-0 px-3 text-xs">
        {t('sidebar.menu.expressionManagement')}
      </TabsTrigger>
      <TabsTrigger value="jargon" size="sm" className="min-w-0 px-3 text-xs">
        {t('sidebar.menu.slangManagement')}
      </TabsTrigger>
      <TabsTrigger value="emoji" size="sm" className="min-w-0 px-3 text-xs">
        {t('sidebar.menu.emojiManagement')}
      </TabsTrigger>
    </TabsList>
  )

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => {
        if (value !== 'expression' && value !== 'jargon' && value !== 'emoji') {
          throw new Error(`未知的学习页签: ${value}`)
        }
        void navigate({
          to: '/resource/learning',
          search: { tab: value },
        })
      }}
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      {/* 手机页签独立于各业务页头，审核子视图和表情包网格中也能切换。 */}
      {isMobile && (
        <div className="shrink-0 overflow-x-auto px-3 pt-3 sm:px-4">{browserHeader}</div>
      )}
      <TabsContent value="expression" className="mt-0 min-h-0 flex-1 overflow-hidden">
        <ExpressionManagementPage browserHeader={isMobile ? undefined : browserHeader} />
      </TabsContent>
      <TabsContent value="jargon" className="mt-0 min-h-0 flex-1 overflow-hidden">
        <JargonManagementPage browserHeader={isMobile ? undefined : browserHeader} />
      </TabsContent>
      <TabsContent value="emoji" className="mt-0 min-h-0 flex-1 overflow-hidden max-lg:overflow-y-auto">
        <EmojiManagementPage browserHeader={isMobile ? undefined : browserHeader} />
      </TabsContent>
    </Tabs>
  )
}
