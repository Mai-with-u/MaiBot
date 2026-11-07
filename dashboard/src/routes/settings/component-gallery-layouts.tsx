/* eslint-disable react-refresh/only-export-components -- 本文件导出示例目录，内部组件随目录整体重新加载。 */
import { useState } from 'react'
import { Bar, BarChart, XAxis } from 'recharts'

import { AccentPanel } from '@/components/ui/accent-panel'
import { Button } from '@/components/ui/button'
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  ConversationAvatar,
  ConversationHeader,
  ConversationListItem,
  ConversationTransition,
  MessageBubble,
  MessageComposerFrame,
  MessageNotice,
  TraceRound,
  TraceStep,
} from '@/components/ui/conversation'
import {
  DashboardFolderPanel,
  DashboardFolderTab,
  DashboardFolderTabBar,
  DashboardTabBar,
  DashboardTabTrigger,
} from '@/components/ui/dashboard-tabs'
import {
  DataBrowserHeader,
  DataBrowserLayout,
  DataToolbar,
  MetricStrip,
  StatusIndicator,
} from '@/components/ui/data-browser'
import { DataPagination } from '@/components/ui/data-pagination'
import { Input } from '@/components/ui/input'
import {
  FileDropzone,
  ResourceRow,
  SectionPanel,
  SegmentedControl,
  SelectionCard,
} from '@/components/ui/management'
import { Markdown } from '@/components/ui/markdown'
import { OrderControls } from '@/components/ui/order-controls'
import { ScrollArea } from '@/components/ui/scroll-area'
import { SelectionListItem } from '@/components/ui/selection-list-item'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { ZoomableChart } from '@/components/ui/zoomable-chart'

import type { ComponentExample } from './component-gallery-types'

function ChartExample() {
  return (
    <ChartContainer
      className="h-48 w-full"
      config={{ count: { label: '消息数', color: 'var(--primary)' } }}
    >
      <BarChart
        accessibilityLayer
        data={[
          { name: '周一', count: 12 },
          { name: '周二', count: 25 },
          { name: '周三', count: 18 },
        ]}
      >
        <XAxis dataKey="name" />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="count" fill="var(--color-count)" />
      </BarChart>
    </ChartContainer>
  )
}

function ConversationExample() {
  const [mode, setMode] = useState<'chat' | 'observe'>('chat')
  const [open, setOpen] = useState(true)
  const [draft, setDraft] = useState('')
  const [message, setMessage] = useState('你好，这是本地消息示例。')
  return (
    <div className="space-y-3">
      <ConversationListItem
        title="示例会话"
        preview="点击切换选中状态"
        avatar={<ConversationAvatar name="小麦" />}
        selected={mode === 'chat'}
        onSelect={() => setMode((current) => (current === 'chat' ? 'observe' : 'chat'))}
      />
      <ConversationHeader
        title="小麦的私聊"
        subtitle="组件预览"
        avatar={<ConversationAvatar name="麦麦" bot />}
      />
      <ConversationTransition mode={mode}>
        {mode === 'chat' ? (
          <div className="space-y-3">
            <MessageNotice>以下消息仅保存在预览中</MessageNotice>
            <MessageBubble name="小麦" time="12:00" avatar={<ConversationAvatar name="小麦" />}>
              {message}
            </MessageBubble>
            <MessageBubble
              name="麦麦"
              time="12:01"
              outgoing
              avatar={<ConversationAvatar name="麦麦" bot />}
            >
              收到，这是回复示例。
            </MessageBubble>
          </div>
        ) : (
          <TraceRound
            label="思考记录"
            time="12:01"
            open={open}
            onOpenChange={setOpen}
            summary="展开查看步骤"
          >
            <TraceStep tone="tool">读取示例内容</TraceStep>
            <TraceStep tone="success">处理完成</TraceStep>
          </TraceRound>
        )}
      </ConversationTransition>
      <MessageComposerFrame
        tools={<span className="text-xs">本地预览</span>}
        submit={
          <Button
            disabled={!draft.trim()}
            onClick={() => {
              setMessage(draft)
              setDraft('')
              setMode('chat')
            }}
          >
            预览发送
          </Button>
        }
      >
        <Input
          aria-label="示例消息"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      </MessageComposerFrame>
    </div>
  )
}

