import { useQuery } from '@tanstack/react-query'
import { ChevronDown } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { getChatSchedulingStatus, type ChatSchedulingStatus } from '@/lib/chat-management-api'

const percent = new Intl.NumberFormat('zh-CN', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 })
const silenceLabels = {
  unscheduled: '暂时不会主动查看',
  scheduled: '过一会儿再决定要不要看',
  sampled: '这次已经判断过，等新消息',
}

function replyState(status: ChatSchedulingStatus) {
  if (status.paused) return '已暂停回复'
  if (status.mode === 'private' && status.configured_frequency === 0) return '已暂停私聊回复'
  if (status.effective_frequency === null) return '这个聊天当前未运行'
  if (status.mode === 'focus') return '多个聊天一起看'
  if (status.effective_frequency === 0) return '只收消息，不回复'
  return status.mode === 'private' ? '收到私聊就看' : '看过消息再决定是否回复'
}

export function ChatSchedulingStatusButton({ sessionId }: { sessionId: string }) {
  const query = useQuery({
    queryKey: ['chat-scheduling-status', sessionId],
    queryFn: () => getChatSchedulingStatus(sessionId),
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  })
  const status = query.data
  if (query.isError || !status) {
    return <span className="text-muted-foreground text-xs">{query.isError ? '暂时看不到回复状态' : '正在读取回复状态…'}</span>
  }

  const group = status.group_status
  const probability = group?.view_probability ?? null
  const state = replyState(status)
  const summary = status.mode === 'group' && !status.paused && probability !== null && probability > 0
    ? `查看概率 ${percent.format(probability)}`
    : state
  const rows: [string, string][] = status.mode === 'group' ? [] : [['麦麦会怎么做', state]]
  if (status.mode === 'group') {
    rows.push(['查看频率设置', String(status.configured_frequency)])
    if (status.adjustment !== null && status.adjustment !== 1) {
      rows.push(['频率调整值', `乘以 ${status.adjustment}`])
    }
    if (probability !== null) rows.push(['查看消息概率', percent.format(probability)])
  } else if (status.mode === 'private') {
    rows.push(['私聊回复', status.configured_frequency > 0 ? '已开启' : '已暂停'])
  } else {
    rows.push(['在哪个聊天里回复', '麦麦看过几个聊天后一起决定'])
  }
  if (group) {
    if (group.completed_rounds !== null) rows.push(['这次想了几轮', `已想 ${group.completed_rounds} 轮，最多 ${group.round_limit} 轮`])
    if (group.round_debt > 0) {
      rows.push(['还要跳过几次查看', `接下来跳过 ${group.round_debt} 次原本会看的普通消息`])
    }
    rows.push(['群里没人说话时', silenceLabels[group.silence]])
  }
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="xs" className="max-w-full gap-1 px-1.5 text-xs" aria-label={`${summary}，查看回复详情`}>
          <span className="truncate">{summary}</span>
          <ChevronDown aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" aria-label="麦麦的回复状态" className="w-80 max-w-[calc(100vw-2rem)]">
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-xs">
          {rows.map(([label, value]) => (
            <div className="contents" key={label}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="text-right tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      </PopoverContent>
    </Popover>
  )
}
