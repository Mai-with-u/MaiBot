import { useNavigate, useSearch } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { ExpressionManagementPage } from '../expression'
import { JargonManagementPage } from '../jargon'

export function LearningPage() {
  const { t } = useTranslation()
  const { tab } = useSearch({ from: '/protected/resource/learning' })
  const navigate = useNavigate()
  const browserHeader = (
    <TabsList aria-label={t('sidebar.menu.learning')} className="h-11 w-full shrink-0">
      <TabsTrigger value="expression" className="min-w-0 flex-1 px-1 text-xs">
        {t('sidebar.menu.expressionManagement')}
      </TabsTrigger>
      <TabsTrigger value="jargon" className="min-w-0 flex-1 px-1 text-xs">
        {t('sidebar.menu.slangManagement')}
      </TabsTrigger>
    </TabsList>
  )

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => {
        void navigate({
          to: '/resource/learning',
          search: { tab: value === 'jargon' ? 'jargon' : 'expression' },
        })
      }}
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      <TabsContent value="expression" className="mt-0 min-h-0 flex-1 overflow-hidden">
        <ExpressionManagementPage browserHeader={browserHeader} />
      </TabsContent>
      <TabsContent value="jargon" className="mt-0 min-h-0 flex-1 overflow-hidden">
        <JargonManagementPage browserHeader={browserHeader} />
      </TabsContent>
    </Tabs>
  )
}