function DashboardTabsExample() {
  return (
    <div className="space-y-4">
      <Tabs defaultValue="overview">
        <DashboardTabBar>
          <DashboardTabTrigger value="overview">概览</DashboardTabTrigger>
          <DashboardTabTrigger value="details">详情</DashboardTabTrigger>
        </DashboardTabBar>
        <TabsContent value="overview">普通标签内容</TabsContent>
        <TabsContent value="details">详细信息</TabsContent>
      </Tabs>
      <Tabs defaultValue="one">
        <DashboardFolderTabBar>
          <DashboardFolderTab value="one" description="第一个分组">
            文件夹一
          </DashboardFolderTab>
          <DashboardFolderTab value="two">文件夹二</DashboardFolderTab>
        </DashboardFolderTabBar>
        <DashboardFolderPanel className="p-3">
          <TabsContent value="one">文件夹面板内容一</TabsContent>
          <TabsContent value="two">文件夹面板内容二</TabsContent>
        </DashboardFolderPanel>
      </Tabs>
    </div>
  )
}

function BrowserExample() {
  const [search, setSearch] = useState('')
  return (
    <div className="space-y-3">
      <DataBrowserHeader
        title="示例数据"
        summary={
          <MetricStrip
            items={[
              { label: '总数', value: 2 },
              { label: '在线', value: 1, positive: true },
            ]}
          />
        }
      />
      <DataToolbar search={search} onSearchChange={setSearch} placeholder="筛选示例名称" />
      <DataBrowserLayout
        sidebar={<span className="text-muted-foreground p-2 text-sm">全部记录</span>}
      >
        <div className="space-y-2 p-2">
          {['示例一', '示例二']
            .filter((name) => name.includes(search))
            .map((name) => (
              <div key={name}>
                {name}{' '}
                <StatusIndicator status={name === '示例一' ? 'on' : 'off'} variant="inline">
                  {name === '示例一' ? '在线' : '离线'}
                </StatusIndicator>
              </div>
            ))}
        </div>
      </DataBrowserLayout>
    </div>
  )
}

function PaginationExample() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  return (
    <DataPagination
      total={95}
      page={page}
      pageSize={pageSize}
      onPageChange={setPage}
      onPageSizeChange={(value) => {
        setPageSize(value)
        setPage(1)
      }}
    />
  )
}

function ManagementExample() {
  const [checked, setChecked] = useState(true)
  const [file, setFile] = useState<File | null>(null)
  const [mode, setMode] = useState('one')
  const [expanded, setExpanded] = useState(false)
  return (
    <SectionPanel title="资源管理" description="上传区只选取文件，不上传或读取内容。">
      <div className="space-y-4">
        <SegmentedControl
          value={mode}
          onValueChange={setMode}
          options={[
            { value: 'one', label: '模式一' },
            { value: 'two', label: '模式二' },
          ]}
          aria-label="管理模式示例"
        />
        <SelectionCard
          label="示例选项"
          description="点击切换选中状态"
          checked={checked}
          onCheckedChange={setChecked}
        />
        <ResourceRow
          title="示例资源"
          metadata="本地"
          description={expanded ? '这是展开后的资源说明。' : '点击查看说明'}
          onActivate={() => setExpanded((value) => !value)}
        />
        <FileDropzone file={file} onFileChange={setFile} description="仅演示文件选择与清除" />
      </div>
    </SectionPanel>
  )
}

function OrderExample() {
  const [items, setItems] = useState(['项目一', '项目二', '项目三'])
  return (
    <div className="space-y-2">
      {items.map((item, index) => (
        <div key={item} className="flex items-center justify-between gap-2">
          <span>{item}</span>
          <OrderControls
            label={item}
            index={index}
            count={items.length}
            upLabel="上移"
            downLabel="下移"
            onMove={(delta) =>
              setItems((current) => {
                const next = [...current]
                ;[next[index], next[index + delta]] = [next[index + delta], next[index]]
                return next
              })
            }
          />
        </div>
      ))}
    </div>
  )
}

