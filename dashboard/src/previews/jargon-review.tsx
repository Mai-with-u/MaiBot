import { useState } from 'react'
import { Check, ChevronDown, Download, MoreHorizontal, Plus, Upload, X } from 'lucide-react'

import { ChatScopeFilterPanel } from '@/components/chat-scope-filter-panel'
import { Button } from '@/components/ui/button'
import {
  DataBrowserHeader,
  DataBrowserLayout,
  DataToolbar,
  MetricStrip,
} from '@/components/ui/data-browser'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { JargonList } from '@/routes/resource/jargon/JargonList'
import type { Jargon } from '@/types/jargon'

import { LearningReviewTabs } from './learning-review-tabs'

const chats = ['麦麦交流群', '游戏讨论组', '小明的私聊', '开发交流组']
const groups = [
  { name: '日常交流', sessions: ['0', '1', '2'] },
  { name: '开发讨论', sessions: ['3'] },
]
const examples = [
  ['好耶', '表示开心、赞同或期待'],
  ['摸鱼', '工作或学习期间暂时放松'],
  ['破防', '受到触动后，情绪难以保持平静'],
  ['复读机', '重复别人说过的话的人'],
  ['整活', '做出有趣、出人意料的事情'],
  ['咕咕咕', '表示原定计划暂时推迟'],
  ['下次一定', '用来轻松回应一个邀请或请求'],
  ['好家伙', '表示惊讶或感叹'],
]
const sampleJargons: Jargon[] = Array.from({ length: 73 }, (_, index) => {
  const session = index < 43 ? 0 : 1 + (index % 3)
  return {
    id: index + 1,
    content: examples[index % examples.length][0],
    meaning: examples[index % examples.length][1],
    session_id: String(session),
    session_ids: [String(session)],
    chat_name: chats[session],
    chat_names: [chats[session]],
    is_global: index % 9 === 0,
    count: 128 - index,
    is_jargon: index % 5 !== 0,
    is_legacy_empty_meaning: false,
    is_complete: index % 7 !== 0,
    created_by: index % 4 === 0 ? 'MANUAL' : 'AI',
    created_timestamp: '2026-10-17T10:00:00',
    updated_timestamp: '2026-10-17T10:00:00',
  }
})
type Summary =
  | 'total'
  | 'confirmed_jargon'
  | 'confirmed_not_jargon'
  | 'manual_jargon'
  | 'global_count'
  | 'complete_count'
const categories: { value: Summary; label: string; matches: (item: Jargon) => boolean }[] = [
  { value: 'total', label: '总数量', matches: () => true },
  { value: 'confirmed_jargon', label: '已确认黑话', matches: (item) => item.is_jargon },
  { value: 'confirmed_not_jargon', label: '无黑话', matches: (item) => !item.is_jargon },
  { value: 'manual_jargon', label: '手动黑话', matches: (item) => item.created_by === 'MANUAL' },
  { value: 'global_count', label: '全局黑话', matches: (item) => item.is_global },
  { value: 'complete_count', label: '推断完成', matches: (item) => item.is_complete },
]

