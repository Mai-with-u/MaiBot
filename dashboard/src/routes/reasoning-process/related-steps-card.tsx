import { Brain, Database, GitBranch, Loader2, MessageSquare, WandSparkles } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { ReasoningToolCallRecord } from '@/lib/reasoning-process-api'
import { cn } from '@/lib/utils'
import { formatStageName } from './context-items'

function stepIcon(stage: string) {
  if (stage === 'planner') return Brain
  if (stage === 'replyer') return MessageSquare
  if (stage.includes('expression')) return WandSparkles
  if (/memory|retriev|recall|query_|spo_/.test(stage)) return Database
  return GitBranch
}

export function RelatedStepsCard({
  records,
  current,
  loading,
  error,
  onSelect,
}: {
  records: ReasoningToolCallRecord[]
  current: { stage: string; session_id: string; stem: string }
  loading: boolean
  error: string | null
  onSelect: (record: ReasoningToolCallRecord) => void
}) {
  return (
    <section
      aria-label="关联步骤"
      className="bg-muted/20 mx-3 mt-3 rounded-md border p-2.5 sm:mx-4"
    >
      <div className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs">
        <GitBranch className="h-3.5 w-3.5" />
        <span className="text-foreground font-medium">关联步骤</span>
        {loading ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <span>{records.length} 个步骤</span>
        )}
        <span className="ml-auto">按记录时间排列</span>
      </div>
      {error ? (
        <p className="text-destructive text-xs">{error}</p>
      ) : (
        <div className="flex max-h-36 flex-wrap gap-2 overflow-y-auto">
          {records.map((record) => {
            const active =
              record.stage === current.stage &&
              record.session === current.session_id &&
              record.stem === current.stem
            const Icon = stepIcon(record.stage)
            const time = new Date(Number(record.stem)).toLocaleTimeString('zh-CN', {
              hour12: false,
            })
            return (
              <Button
                key={`${record.stage}/${record.session}/${record.stem}`}
                variant="outline"
                className={cn(
                  'h-auto gap-2 px-2.5 py-1.5 text-left',
                  active && 'border-primary bg-primary/10 disabled:opacity-100'
                )}
                aria-current={active ? 'step' : undefined}
                disabled={active}
                onClick={() => onSelect(record)}
                title={active ? '当前步骤' : `查看${formatStageName(record.stage)} · ${time}`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="flex flex-col gap-0.5">
                  <span className="text-xs">
                    {formatStageName(record.stage)}
                    {active && ' · 当前'}
                  </span>
                  <span className="text-muted-foreground text-[10px] font-normal">{time}</span>
                </span>
              </Button>
            )
          })}
          {!loading && records.length <= 1 && (
            <span className="text-muted-foreground self-center text-xs">尚无其他关联记录</span>
          )}
        </div>
      )}
    </section>
  )
}
