import { useRef, useState } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { Activity, Bot, Brain, ImagePlus, Send, Smile, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  ConversationAvatar,
  ConversationHeader,
  ConversationTransition,
  MessageComposerFrame,
} from '@/components/ui/conversation'
import { StatusIndicator } from '@/components/ui/data-browser'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { Textarea } from '@/components/ui/textarea'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ChatWorkspaceSidebar } from '@/routes/chat/ChatWorkspaceSidebar'
import { MessageList } from '@/routes/chat/MessageList'
import { ObservationHeader } from '@/routes/monitor/ObservationHeader'
import { TimelineEventRenderer } from '@/routes/monitor/maisaka-monitor'
import type { ChatMessage, ChatTab } from '@/routes/chat/types'
import type { SessionInfo, TimelineEntry } from '@/routes/monitor/use-maisaka-monitor'
import type { FlowStepEvent, PlannerFinalizedEvent } from '@/lib/maisaka-monitor-client'
import zh from '@/i18n/locales/zh.json'

const i18n = createInstance()
void i18n.init({
  lng: 'zh',
  resources: { zh: { translation: zh } },
  interpolation: { escapeValue: false },
})
const names = ['戒色中队', '糖人街会员制餐厅', '第恁音游人', '共享千石', '魔鸢', '北化乐灵会']
const sessions = new Map<string, SessionInfo>(
  names.map((name, i) => [
    String(i),
    {
      sessionId: String(i),
      sessionName: name,
      isGroupChat: true,
      lastActivity: 10 - i,
      eventCount: 1,
    },
  ])
)
const localTab: ChatTab = {
  id: 'webui-default',
  type: 'webui',
  label: '麦麦',
  messages: [],
  isConnected: true,
  isTyping: false,
  sessionInfo: { bot_name: '麦麦' },
}
const messages: ChatMessage[] = [
  {
    id: 'notice',
    type: 'system',
    content: '已连接本地聊天室，可以开始和麦麦对话',
    timestamp: 1792217880,
  },
  ...(['你好', '?', '?', '?', '?', '你到底说不说'] as const).map(
    (content, i): ChatMessage => ({
      id: String(i),
      type: [3, 5].includes(i) ? 'bot' : 'user',
      content,
      timestamp: 1792217880 + i * 60,
    })
  ),
]
function makeRound(cycle: number): TimelineEntry {
  const data: PlannerFinalizedEvent = {
    session_id: '0',
    cycle_id: cycle,
    timestamp: 1792217880 + cycle * 2,
    planner_started_at: 1792217880,
    planner_ended_at: 1792217882,
    timing_gate: null,
    request: { selected_history_count: 31, tool_count: 20 },
    planner: {
      content: null,
      model_name: 'deepseek-flash',
      tool_calls:
        cycle === 2 ? [{ id: 'collect', name: 'pixiv_collect_excellent', arguments: {} }] : [],
      prompt_tokens: 12307,
      completion_tokens: 43,
      total_tokens: 12350,
      duration_ms: 1840,
    },
    active_tool_call_id: cycle === 2 ? 'collect' : undefined,
    tools:
      cycle === 2
        ? []
        : [
            {
              tool_call_id: 'search',
              tool_name: 'tool_search',
              tool_args: { query: 'pixiv' },
              success: true,
              duration_ms: 1,
              summary: '找到 4 个 pixiv 工具',
              newly_discovered_tool_names: [
                'pixiv_collect_excellent',
                'pixiv_get_illust',
                'pixiv_get_ranking',
                'pixiv_search_illusts',
              ],
              matched_tool_names: [
                'pixiv_collect_excellent',
                'pixiv_get_illust',
                'pixiv_get_ranking',
                'pixiv_search_illusts',
              ],
            },
          ],
    final_state: {
      time_records: {},
      agent_state: 'running',
      end_detail: 'Planner 工具执行完成，继续下一轮内部思考。',
    },
  }
  return {
    id: `round-${cycle}`,
    type: cycle === 2 ? 'planner.progress' : 'planner.finalized',
    data,
    timestamp: data.timestamp,
    sessionId: '0',
  }
}
const rounds: TimelineEntry[] = [
  {
    id: 'incoming',
    type: 'message.ingested',
    sessionId: '0',
    timestamp: 1792217870,
    data: {
      session_id: '0',
      message_id: 'incoming',
      speaker_name: '樱岛里有麻衣在爬',
      content: '今天赢三把就算赢',
      timestamp: 1792217870,
    },
  },
  makeRound(1),
  makeRound(2),
  {
    id: 'context-trim',
    type: 'flow.step',
    sessionId: '0',
    timestamp: 1792217910,
    data: {
      session_id: '0', run_id: 'preview', cycle_id: 2,
      step: 'context.trimmed', title: '上下文裁切', detail: '', timestamp: 1792217910,
      trim: {
        removed_count: 74, remaining_count: 50,
        learning: {
          status: 'completed', detail: '',
          learners: [
            { name: '表达学习', status: 'completed', detail: '写入 4 条表达', contents: ['表示赞同 → 确实如此', '表示惊讶 → 这么厉害', '分享喜悦 → 今天真开心', '表达感谢 → 谢谢你'] },
            { name: '黑话学习', status: 'completed', detail: '处理 2 个黑话候选', contents: ['好耶', '绝绝子'] },
            { name: '高频词学习', status: 'completed', detail: '更新 2 个词条', contents: ['模型（4 次）', 'agent（2 次）'] },
          ],
        },
      },
    } satisfies FlowStepEvent,
  },
]

