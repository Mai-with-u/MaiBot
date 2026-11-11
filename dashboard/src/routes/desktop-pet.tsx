import { useQuery } from '@tanstack/react-query'
import { Navigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { Board, BoardModule, BoardScreen } from '@/components/ui/board'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { desktopPetStatusQueryOptions } from '@/lib/desktop-pet-api'

import { DesktopPetSettings } from './desktop-pet/DesktopPetSettings'
import { DesktopPetCardActions, DesktopPetCardBody } from './observation/DesktopPetCard'

export function DesktopPetPage() {
  const { t } = useTranslation()
  const statusQuery = useQuery({ ...desktopPetStatusQueryOptions, refetchOnMount: 'always' })
  const status = statusQuery.data
  const refresh = () => void statusQuery.refetch()

  if (statusQuery.isFetchedAfterMount && !statusQuery.isError && status?.enabled === false) {
    return <Navigate to="/config/bot" replace />
  }

  return (
    <ScrollArea
      className="h-full max-lg:h-auto max-lg:overflow-visible max-lg:[&>[data-dashboard-scrollbar]]:hidden"
      viewportClassName="max-lg:h-auto max-lg:!overflow-visible max-lg:overscroll-auto"
      scrollbars="vertical"
    >
      <div className="mx-auto w-full max-w-6xl space-y-4 p-4 sm:p-6">
        <h1 className="text-2xl font-semibold">{t('observation.pet.statusTitle')}</h1>
        <Board>
          <BoardModule
            code="PET"
            title={t('observation.pet.title')}
            actions={<DesktopPetCardActions />}
            span={12}
            className="min-h-[22rem]"
          >
            {statusQuery.isError && (
              <div role="alert" className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                <p className="text-destructive text-sm">
                  {t('observation.pet.statusLoadFailed', { error: statusQuery.error.message })}
                </p>
                <Button type="button" size="sm" variant="outline" onClick={refresh} disabled={statusQuery.isFetching}>
                  {t('common.retry')}
                </Button>
              </div>
            )}
            {status?.enabled && <DesktopPetCardBody status={status} onChanged={refresh} />}
            {!status?.enabled && !statusQuery.isError && (
              <BoardScreen className="items-center justify-center">
                <p role="status" className="text-muted-foreground text-sm">{t('common.loading')}</p>
              </BoardScreen>
            )}
          </BoardModule>
          <DesktopPetSettings onSaved={refresh} />
        </Board>
      </div>
    </ScrollArea>
  )
}