/** 使用实际列表和公共浏览组件；示例状态只保存在本页，不调用业务接口。 */
export function JargonReview() {
  const [items, setItems] = useState(sampleJargons)
  const [search, setSearch] = useState('')
  const [scope, setScope] = useState<'chat' | 'group' | 'all'>('chat')
  const [scopeId, setScopeId] = useState<string | number>('0')
  const [summary, setSummary] = useState<Summary>('total')
  const [selected, setSelected] = useState(new Set<number>())
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [notice, setNotice] = useState('示例数据；操作仅在预览页生效，不调用业务接口')
  const activeCategory = categories.find((category) => category.value === summary)!
  const filtered = items.filter((item) => {
    const scopeMatches =
      scope === 'all' ||
      summary === 'global_count' ||
      (scope === 'chat'
        ? item.session_ids.includes(String(scopeId))
        : groups[Number(scopeId)].sessions.some((session) => item.session_ids.includes(session)))
    return scopeMatches && activeCategory.matches(item) && item.content.includes(search)
  })
  const rows = filtered.slice((page - 1) * pageSize, page * pageSize)
  const changeParams = () => {
    setPage(1)
  }
  const markSelected = (isJargon: boolean) => {
    setItems((current) =>
      current.map((item) => (selected.has(item.id) ? { ...item, is_jargon: isJargon } : item))
    )
    setNotice(`预览：已将 ${selected.size} 条标记为${isJargon ? '黑话' : '无黑话'}`)
    setSelected(new Set())
    changeParams()
  }

  return (
    <main className="learning-review-screen">
      <DataBrowserHeader
        title="学习"
        navigation={<LearningReviewTabs page="jargon" />}
        summary={
          <MetricStrip
            items={[
              { label: '总数量', value: items.length.toLocaleString() },
              {
                label: '已确认黑话',
                value: items.filter((item) => item.is_jargon).length,
                positive: true,
              },
              { label: '推断完成', value: items.filter((item) => item.is_complete).length },
            ]}
          />
        }
      />
      <DataBrowserLayout
        sidebar={
          <ChatScopeFilterPanel
            density="compact"
            title="聊天范围"
            titleMeta={scope === 'chat' ? `${chats.length} 个` : undefined}
            modes={[
              { label: '聊天', value: 'chat' },
              { label: '组', value: 'group' },
              { label: '全部', value: 'all' },
            ]}
            activeMode={scope}
            onModeChange={(value) => {
              setScope(value)
              setScopeId('0')
              if (value === 'group' && summary === 'global_count') setSummary('total')
              changeParams()
            }}
            selectedItemId={scopeId}
            onItemSelect={(value) => {
              setScopeId(value)
              if (scope === 'chat' && summary === 'global_count') setSummary('total')
              changeParams()
            }}
            items={
              scope === 'chat'
                ? chats.map((name, index) => ({
                    id: String(index),
                    label: name,
                    description: '账号 2814567…',
                    meta: items.filter((item) => item.session_ids.includes(String(index))).length,
                  }))
                : scope === 'group'
                  ? groups.map((group, index) => ({
                      id: String(index),
                      label: group.name,
                      description: group.sessions
                        .map((session) => chats[Number(session)])
                        .join('、'),
                    }))
                  : []
            }
            emptyContent={<p className="text-muted-foreground p-3 text-sm">当前显示全部黑话</p>}
            listClassName="max-h-64 lg:max-h-none"
          />
        }
      >
        <JargonList
          jargons={rows}
          total={filtered.length}
          page={page}
          pageSize={pageSize}
          loading={false}
          selectedIds={selected}
          hideChatColumn={scope === 'chat' || summary === 'global_count'}
          toolbar={
            <DataToolbar
              search={search}
              onSearchChange={(value) => {
                setSearch(value)
                changeParams()
              }}
              placeholder="搜索黑话内容..."
              actions={
                <>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" size="sm" aria-label="黑话分类">
                        {activeCategory.label} {items.filter(activeCategory.matches).length}
                        <ChevronDown />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuRadioGroup
                        value={summary}
                        onValueChange={(value) => {
                          setSummary(value as Summary)
                          if (value === 'global_count') setScope('all')
                          changeParams()
                        }}
                      >
                        {categories.map((category) => (
                          <DropdownMenuRadioItem key={category.value} value={category.value}>
                            {category.label}
                            <span className="ml-auto pl-4 tabular-nums">
                              {items.filter(category.matches).length}
                            </span>
                          </DropdownMenuRadioItem>
                        ))}
                      </DropdownMenuRadioGroup>
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <Button
                    variant="outline"
                    size="icon-sm"
                    aria-label="导出黑话"
                    title="导出黑话"
                    onClick={() => setNotice('预览：正式页面将打开导出窗口')}
                  >
                    <Download />
                  </Button>
                  <Button size="sm" onClick={() => setNotice('预览：正式页面将打开新增窗口')}>
                    <Plus />
                    新增
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" size="icon-sm" aria-label="更多操作">
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => setNotice('预览：正式页面将打开导入窗口')}>
                        <Upload />
                        导入黑话
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </>
              }
            >
              {selected.size > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-2 border-t pt-2">
                  <span className="text-muted-foreground text-xs">已选择 {selected.size} 个</span>
                  <Button variant="outline" size="xs" onClick={() => markSelected(true)}>
                    <Check />
                    标记为黑话
                  </Button>
                  <Button variant="outline" size="xs" onClick={() => markSelected(false)}>
                    <X />
                    标记为无黑话
                  </Button>
                  <Button variant="outline" size="xs" onClick={() => setSelected(new Set())}>
                    取消选择
                  </Button>
                </div>
              )}
            </DataToolbar>
          }
          onEdit={(item) => setNotice(`预览编辑：${item.content}`)}
          onDelete={(item) => setNotice(`预览删除确认：${item.content}`)}
          onToggleSelect={(id) =>
            setSelected((current) => {
              const next = new Set(current)
              if (next.has(id)) next.delete(id)
              else next.add(id)
              return next
            })
          }
          onToggleSelectAll={() =>
            setSelected((current) => {
              const next = new Set(current)
              const allSelected = rows.every((item) => current.has(item.id))
              rows.forEach((item) => {
                if (allSelected) next.delete(item.id)
                else next.add(item.id)
              })
              return next
            })
          }
          onPageChange={setPage}
          onPageSizeChange={(value) => {
            setPageSize(value)
            changeParams()
          }}
        />
      </DataBrowserLayout>
      <p role="status" className="text-muted-foreground text-xs">
        {notice}
      </p>
    </main>
  )
}
