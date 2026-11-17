import { useNavigate, useRouterState } from '@tanstack/react-router'
import { Network, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { DashboardFolderPanel, DashboardFolderTab, DashboardFolderTabBar } from '@/components/ui/dashboard-tabs'
import { Tabs, TabsContent } from '@/components/ui/tabs'

import { ChatManagementPage } from './chat-management'
import { PersonManagementPage } from './person'

export function ContactsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const tab = pathname === '/chat-management' ? 'sharing' : 'persons'

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => {
        void navigate({ to: value === 'sharing' ? '/chat-management' : '/resource/person' })
      }}
      className="flex h-full min-h-0 flex-col p-3 sm:p-4"
    >
      <DashboardFolderTabBar size="large" aria-label={t('sidebar.menu.contacts')}>
        <DashboardFolderTab value="persons" icon={<Users />}>
          {t('sidebar.menu.contactPeople')}
        </DashboardFolderTab>
        <DashboardFolderTab value="sharing" icon={<Network />}>
          {t('sidebar.menu.mutualGroups')}
        </DashboardFolderTab>
      </DashboardFolderTabBar>
      <TabsContent value="persons" className="mt-0 min-h-0 flex-1">
        <DashboardFolderPanel className="h-full min-h-0 overflow-hidden">
          <PersonManagementPage />
        </DashboardFolderPanel>
      </TabsContent>
      <TabsContent value="sharing" className="mt-0 min-h-0 flex-1">
        <DashboardFolderPanel className="h-full min-h-0 overflow-hidden">
          <ChatManagementPage />
        </DashboardFolderPanel>
      </TabsContent>
    </Tabs>
  )
}