function SelectionExample() {
  const [selected, setSelected] = useState('one')
  return (
    <div>
      {['one', 'two'].map((value) => (
        <SelectionListItem
          key={value}
          selected={selected === value}
          onClick={() => setSelected(value)}
        >
          选项 {value}
        </SelectionListItem>
      ))}
      <SelectionListItem disabled>禁用选项</SelectionListItem>
    </div>
  )
}

export const layoutExamples: ComponentExample[] = [
  {
    file: 'accent-panel',
    name: 'AccentPanel',
    category: '布局',
    description: '具有主题装饰条的强调面板。',
    preview: <AccentPanel contentClassName="p-6">强调内容区域</AccentPanel>,
  },
  {
    file: 'chart',
    name: 'ChartContainer / ChartTooltip / ChartLegend',
    category: '图表',
    description: '图表容器、悬停提示与图例，自动跟随主题。',
    preview: <ChartExample />,
  },
  {
    file: 'collapsible',
    name: 'Collapsible / CollapsibleTrigger / CollapsibleContent',
    category: '布局',
    description: '基础折叠容器。',
    preview: (
      <Collapsible>
        <CollapsibleTrigger asChild>
          <Button variant="outline">展开 / 收起</Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="pt-3">可折叠的示例内容</CollapsibleContent>
      </Collapsible>
    ),
  },
  {
    file: 'conversation',
    name: 'ConversationAvatar / ConversationListItem / ConversationHeader / ConversationTransition / MessageBubble / MessageNotice / MessageComposerFrame / TraceRound / TraceStep',
    category: '布局',
    description: '聊天列表、头像、消息、输入区及思考记录；点击会话项切换视图。',
    preview: <ConversationExample />,
  },
  {
    file: 'dashboard-tabs',
    name: 'DashboardTabBar / DashboardTabTrigger / DashboardFolderTabBar / DashboardFolderTab / DashboardFolderPanel',
    category: '布局',
    description: '普通标签与文件夹样式的页面分组。',
    preview: <DashboardTabsExample />,
  },
  {
    file: 'data-browser',
    name: 'DataBrowserHeader / DataBrowserLayout / DataToolbar / MetricStrip / StatusIndicator',
    category: '布局',
    description: '数据浏览标题、摘要、工具栏和状态。',
    preview: <BrowserExample />,
  },
  {
    file: 'data-pagination',
    name: 'DataPagination',
    category: '布局',
    description: '分页、页码跳转和每页数量。',
    preview: <PaginationExample />,
  },
  {
    file: 'management',
    name: 'SectionPanel / SelectionCard / ResourceRow / FileDropzone / SegmentedControl',
    category: '布局',
    description: '资源管理中共用的容器、选择与文件拖放。',
    preview: <ManagementExample />,
  },
  {
    file: 'markdown',
    name: 'Markdown',
    category: '基础',
    description: 'Markdown 富文本，支持表格与数学公式。',
    preview: (
      <Markdown>
        {'### 示例标题\n\n支持 **加粗**、*斜体*、`代码` 和公式 $E=mc^2$。\n\n- 第一项\n- 第二项'}
      </Markdown>
    ),
  },
  {
    file: 'order-controls',
    name: 'OrderControls',
    category: '表单',
    description: '上下移动列表项，首尾自动禁用。',
    preview: <OrderExample />,
  },
  {
    file: 'scroll-area',
    name: 'ScrollArea / ScrollBar',
    category: '布局',
    description: '统一滚动条，示例可垂直滚动。',
    preview: (
      <ScrollArea className="h-40 rounded border">
        {Array.from({ length: 15 }, (_, index) => (
          <p className="p-2" key={index}>
            滚动内容 {index + 1}
          </p>
        ))}
      </ScrollArea>
    ),
  },
  {
    file: 'selection-list-item',
    name: 'SelectionListItem',
    category: '基础',
    description: '带选中、禁用状态的列表行。',
    preview: <SelectionExample />,
  },
  {
    file: 'zoomable-chart',
    name: 'ZoomableChart',
    category: '图表',
    description: '双指捏合或 Ctrl + 滚轮缩放图表。',
    preview: (
      <ZoomableChart aria-label="可缩放的示例图表">
        <ChartExample />
      </ZoomableChart>
    ),
  },
]
