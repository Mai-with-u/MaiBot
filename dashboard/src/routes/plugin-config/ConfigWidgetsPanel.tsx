import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { getConfigWidgets, invokeConfigWidget } from '@/lib/plugin-api/config'
import { cn } from '@/lib/utils'

/** 运行时数据独立于配置草稿；离开页面后不保留登录二维码。 */
export function ConfigWidgetsPanel({ pluginId }: { pluginId: string }) {
  const queryClient = useQueryClient()
  const queryKey = ['plugin-config-widgets', pluginId] as const
  const [open, setOpen] = useState(true)
  const [now, setNow] = useState(Date.now)
  const action = useMutation({
    mutationFn: (actionId: string) => invokeConfigWidget(pluginId, actionId),
    retry: false,
    gcTime: 0,
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey })
    },
    onSuccess: (snapshot) => {
      if (queryClient.getQueryCache().find({ queryKey })?.getObserversCount()) {
        queryClient.setQueryData(queryKey, snapshot)
      }
    },
  })
  const snapshot = useQuery({
    queryKey,
    queryFn: ({ signal }) => getConfigWidgets(pluginId, signal),
    enabled: !action.isPending,
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  })
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const error = action.error ?? snapshot.error
  const visibleWidgets = snapshot.isError ? [] : (snapshot.data?.widgets ?? []).filter(
    widget => snapshot.data!.states[widget.id].visible
  )
  const contentWidgets = visibleWidgets.filter(widget => widget.kind !== 'action')
  const actions = visibleWidgets.filter(widget => widget.kind === 'action')

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className="mb-4">
        <CardHeader className="space-y-0 px-4! py-1.5! sm:px-4! sm:py-1.5!">
          <CollapsibleTrigger asChild>
            <button type="button" className="flex w-full items-center gap-2 rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
              <CardTitle className="text-base">运行状态</CardTitle>
            </button>
          </CollapsibleTrigger>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className="space-y-3 px-4! pb-4! pt-0! sm:px-4! sm:pb-4! sm:pt-0!">
            {error && <Alert variant="destructive"><AlertDescription>{error.message}</AlertDescription></Alert>}
            {snapshot.isPending && <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />正在获取运行状态
            </div>}
            {contentWidgets.map(widget => {
              const state = snapshot.data!.states[widget.id]
              const expired = state.expires_at != null && now >= state.expires_at * 1000
              return (
                <div key={widget.id} className={widget.kind === 'status' ? 'flex flex-wrap items-center gap-x-3 gap-y-2' : 'space-y-2'}>
                  <span className="text-xs text-muted-foreground">{widget.label}</span>
                  {widget.kind === 'qrcode' && !expired && state.image &&
                    /^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(state.image) && (
                      <img src={state.image} alt={widget.label} className="h-auto w-48 max-w-full rounded-sm border bg-white p-2" />
                    )}
                  {widget.kind === 'status' ? (
                    <Badge variant="outline" role="status" className="max-w-full gap-2 px-2 py-1 font-normal">
                      <span aria-hidden="true" className={cn('h-1.5 w-1.5 shrink-0 rounded-full',
                        state.tone === 'error' ? 'bg-destructive' : state.tone === 'success' ? 'bg-emerald-500'
                          : state.tone === 'warning' ? 'bg-amber-500' : 'bg-muted-foreground')} />
                      <span className="break-words whitespace-pre-wrap">{expired ? '状态已过期，请刷新' : state.text}</span>
                    </Badge>
                  ) : <p role="status" className={cn('text-sm break-words whitespace-pre-wrap',
                    state.tone === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
                    {expired ? '二维码已过期，请刷新' : state.text}
                  </p>}
                </div>
              )
            })}
            {actions.length > 0 && <div role="group" aria-label="运行状态操作" className="flex flex-wrap items-center gap-2 border-t pt-3">
              {actions.map(widget => {
                const state = snapshot.data!.states[widget.id]
                const expired = state.expires_at != null && now >= state.expires_at * 1000
                return <Button key={widget.id} type="button" variant="outline" size="sm"
                  disabled={state.disabled || action.isPending || expired}
                  onClick={() => {
                    if (widget.confirmation && !window.confirm(widget.confirmation)) return
                    action.mutate(widget.id)
                  }}>
                  {action.isPending && action.variables === widget.id && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  {widget.label}
                </Button>
              })}
            </div>}
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}
