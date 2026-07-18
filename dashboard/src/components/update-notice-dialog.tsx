import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { ArrowRight, Check, CircleAlert, CircleCheck, CircleX } from 'lucide-react'

import { MarkdownRenderer } from '@/components/markdown-renderer'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { getSetting } from '@/lib/settings-manager'
import {
  ackUpdateNotice,
  getUpdateNotice,
  type IncompatiblePluginNotice,
  type UpdateNoticeResponse,
} from '@/lib/system-api'

type NoticeStage = 'update' | 'compatibility' | null

function getUpdateStatus(plugin: IncompatiblePluginNotice) {
  if (plugin.update_status === 'available') {
    return {
      icon: CircleCheck,
      label: `可更新至 v${plugin.update_version}`,
      className: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
    }
  }
  if (plugin.update_status === 'check_failed') {
    return {
      icon: CircleAlert,
      label: '兼容更新检查失败',
      className: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300',
    }
  }
  if (plugin.update_status === 'not_found') {
    return {
      icon: CircleX,
      label: '插件市场中未找到',
      className: 'border-border bg-muted text-muted-foreground',
    }
  }
  return {
    icon: CircleX,
    label: '暂无兼容更新',
    className: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
}

export function UpdateNoticeDialog() {
  const navigate = useNavigate()
  const alwaysShowUpdateNotice = getSetting('alwaysShowUpdateNotice')
  const [notice, setNotice] = useState<UpdateNoticeResponse | null>(null)
  const [stage, setStage] = useState<NoticeStage>(null)
  const ackedRef = useRef(false)

  useEffect(() => {
    let cancelled = false

    async function loadNotice() {
      try {
        const response = await getUpdateNotice(alwaysShowUpdateNotice)
        if (cancelled || !response.pending) {
          return
        }
        ackedRef.current = false
        setNotice(response)
        setStage('update')
      } catch (error) {
        console.error('[UpdateNotice] 获取更新公告失败:', error)
      }
    }

    void loadNotice()

    return () => {
      cancelled = true
    }
  }, [alwaysShowUpdateNotice])

  const acknowledgeNoticeSequence = useCallback(async () => {
    if (ackedRef.current) {
      setStage(null)
      return
    }

    ackedRef.current = true
    setStage(null)
    try {
      await ackUpdateNotice()
    } catch (error) {
      console.error('[UpdateNotice] 确认更新公告失败:', error)
    }
  }, [])

  const finishUpdateNotice = useCallback(() => {
    if (alwaysShowUpdateNotice || (notice?.incompatible_plugins?.length ?? 0) > 0) {
      setStage('compatibility')
      return
    }
    void acknowledgeNoticeSequence()
  }, [acknowledgeNoticeSequence, alwaysShowUpdateNotice, notice])

  const openPluginManagement = useCallback(async () => {
    await acknowledgeNoticeSequence()
    await navigate({ to: '/plugin-config' })
  }, [acknowledgeNoticeSequence, navigate])

  if (!notice) {
    return null
  }

  const incompatiblePlugins = notice.incompatible_plugins ?? []

  return (
    <>
      <Dialog
        open={stage === 'update'}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            finishUpdateNotice()
          }
        }}
      >
        <DialogContent style={{ '--dialog-width': '44rem' } as CSSProperties}>
          <DialogHeader>
            <DialogTitle>更新内容</DialogTitle>
            <DialogDescription>查看本次 MaiBot 更新包含的功能与修复。</DialogDescription>
          </DialogHeader>
          <DialogBody className="max-h-[min(70vh,42rem)]">
            <MarkdownRenderer content={notice.content} className="[&_h1:first-child]:mt-0" />
          </DialogBody>
          <DialogFooter>
            <Button type="button" onClick={finishUpdateNotice}>
              <Check className="h-4 w-4" />
              知道了
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={stage === 'compatibility'}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            void acknowledgeNoticeSequence()
          }
        }}
      >
        <DialogContent style={{ '--dialog-width': '42rem' } as CSSProperties}>
          <DialogHeader>
            <DialogTitle>插件兼容性提醒</DialogTitle>
            <DialogDescription>
              {incompatiblePlugins.length > 0
                ? `以下插件在 MaiBot 更新到 v${notice.current_version} 后不再兼容，请更新插件或暂时停用。`
                : `已完成 MaiBot v${notice.current_version} 的插件兼容性检查。`}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="max-h-[min(65vh,36rem)]">
            <div className="space-y-3 pr-1">
              {incompatiblePlugins.length === 0 && (
                <div className="rounded-lg border border-dashed bg-muted/30 p-4 text-sm text-muted-foreground">
                  当前版本未检测到因主程序更新而失去兼容性的已安装插件。
                </div>
              )}
              {incompatiblePlugins.map((plugin) => {
                const status = getUpdateStatus(plugin)
                const StatusIcon = status.icon
                return (
                  <div
                    key={plugin.plugin_id}
                    className="rounded-lg border bg-muted/30 p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{plugin.name}</span>
                      <span className="text-xs text-muted-foreground">
                        v{plugin.installed_version}
                      </span>
                      <Badge variant="outline" className={status.className}>
                        <StatusIcon className="mr-1 h-3.5 w-3.5" />
                        {status.label}
                      </Badge>
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">
                      {plugin.plugin_id} · 支持 MaiBot v{plugin.host_min_version} - v{plugin.host_max_version}
                    </p>
                  </div>
                )
              })}
            </div>
          </DialogBody>
          <DialogFooter>
            {incompatiblePlugins.length > 0 ? (
              <>
                <Button type="button" variant="outline" onClick={() => void acknowledgeNoticeSequence()}>
                  稍后处理
                </Button>
                <Button type="button" onClick={() => void openPluginManagement()}>
                  前往插件管理
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </>
            ) : (
              <Button type="button" onClick={() => void acknowledgeNoticeSequence()}>
                <Check className="h-4 w-4" />
                知道了
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
