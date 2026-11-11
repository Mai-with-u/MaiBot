import { useHostRuntime } from '@/host/state'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { DataTransferWorkspace } from '@/routes/data-transfer/DataTransferWorkspace'
import { useDataTransfer } from '@/routes/data-transfer/useDataTransfer'
import { ChatMigrationWorkspace } from '@/routes/data-transfer/ChatMigrationWorkspace'
import { FilesPage } from '@/routes/files'
import { LocalCacheTab } from '@/routes/settings/LocalCacheTab'

export function DataTransferPage() {
  const runtime = useHostRuntime()
  return <BotDataTransferPage key={runtime.selected_bot_id} />
}

function BotDataTransferPage() {
  const { t } = useTranslation()
  const { tab = 'transfer' } = useSearch({ from: '/protected/data-transfer' })
  const navigate = useNavigate()
  const { flags } = useFeatureFlags()
  const viewModel = useDataTransfer()
  const pageTabs = (
    <TabsList aria-label={t('sidebar.menu.dataTransfer')} className="shrink-0">
      <TabsTrigger value="transfer">{t('dataManagement.transfer')}</TabsTrigger>
      <TabsTrigger value="chat-migration">聊天迁移</TabsTrigger>
      {(flags?.files || tab === 'files') && (
        <TabsTrigger value="files">{t('files.title')}</TabsTrigger>
      )}
    </TabsList>
  )
  return (
    // 手机端让各子页面内容自然展开，交给工作区统一滚动。
    <ScrollArea
      className="h-full max-lg:h-auto max-lg:overflow-visible max-lg:[&>[data-dashboard-scrollbar]]:hidden"
      viewportClassName="max-lg:h-auto max-lg:!overflow-visible max-lg:overscroll-auto"
      scrollbars="vertical"
    >
      <div className="mx-auto w-full max-w-6xl space-y-6 p-4 sm:p-6">
        <Tabs
          value={tab}
          onValueChange={(value) => {
            if (value !== 'transfer' && value !== 'files' && value !== 'chat-migration') return
            void navigate({ to: '/data-transfer', search: { tab: value === 'transfer' ? undefined : value } })
          }}
          className="min-w-0"
        >
          <TabsContent value="transfer" forceMount className="mt-0 space-y-6 data-[state=inactive]:hidden">
            <DataTransferWorkspace viewModel={viewModel} headerActions={tab === 'transfer' ? pageTabs : undefined} />
            <Separator variant="groove" />
            <details id="local-cache" className="group">
              <summary className="cursor-pointer py-2 text-sm font-medium">本地缓存管理</summary>
              <div className="pt-4">
                <LocalCacheTab />
              </div>
            </details>
          </TabsContent>
          <TabsContent value="chat-migration" className="mt-0">
            <ChatMigrationWorkspace headerActions={pageTabs} />
          </TabsContent>
          <TabsContent value="files" className="mt-0">
            <FilesPage headerActions={pageTabs} />
          </TabsContent>
        </Tabs>
      </div>
    </ScrollArea>
  )
}
