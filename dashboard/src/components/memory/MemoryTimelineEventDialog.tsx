import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
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
import {
  getMemoryRecordContext,
  type MemoryTimelineEventPayload,
  type MemoryTimelineJumpTargetPayload,
} from '@/lib/memory-api'

interface MemoryTimelineEventDialogProps {
  event: MemoryTimelineEventPayload | null
  categoryLabel: string
  timeLabel: string
  onClose: () => void
  onJump: (target: MemoryTimelineJumpTargetPayload) => void
}

export function MemoryTimelineEventDialog({
  event,
  categoryLabel,
  timeLabel,
  onClose,
  onJump,
}: MemoryTimelineEventDialogProps) {
  const relationId = event?.category === 'maintenance' ? event.key_id : undefined
  const contextQuery = useQuery({
    queryKey: ['memory-record-context', 'relation', relationId],
    queryFn: () => getMemoryRecordContext('relation', relationId!),
    enabled: Boolean(relationId),
  })
  const context = contextQuery.data
  const reason = event?.metadata?.reason
  const profileChanges = event?.metadata?.profile_changes as
    | { added?: unknown; removed?: unknown }
    | undefined
  const added = Array.isArray(profileChanges?.added)
    ? profileChanges.added.filter((line): line is string => typeof line === 'string')
    : []
  const removed = Array.isArray(profileChanges?.removed)
    ? profileChanges.removed.filter((line): line is string => typeof line === 'string')
    : []
  const profileText = event?.metadata?.profile_text
  const profileVersion = event?.metadata?.profile_version
  const previousVersion = event?.metadata?.previous_version
  const timeNote = event?.metadata?.time_note

  return (
    <Dialog
      open={event !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="max-h-[85vh] [--dialog-width:56rem]">
        <DialogHeader>
          <DialogTitle>{event?.title.replace(/\bEpisode\b/g, '记忆片')}详情</DialogTitle>
          <DialogDescription className="sr-only">
            查看事件时间、触发原因与关联证据。
          </DialogDescription>
        </DialogHeader>
        {event && (
          <>
            <DialogBody contentClassName="space-y-4">
              <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
                <Badge variant="outline">{categoryLabel}</Badge>
                <span>{timeLabel}</span>
                <span>{event.chat_name}</span>
              </div>
              {typeof timeNote === 'string' && (
                <p className="text-muted-foreground text-xs">{timeNote}</p>
              )}
              <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                {event.summary}
              </p>
              {typeof reason === 'string' && reason ? (
                <p className="text-muted-foreground text-sm">触发原因：{reason}</p>
              ) : null}
              {event.category === 'profile' && typeof profileVersion === 'number' && (
                <section className="space-y-3">
                  <div className="text-sm font-medium">
                    {typeof previousVersion === 'number'
                      ? `版本 ${previousVersion} → ${profileVersion}`
                      : `版本 ${profileVersion}`}
                  </div>
                  {(added.length > 0 || removed.length > 0) && (
                    <div
                      data-memory-timeline-evidence="true"
                      className="divide-border bg-background divide-y border"
                    >
                      {[
                        { label: '新增内容', lines: added },
                        { label: '移除内容', lines: removed },
                      ]
                        .filter((group) => group.lines.length > 0)
                        .map((group) => (
                          <div key={group.label} className="space-y-2 p-3">
                            <div className="text-sm font-medium">
                              {group.label}（{group.lines.length}）
                            </div>
                            <ul className="list-disc space-y-1 pl-4 text-sm">
                              {group.lines.map((line) => (
                                <li key={line} className="break-words">
                                  {line}
                                </li>
                              ))}
                            </ul>
                          </div>
                        ))}
                    </div>
                  )}
                  {typeof profileText === 'string' && (
                    <section className="space-y-2">
                      <div className="text-sm font-medium">该版本画像正文</div>
                      <div
                        data-memory-timeline-evidence="true"
                        className="bg-background border p-3 text-sm leading-relaxed break-words whitespace-pre-wrap"
                      >
                        {profileText}
                      </div>
                    </section>
                  )}
                </section>
              )}
              <dl className="grid gap-x-3 gap-y-2 text-xs sm:grid-cols-[auto_minmax(0,1fr)]">
                <dt className="text-muted-foreground">
                  {event.category === 'profile' ? '关联证据' : '影响对象'}
                </dt>
                <dd>{event.object_count}</dd>
                {event.key_id && (
                  <>
                    <dt className="text-muted-foreground">关键 ID</dt>
                    <dd className="font-mono break-all">{event.key_id}</dd>
                  </>
                )}
                {event.source && (
                  <>
                    <dt className="text-muted-foreground">来源</dt>
                    <dd className="font-mono break-all">{event.source}</dd>
                  </>
                )}
                {event.attribution && (
                  <>
                    <dt className="text-muted-foreground">归属依据</dt>
                    <dd className="font-mono break-all">{event.attribution}</dd>
                  </>
                )}
              </dl>
              {contextQuery.isLoading && (
                <div className="text-muted-foreground flex items-center gap-2 py-4 text-sm">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  读取关系与证据
                </div>
              )}
              {contextQuery.error && (
                <Alert variant="destructive">
                  <AlertDescription>{contextQuery.error.message}</AlertDescription>
                </Alert>
              )}
              {context && (
                <>
                  <section className="space-y-2">
                    <div className="text-muted-foreground text-xs font-medium">当前关系</div>
                    <p className="text-sm break-words">{context.record.title}</p>
                    {typeof context.record.metadata.confidence === 'number' && (
                      <span className="text-muted-foreground text-xs">
                        置信度 {context.record.metadata.confidence.toFixed(2)}
                      </span>
                    )}
                  </section>
                  {context.related.paragraphs.length > 0 && (
                    <section className="space-y-2">
                      <div className="text-muted-foreground text-xs font-medium">证据段落</div>
                      <div
                        data-memory-timeline-evidence="true"
                        className="divide-border bg-background divide-y border"
                      >
                        {context.related.paragraphs.map((paragraph) => (
                          <div key={paragraph.id} className="space-y-2 p-3">
                            <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                              {paragraph.summary}
                            </p>
                            {paragraph.source_label && (
                              <span className="text-muted-foreground text-xs">
                                来自 {paragraph.source_label}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}
                </>
              )}
            </DialogBody>
            <DialogFooter className="gap-2">
              <Button type="button" variant="outline" onClick={onClose}>
                关闭
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  onClose()
                  onJump(event.jump_target)
                }}
              >
                {event.jump_target.tab === 'maintenance'
                  ? '状态维护'
                  : event.jump_target.tab === 'profiles'
                    ? '查看当前画像'
                    : '打开相关页面'}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
