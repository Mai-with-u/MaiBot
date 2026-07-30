import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react'
import { useNavigate } from '@tanstack/react-router'
import { ArrowRight, Check, ChevronDown, CircleAlert, CircleCheck, CircleX } from 'lucide-react'
import { motion } from 'motion/react'

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
  getUpdateHistory,
  getUpdateNotice,
  type IncompatiblePluginNotice,
  type UpdateHistoryEntry,
  type UpdateNoticeResponse,
} from '@/lib/system-api'
import { UPDATE_NOTICE_OPEN_EVENT, type UpdateNoticeTarget } from '@/lib/update-notice-events'

import {
  extractWebuiSections,
  extractWebuiVersion,
  removeTopLevelHeadings,
  removeWebuiVersions,
} from './update-notice-markdown'

type NoticeStage = 'update' | 'compatibility' | null

const MarkdownRenderer = lazy(() =>
  import('@/components/markdown-renderer').then((module) => ({
    default: module.MarkdownRenderer,
  }))
)

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
  const [manualTarget, setManualTarget] = useState<UpdateNoticeTarget | null>(null)
  const [manualLoading, setManualLoading] = useState(false)
  const [historyEntries, setHistoryEntries] = useState<UpdateHistoryEntry[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyHasMore, setHistoryHasMore] = useState(true)
  const [noticeBodyHeight, setNoticeBodyHeight] = useState<number>()
  const [activeManualVersion, setActiveManualVersion] = useState<string | null>(null)
  const ackedRef = useRef(false)
  const historyRequestedRef = useRef(false)
  const noticeContentRef = useRef<HTMLDivElement>(null)
  const noticeViewportRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false

    async function loadNotice() {
      try {
        const response = await getUpdateNotice(alwaysShowUpdateNotice)
        if (cancelled || !response.pending) {
          return
        }
        ackedRef.current = false
        setManualTarget(null)
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

  useEffect(() => {
    let cancelled = false

    const handleManualOpen = (event: Event) => {
      const target = (event as CustomEvent<UpdateNoticeTarget>).detail
      setManualTarget(target)
      setManualLoading(true)
      setHistoryEntries([])
      setHistoryLoading(false)
      setHistoryLoaded(false)
      setHistoryOffset(0)
      setHistoryHasMore(true)
      setNoticeBodyHeight(undefined)
      setActiveManualVersion(null)
      historyRequestedRef.current = false
      setStage('update')

      void getUpdateNotice(true)
        .then((response) => {
          if (!cancelled) {
            setNotice(response)
          }
        })
        .catch((error) => {
          console.error('[UpdateNotice] 手动获取更新公告失败:', error)
        })
        .finally(() => {
          if (!cancelled) {
            setManualLoading(false)
          }
        })
    }

    window.addEventListener(UPDATE_NOTICE_OPEN_EVENT, handleManualOpen)
    return () => {
      cancelled = true
      window.removeEventListener(UPDATE_NOTICE_OPEN_EVENT, handleManualOpen)
    }
  }, [])

  const revealHistory = useCallback(async () => {
    if (
      !manualTarget ||
      historyRequestedRef.current ||
      historyLoading ||
      (historyLoaded && !historyHasMore)
    ) {
      return
    }

    historyRequestedRef.current = true
    setHistoryLoading(true)
    try {
      const displayedVersions = notice?.versions ?? []
      const oldestDisplayedVersion = displayedVersions[displayedVersions.length - 1]
      const response = await getUpdateHistory(
        historyOffset,
        3,
        oldestDisplayedVersion,
        manualTarget === 'console' ? 'webui' : undefined
      )
      const historicalEntries = response.entries
      const scopedEntries =
        manualTarget === 'console'
          ? historicalEntries.filter((entry) => extractWebuiSections(entry.content))
          : historicalEntries
      setHistoryEntries((currentEntries) => [
        ...currentEntries,
        ...scopedEntries.filter(
          (entry) => !currentEntries.some((currentEntry) => currentEntry.version === entry.version)
        ),
      ])
      setHistoryOffset(response.next_offset)
      setHistoryHasMore(response.has_more)
      setHistoryLoaded(true)
    } catch (error) {
      console.error('[UpdateNotice] 获取历史更新记录失败:', error)
    } finally {
      historyRequestedRef.current = false
      setHistoryLoading(false)
    }
  }, [
    historyHasMore,
    historyLoaded,
    historyLoading,
    historyOffset,
    manualTarget,
    notice?.versions,
  ])

  const shouldLoadMoreHistory = useCallback(() => {
    const viewport = noticeViewportRef.current
    if (!viewport) {
      return !historyLoaded
    }
    return (
      !historyLoaded ||
      viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 96
    )
  }, [historyLoaded])

  const syncActiveManualVersion = useCallback(() => {
    const viewport = noticeViewportRef.current
    if (!viewport || !manualTarget) {
      return
    }

    const versionSections = Array.from(
      viewport.querySelectorAll<HTMLElement>('[data-update-notice-version]')
    )
    if (versionSections.length === 0) {
      return
    }

    const viewportTop = viewport.getBoundingClientRect().top
    let activeVersion = versionSections[0].dataset.updateNoticeVersion
    for (const section of versionSections) {
      if (section.getBoundingClientRect().top > viewportTop + 1) {
        break
      }
      activeVersion = section.dataset.updateNoticeVersion
    }
    if (activeVersion) {
      setActiveManualVersion((current) => (current === activeVersion ? current : activeVersion))
    }
  }, [manualTarget])

  useEffect(() => {
    const viewport = noticeViewportRef.current
    if (!viewport || !manualTarget || stage !== 'update') {
      return
    }

    const handleScroll = () => {
      syncActiveManualVersion()
      if (viewport.scrollTop > 0 && shouldLoadMoreHistory()) {
        void revealHistory()
      }
    }
    viewport.addEventListener('scroll', handleScroll, { passive: true })
    return () => viewport.removeEventListener('scroll', handleScroll)
  }, [manualTarget, revealHistory, shouldLoadMoreHistory, stage, syncActiveManualVersion])

  useEffect(() => {
    const content = noticeContentRef.current
    if (!content || stage !== 'update' || manualLoading) {
      return
    }

    const updateBodyHeight = () => {
      const rootFontSize = Number.parseFloat(
        window.getComputedStyle(document.documentElement).fontSize
      )
      const maxBodyHeight = Math.min(window.innerHeight * 0.7, rootFontSize * 42)
      setNoticeBodyHeight(Math.min(Math.ceil(content.getBoundingClientRect().height), maxBodyHeight))
    }
    const resizeObserver = new ResizeObserver(updateBodyHeight)
    resizeObserver.observe(content)
    updateBodyHeight()

    return () => resizeObserver.disconnect()
  }, [manualLoading, notice, stage])

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
    if (manualTarget) {
      setStage(null)
      setManualTarget(null)
      setActiveManualVersion(null)
      return
    }
    if (alwaysShowUpdateNotice || (notice?.incompatible_plugins?.length ?? 0) > 0) {
      setStage('compatibility')
      return
    }
    void acknowledgeNoticeSequence()
  }, [acknowledgeNoticeSequence, alwaysShowUpdateNotice, manualTarget, notice])

  const openPluginManagement = useCallback(async () => {
    await acknowledgeNoticeSequence()
    await navigate({ to: '/plugin-config' })
  }, [acknowledgeNoticeSequence, navigate])

  if (!notice && !manualLoading) {
    return null
  }

  const incompatiblePlugins = notice?.incompatible_plugins ?? []
  const currentContent =
    manualTarget === 'console' && notice
      ? extractWebuiSections(notice.content) || '当前版本没有 WebUI 子项目的更新记录。'
      : notice?.content
  const displayedCurrentContent =
    manualTarget && currentContent ? removeTopLevelHeadings(currentContent) : currentContent
  const currentWebuiVersion =
    manualTarget === 'console' && notice ? extractWebuiVersion(notice.content) : null
  const currentManualVersion =
    manualTarget === 'console' ? (currentWebuiVersion ?? '—') : (notice?.current_version ?? '—')
  const renderedCurrentContent =
    manualTarget === 'maibot' && displayedCurrentContent
      ? removeWebuiVersions(displayedCurrentContent)
      : displayedCurrentContent

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
            {manualTarget ? (
              <DialogTitle className="flex flex-col items-start gap-2">
                <span
                  className="text-2xl leading-tight font-bold tracking-wide"
                  style={{ color: 'var(--retro-ink)' }}
                >
                  {manualTarget === 'console' ? 'CONSOLE 版本' : 'MAIBOT 版本'}
                </span>
                <span
                  className="text-4xl leading-none font-black tracking-tight"
                  style={{ color: 'var(--retro-rust)' }}
                >
                  {activeManualVersion ?? currentManualVersion}
                </span>
              </DialogTitle>
            ) : (
              <DialogTitle>更新内容</DialogTitle>
            )}
            {!manualTarget && (
              <DialogDescription>
                查看本次 MaiBot 更新包含的功能与修复。
              </DialogDescription>
            )}
          </DialogHeader>
          <DialogBody
            className="max-h-[min(70vh,42rem)] flex-none"
            viewportRef={noticeViewportRef}
            style={
              noticeBodyHeight === undefined
                ? undefined
                : {
                    height: noticeBodyHeight,
                    transition: 'height 480ms cubic-bezier(0.22, 1, 0.36, 1)',
                  }
            }
            onWheelCapture={(event) => {
              if (event.deltaY > 0 && shouldLoadMoreHistory()) {
                void revealHistory()
              }
            }}
          >
            {manualLoading || !notice ? (
              <div className="text-muted-foreground py-8 text-center text-sm">
                正在加载更新内容…
              </div>
            ) : (
              <Suspense
                fallback={
                  <div className="text-muted-foreground py-8 text-center text-sm">
                    正在加载更新内容…
                  </div>
                }
              >
                <div ref={noticeContentRef}>
                  <div data-update-notice-version={currentManualVersion}>
                    <MarkdownRenderer
                      content={renderedCurrentContent ?? ''}
                      className="[&_h1:first-child]:mt-0 [&_h2:first-child]:mt-0 [&_h3:first-child]:mt-0"
                    />
                  </div>
                  {manualTarget && !historyLoaded && !historyLoading && (
                    <motion.div
                      aria-hidden="true"
                      className="text-primary/60 pointer-events-none sticky bottom-1 flex h-8 items-center justify-center"
                      animate={{ y: [0, 5, 0], opacity: [0.55, 0.9, 0.55] }}
                      transition={{
                        duration: 1.6,
                        ease: 'easeInOut',
                        repeat: Infinity,
                      }}
                    >
                      <ChevronDown className="h-5 w-5" strokeWidth={2} />
                    </motion.div>
                  )}
                  {historyLoading && !historyLoaded && (
                    <div className="text-muted-foreground border-t py-5 text-center text-sm">
                      正在加载历史版本…
                    </div>
                  )}
                  {historyLoaded && historyEntries.length > 0 && (
                    <section className="mt-8 border-t pt-6">
                      <h2 className="mb-5 text-base font-semibold">历史版本</h2>
                      <div className="divide-border divide-y">
                        {historyEntries.map((entry, index) => (
                          <motion.article
                            key={entry.version}
                            data-update-notice-version={
                              manualTarget === 'console'
                                ? (extractWebuiVersion(entry.content) ?? '—')
                                : entry.version
                            }
                            layout="position"
                            initial={{ opacity: 0, y: 24, filter: 'blur(4px)' }}
                            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                            transition={{
                              duration: 0.45,
                              delay: (index % 3) * 0.08,
                              ease: [0.22, 1, 0.36, 1],
                            }}
                            className="py-8 first:pt-0 last:pb-0"
                          >
                            <h3 className="mb-4 text-2xl font-bold tracking-tight">
                              v
                              {manualTarget === 'console'
                                ? (extractWebuiVersion(entry.content) ?? '—')
                                : entry.version}
                            </h3>
                            <MarkdownRenderer
                              content={
                                manualTarget === 'console'
                                  ? extractWebuiSections(entry.content)
                                  : removeWebuiVersions(entry.content)
                              }
                              className="[&_h1]:hidden [&_h2]:!text-xl [&_h3]:!text-lg [&_h2:first-child]:!mt-0 [&_h3:first-child]:!mt-0"
                            />
                          </motion.article>
                        ))}
                      </div>
                      {historyLoading && (
                        <div className="text-muted-foreground border-t py-5 text-center text-sm">
                          正在加载更多历史版本…
                        </div>
                      )}
                    </section>
                  )}
                </div>
              </Suspense>
            )}
          </DialogBody>
          {!manualTarget && (
            <DialogFooter>
              <Button type="button" disabled={manualLoading} onClick={finishUpdateNotice}>
                <Check className="h-4 w-4" />
                知道了
              </Button>
            </DialogFooter>
          )}
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
                ? `以下插件在 MaiBot 更新到 v${notice?.current_version ?? '—'} 后不再兼容，请更新插件或暂时停用。`
                : `已完成 MaiBot v${notice?.current_version ?? '—'} 的插件兼容性检查。`}
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