/** 正式聊天列表、消息和轮次渲染器；预览数据与操作均留在本页，不连接聊天服务。 */
function ConversationReview({ thinking = false }: { thinking?: boolean }) {
  const [observed, setObserved] = useState<string | null>(thinking ? '0' : null)
  const [items, setItems] = useState(messages)
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [allOpen, setAllOpen] = useState(true)
  const [notice, setNotice] = useState('示例数据；操作只影响本页')
  const traceRef = useRef<HTMLDivElement>(null)
  const imageRef = useRef<HTMLInputElement>(null)
  const title = observed === null ? '麦麦' : sessions.get(observed)!.sessionName
  const send = () => {
    if (!draft.trim()) return
    setItems((current) => [
      ...current,
      { id: crypto.randomUUID(), type: 'user', content: draft, timestamp: Date.now() / 1000 },
    ])
    setDraft('')
  }
  return (
    <I18nextProvider i18n={i18n}>
      <TooltipProvider>
        <p className="text-muted-foreground px-4 pt-2 text-xs" role="status">
          {notice}
        </p>
        <div data-chat-workspace="true" className="conversation-review-screen">
          <ChatWorkspaceSidebar
            tabs={[localTab]}
            activeTabId="webui-default"
            activeObservedSessionId={observed}
            observedSessions={sessions}
            observedStageStatuses={new Map()}
            observedLatestMessages={
              new Map(
                names.map((_, i) => [
                  String(i),
                  { speakerName: '麦麦', content: '今天赢三把就算赢', mediaText: '' },
                ])
              )
            }
            onSwitch={() => setObserved(null)}
            onSelectObserved={setObserved}
            onOpenObservedSettings={(id) =>
              setNotice(`${sessions.get(id)!.sessionName}：此处在正式页面打开聊天流设置`)
            }
            onClose={() => {}}
          />
          <PageFrame className="flex min-h-0 min-w-0 flex-1 flex-col">
            <ConversationTransition mode={observed === null ? 'chat' : 'observe'}>
            {observed === null ? (
              <ConversationHeader
                title={title}
                avatar={<ConversationAvatar name="麦麦" bot />}
                subtitle={<StatusIndicator variant="inline" status="on">本地聊天室 · 已连接</StatusIndicator>}
                actions={
                  <Button variant="outline" size="icon-sm" aria-label="清空预览消息" onClick={() => setItems([])}>
                    <Trash2 />
                  </Button>
                }
              />
            ) : (
              <ObservationHeader
                title={title}
                subtitle={<StatusIndicator variant="inline" status="mixed">工具执行中 · 第 2/16 轮 · 更新于 1 分钟前</StatusIndicator>}
                allExpanded={allOpen}
                autoScroll={false}
                onToggleAll={() => { setAllOpen(!allOpen); setOpen({}) }}
                onScrollToTop={() => traceRef.current?.scrollTo({ top: 0, behavior: 'smooth' })}
                onScrollToBottom={() => traceRef.current?.scrollTo({ top: traceRef.current.scrollHeight, behavior: 'smooth' })}
                onFindPreviousBotMessage={() => traceRef.current?.scrollTo({ top: 0, behavior: 'smooth' })}
                onClearTimeline={() => setNotice('预览保留示例记录；正式页面可清空记录')}
                tools={
                  <>
                    {[
                      { label: '统计', Icon: Activity, content: '2 轮 · 24,700 tokens' },
                      { label: '当前上下文', Icon: Brain, content: '上下文 31 条 · 可用工具 20' },
                      { label: '子代理', Icon: Bot, content: '当前没有子代理任务' },
                    ].map(({ label, Icon, content }) => (
                      <Popover key={label}>
                        <PopoverTrigger asChild>
                          <Button variant="outline" size="sm"><Icon />{label}</Button>
                        </PopoverTrigger>
                        <PopoverContent>{content}</PopoverContent>
                      </Popover>
                    ))}
                  </>
                }
              />
            )}
            {observed !== null ? (
              <DataPanel
                padding="none"
                data-conversation-screen="true"
                className="overflow-y-auto!"
                ref={traceRef}
              >
                <div data-conversation-messages="true" className="space-y-3">
                  {rounds.map((entry) => (
                    <TimelineEventRenderer
                      key={entry.id}
                      entry={entry}
                      roundOpen={open[entry.id] ?? allOpen}
                      onRoundOpenChange={(value) =>
                        setOpen((current) => ({ ...current, [entry.id]: value }))
                      }
                      onJumpToMessage={() => {}}
                      onOpenReasoning={() => {}}
                    />
                  ))}
                </div>
              </DataPanel>
            ) : (
              <>
                <MessageList
                  messages={items}
                  isLoadingHistory={false}
                  botDisplayName="麦麦"
                  userName="人类"
                  language="zh-CN"
                />
                <MessageComposerFrame
                  tools={
                    <>
                      <input
                        ref={imageRef}
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => {
                          const file = e.target.files?.[0]
                          if (file) setNotice(`已选择 ${file.name}（预览不上传）`)
                          e.target.value = ''
                        }}
                      />
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label="选择图片"
                        onClick={() => imageRef.current?.click()}
                      >
                        <ImagePlus />
                      </Button>
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label="插入表情"
                        onClick={() => setDraft((current) => current + '😊')}
                      >
                        <Smile />
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setNotice('预览以「人类」发送；正式页面可编辑头像与昵称')}
                      >
                        以「人类」发送
                      </Button>
                    </>
                  }
                  hint="Enter 发送 · Shift+Enter 换行"
                  submit={
                    <Button size="sm" disabled={!draft.trim()} onClick={send}>
                      <Send />
                      发送
                    </Button>
                  }
                >
                  <Textarea
                    variant="composer"
                    autoResize
                    minHeight={56}
                    maxHeight={160}
                    aria-label="输入消息"
                    placeholder="输入消息…"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                        e.preventDefault()
                        send()
                      }
                    }}
                  />
                </MessageComposerFrame>
              </>
            )}
            </ConversationTransition>
          </PageFrame>
        </div>
      </TooltipProvider>
    </I18nextProvider>
  )
}
export function ChatReview() {
  return <ConversationReview />
}
export function ThinkingReview() {
  return <ConversationReview thinking />
}
