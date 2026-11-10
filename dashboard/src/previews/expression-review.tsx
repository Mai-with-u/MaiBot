import { useState } from 'react'
import { Download, Plus } from 'lucide-react'

import { ChatScopeFilterPanel } from '@/components/chat-scope-filter-panel'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { SwitchField } from '@/components/ui/switch'
import {
  DataBrowserHeader,
  DataBrowserLayout,
  DataToolbar,
  MetricStrip,
} from '@/components/ui/data-browser'
import { ExpressionList } from '@/routes/resource/expression/ExpressionList'
import { ExpressionScopeStatus } from '@/routes/resource/expression/ExpressionScopeStatus'
import type { Expression } from '@/types/expression'

import { LearningReviewTabs } from './learning-review-tabs'

const examples = [
  ['吐槽某人贴的', '这一个是xx全贴的吧'],
  ['吐槽他人离谱行为', '屑爆了 口语化感叹'],
  ['感叹群友行为', '这个群为什么有那么多…'],
  ['对游戏操作表示惊叹', '卧槽这个有点强'],
  ['回应他人请求或提议', '可以'],
  ['表达个人意愿或坚持', '我乐意xxxx'],
  ['嘲讽对方能力不行', '废物/菜/就这'],
  ['表达认同', '确实如此'],
]
const chats = [
  '糖人街会员制餐厅',
  '退化棒（战场的狗）',
  '北化乐灵会',
  '共享千石',
  '戒色中队',
  '麦麦大脑机接口',
  '灰的MaiBot 麦麦开发',
  '第恁音游人',
  '麦克啊瑟',
  '魔鸢',
]
const sampleExpressions: Expression[] = Array.from({ length: 73 }, (_, index) => ({
  id: index + 1,
  situation: examples[index % examples.length][0],
  style: examples[index % examples.length][1],
  chat_id: '0',
  chat_name: chats[0],
  checked: index === 2,
  modified_by: index === 2 ? 'user' : null,
  last_active_time: 0,
  create_date: null,
}))

/** 直接使用生产组件展示布局；所有操作只改变本页示例，不连接业务接口。 */
export function ExpressionReview() {
  const [items, setItems] = useState(sampleExpressions)
  const [search, setSearch] = useState('')
  const [scope, setScope] = useState<'chat' | 'group' | 'all'>('chat')
  const [chat, setChat] = useState<string | number>('0')
  const [legacy, setLegacy] = useState(false)
  const [filter, setFilter] = useState<'all' | 'user_checked' | 'unchecked'>('all')
  const [sortBy, setSortBy] = useState('time')
  const [selected, setSelected] = useState(new Set<number>())
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [notice, setNotice] = useState('示例数据；操作仅在预览页生效')
  const filtered = items.filter(
    (item) =>
      `${item.situation}${item.style}`.includes(search) &&
      (filter === 'all' || (filter === 'user_checked' ? item.checked : !item.checked))
  )
  const rows = filtered.slice((page - 1) * pageSize, page * pageSize)
  const changeFilter = (value: typeof filter) => {
    setFilter(value)
    setPage(1)
    setSelected(new Set())
  }
  return (
    <main className="learning-review-screen">
      <DataBrowserHeader
        title="学习"
        navigation={<LearningReviewTabs page="expression" />}
        summary={
          <>
            <MetricStrip
              items={[
                { label: '总数量', value: '35,903' },
                { label: '近7天新增', value: '+5,579', positive: true },
              ]}
            />
          </>
        }
      />
      <DataBrowserLayout
        sidebar={
          <ChatScopeFilterPanel
            density="compact"
            title="聊天范围"
            titleMeta="10 个"
            modes={[
              { label: '聊天', value: 'chat' },
              { label: '组', value: 'group' },
              { label: '全部', value: 'all' },
            ]}
            activeMode={scope}
            onModeChange={setScope}
            selectedItemId={chat}
            onItemSelect={setChat}
            items={chats.map((name, index) => ({
              id: String(index),
              label: name,
              description: '账号 2814567…',
              meta: (1204 - index * 121).toLocaleString(),
              trailing: <ExpressionScopeStatus enableLearning="on" useExpression="on" />,
            }))}
            footer={
              <label className="flex items-center justify-between gap-2 text-xs">
                显示旧格式
                <Checkbox
                  size="sm"
                  checked={legacy}
                  onCheckedChange={(value) => setLegacy(value === true)}
                />
              </label>
            }
            listClassName="max-h-64 lg:max-h-none"
          />
        }
      >
        <ExpressionList
          expressions={rows}
          total={filtered.length}
          page={page}
          pageSize={pageSize}
          loading={false}
          selectedIds={selected}
          chatNameMap={new Map()}
          hideChatColumn
          reviewFilter={filter}
          onReviewFilterChange={changeFilter}
          toolbar={
            <DataToolbar
              search={search}
              onSearchChange={(value) => {
                setSearch(value)
                setPage(1)
                setSelected(new Set())
              }}
              placeholder="搜索情境、风格或上下文..."
              actions={
                <>
                  <Select value={sortBy} onValueChange={setSortBy}>
                    <SelectTrigger controlSize="sm" className="w-[130px]" aria-label="表达方式排序">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="time">最近活跃</SelectItem>
                      <SelectItem value="created">最新创建</SelectItem>
                    </SelectContent>
                  </Select>
                  <SwitchField
                    controlSize="sm"
                    label="仅精选"
                    checked={filter === 'user_checked'}
                    onCheckedChange={(checked) => changeFilter(checked ? 'user_checked' : 'all')}
                  />
                  <Button
                    variant="outline"
                    size="icon-sm"
                    title="导出所选"
                    aria-label="导出所选"
                    disabled={selected.size === 0}
                    onClick={() => setNotice(`预览：已选择 ${selected.size} 条用于导出`)}
                  >
                    <Download />
                  </Button>
                  <Button size="sm" onClick={() => setNotice('预览：正式页面将打开新增窗口')}>
                    <Plus />
                    新增
                  </Button>
                </>
              }
            />
          }
          onEdit={(item) => setNotice(`预览编辑：${item.situation}`)}
          onDelete={(item) => setNotice(`预览删除确认：${item.situation}`)}
          onToggleReviewStatus={async (item) => {
            setItems((current) =>
              current.map((row) =>
                row.id === item.id ? { ...row, checked: !row.checked, modified_by: 'user' } : row
              )
            )
          }}
          onToggleSelect={(id) =>
            setSelected((current) => {
              const next = new Set(current)
              if (next.has(id)) next.delete(id)
              else next.add(id)
              return next
            })
          }
          onToggleSelectAll={() =>
            setSelected(
              selected.size === rows.length ? new Set() : new Set(rows.map((item) => item.id))
            )
          }
          onPageChange={(value) => {
            setPage(value)
            setSelected(new Set())
          }}
          onPageSizeChange={(value) => {
            setPageSize(value)
            setPage(1)
            setSelected(new Set())
          }}
        />
      </DataBrowserLayout>
      <p role="status" className="text-muted-foreground text-xs">
        {notice}
      </p>
    </main>
  )
}
