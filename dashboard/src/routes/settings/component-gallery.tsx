import { useState, type ReactNode } from 'react'
import { Plus } from 'lucide-react'
import { useTheme } from '@/components/use-theme'

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Board, BoardModule, BoardScreen } from '@/components/ui/board'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { DashedButton } from '@/components/ui/dashed-button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Progress } from '@/components/ui/progress'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Slider } from '@/components/ui/slider'
import { StatusLight } from '@/components/ui/status-light'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { Switch, SwitchField } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

import { feedbackExamples } from './component-gallery-feedback'
import { formExamples } from './component-gallery-forms'
import { layoutExamples } from './component-gallery-layouts'
import { ComponentGalleryCanvas } from './component-gallery-canvas'
import type { ComponentExample } from './component-gallery-types'

// 示例不连接业务接口，输入与选择状态仅保留在当前预览中。
const examples: ComponentExample[] = [
  {
    name: 'Button',
    file: 'button',
    category: '基础',
    description: '按钮：主要、次要、描边、危险及禁用状态。',
    preview: (
      <div className="flex flex-wrap gap-2">
        {(['default', 'secondary', 'outline', 'destructive', 'ghost', 'link'] as const).map(
          (variant) => (
            <Button key={variant} variant={variant}>
              {variant}
            </Button>
          )
        )}
        <Button disabled>禁用</Button>
      </div>
    ),
  },
  {
    name: 'DashedButton',
    file: 'dashed-button',
    category: '基础',
    description: '虚线按钮：用于添加厂商、安排模型等操作。千禧风格下，悬停时像键帽一样凸起，按下或展开菜单时呈现压入效果。',
    preview: (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <DashedButton className="w-full">
            <Plus className="size-3.5" aria-hidden="true" />
            添加条目
          </DashedButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem>手动添加</DropdownMenuItem>
          <DropdownMenuItem>从模板添加</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    ),
  },
  {
    name: 'Badge',
    file: 'badge',
    category: '基础',
    description: '徽标：分类与状态标签。',
    preview: (
      <div className="flex flex-wrap gap-2">
        {(['default', 'secondary', 'success', 'destructive', 'outline'] as const).map((variant) => (
          <Badge key={variant} variant={variant}>
            {variant}
          </Badge>
        ))}
      </div>
    ),
  },
  {
    name: 'Avatar',
    file: 'avatar',
    category: '基础',
    description: '头像：无图片时的占位内容。',
    preview: (
      <Avatar>
        <AvatarFallback>麦</AvatarFallback>
      </Avatar>
    ),
  },
  {
    name: 'Separator',
    file: 'separator',
    category: '基础',
    description: '分隔线：划分相邻内容。',
    preview: (
      <div className="space-y-3">
        <p>上方内容</p>
        <Separator />
        <p>下方内容</p>
      </div>
    ),
  },
  {
    name: 'Input',
    file: 'input',
    category: '表单',
    description: '单行输入框与禁用状态。',
    preview: (
      <div className="space-y-3">
        <Input aria-label="输入框示例" placeholder="试着输入内容" />
        <Input aria-label="禁用输入框" disabled placeholder="禁用状态" />
      </div>
    ),
  },
  {
    name: 'Textarea',
    file: 'textarea',
    category: '表单',
    description: '多行文本输入。',
    preview: <Textarea aria-label="多行输入示例" placeholder="输入多行内容…" />,
  },
  {
    name: 'Checkbox',
    file: 'checkbox',
    category: '表单',
    description: '复选框：支持勾选、未选和禁用。',
    preview: (
      <div className="flex flex-wrap gap-4">
        <label htmlFor="gallery-checkbox-checked" className="flex items-center gap-2">
          <Checkbox id="gallery-checkbox-checked" defaultChecked />
          已选
        </label>
        <label htmlFor="gallery-checkbox-unchecked" className="flex items-center gap-2">
          <Checkbox id="gallery-checkbox-unchecked" />
          可选
        </label>
        <Checkbox disabled aria-label="禁用复选框" />
      </div>
    ),
  },
  {
    name: 'Switch / SwitchField',
    file: 'switch',
    category: '表单',
    description: '开关与带标签的工具栏开关。',
    preview: (
      <div className="flex flex-wrap items-center gap-4">
        <Switch defaultChecked aria-label="开关示例" />
        <SwitchField label="启用功能" />
      </div>
    ),
  },
  {
    name: 'RadioGroup',
    file: 'radio-group',
    category: '表单',
    description: '单选组：一次选择一个选项。',
    preview: (
      <RadioGroup defaultValue="a" aria-label="单选示例">
        {['a', 'b'].map((value) => (
          <label key={value} className="flex items-center gap-2">
            <RadioGroupItem value={value} />
            选项 {value.toUpperCase()}
          </label>
        ))}
      </RadioGroup>
    ),
  },
  {
    name: 'Select',
    file: 'select',
    category: '表单',
    description: '下拉选择：点击或使用键盘选择。',
    preview: (
      <Select defaultValue="one">
        <SelectTrigger aria-label="下拉示例">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="one">选项一</SelectItem>
          <SelectItem value="two">选项二</SelectItem>
        </SelectContent>
      </Select>
    ),
  },
  {
    name: 'Slider',
    file: 'slider',
    category: '表单',
    description: '滑块：拖动或使用方向键调整数值。',
    preview: (
      <div className="py-4">
        <Slider defaultValue={[40]} max={100} step={1} aria-label="滑块示例" />
      </div>
    ),
  },
  {
    name: 'Card',
    file: 'card',
    category: '布局',
    description: '卡片：标题与内容容器。',
    preview: (
      <Card>
        <CardHeader>
          <CardTitle>示例卡片</CardTitle>
        </CardHeader>
        <CardContent>放置相关信息或操作。</CardContent>
      </Card>
    ),
  },
  {
    name: 'Board / BoardModule / BoardScreen',
    file: 'board',
    category: '布局',
    description: '面板：适应主题的网格、模块标题和显示区。',
    preview: (
      <Board>
        <BoardModule code="STATUS" title="运行状态" span={12}>
          <BoardScreen>面板内容</BoardScreen>
        </BoardModule>
      </Board>
    ),
  },
  {
    name: 'PageFrame / DataPanel',
    file: 'surface',
    category: '布局',
    description: '页面外壳与只读数据区域。',
    preview: (
      <PageFrame className="p-3">
        <DataPanel>数据展示区域</DataPanel>
      </PageFrame>
    ),
  },
  {
    name: 'Tabs',
    file: 'tabs',
    category: '布局',
    description: '标签页：切换不同内容区域。',
    preview: (
      <Tabs defaultValue="one">
        <TabsList>
          <TabsTrigger value="one">概览</TabsTrigger>
          <TabsTrigger value="two">详情</TabsTrigger>
        </TabsList>
        <TabsContent value="one">概览内容</TabsContent>
        <TabsContent value="two">详情内容</TabsContent>
      </Tabs>
    ),
  },
  {
    name: 'Accordion',
    file: 'accordion',
    category: '布局',
    description: '折叠面板：按需展开详情。',
    preview: (
      <Accordion type="single" collapsible>
        <AccordionItem value="example">
          <AccordionTrigger>展开查看</AccordionTrigger>
          <AccordionContent>这里是折叠面板的内容。</AccordionContent>
        </AccordionItem>
      </Accordion>
    ),
  },
  {
    name: 'Table',
    file: 'table',
    category: '布局',
    description: '表格：展示结构化数据。',
    preview: (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>名称</TableHead>
            <TableHead>状态</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>示例任务</TableCell>
            <TableCell>已完成</TableCell>
          </TableRow>
        </TableBody>
      </Table>
    ),
  },
  {
    name: 'Alert',
    file: 'alert',
    category: '反馈',
    description: '提示：普通信息与错误状态。',
    preview: (
      <div className="space-y-3">
        <Alert>
          <AlertTitle>提示信息</AlertTitle>
          <AlertDescription>这是一条示例提示。</AlertDescription>
        </Alert>
        <Alert variant="destructive">
          <AlertTitle>错误示例</AlertTitle>
          <AlertDescription>请检查输入内容。</AlertDescription>
        </Alert>
      </div>
    ),
  },
  {
    name: 'Progress',
    file: 'progress',
    category: '反馈',
    description: '进度条：显示完成比例。',
    preview: <Progress value={65} aria-label="示例进度 65%" />,
  },
  {
    name: 'StatusLight',
    file: 'status-light',
    category: '反馈',
    description: '状态灯：运行、停止、处理和异常。',
    preview: (
      <div className="flex flex-wrap gap-4">
        {(['on', 'off', 'mixed', 'error'] as const).map((status) => (
          <span key={status} className="flex items-center gap-2">
            <StatusLight status={status} />
            {status}
          </span>
        ))}
      </div>
    ),
  },
  {
    name: 'Skeleton',
    file: 'skeleton',
    category: '反馈',
    description: '骨架屏：加载中的内容占位。',
    preview: (
      <div className="space-y-3">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-5 w-full" />
      </div>
    ),
  },
  {
    name: 'Dialog',
    file: 'dialog',
    category: '浮层',
    description: '对话框：点击打开，按 Esc 或关闭按钮返回。',
    preview: (
      <Dialog>
        <DialogTrigger asChild>
          <Button variant="outline">打开示例对话框</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>示例对话框</DialogTitle>
            <DialogDescription>这是独立的预览，不会修改任何设置。</DialogDescription>
          </DialogHeader>
        </DialogContent>
      </Dialog>
    ),
  },
  {
    name: 'Popover',
    file: 'popover',
    category: '浮层',
    description: '气泡面板：锚定触发按钮的补充内容。',
    preview: (
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="outline">打开气泡</Button>
        </PopoverTrigger>
        <PopoverContent>气泡中的示例内容。</PopoverContent>
      </Popover>
    ),
  },
  {
    name: 'Tooltip',
    file: 'tooltip',
    category: '浮层',
    description: '工具提示：悬停或键盘聚焦查看说明。',
    preview: (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="outline">悬停查看提示</Button>
          </TooltipTrigger>
          <TooltipContent>这是工具提示。</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    ),
  },
]

const allExamples = [...examples, ...formExamples, ...layoutExamples, ...feedbackExamples].sort(
  (a, b) => a.file.localeCompare(b.file)
)

const categories = ['基础', '表单', '布局', '浮层', '反馈', '图表', '图标', '无障碍', '辅助模块']

export default function ComponentGallery({ heading }: { heading?: ReactNode }) {
  const { themeConfig, updateThemeConfig } = useTheme()
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('全部')
  const [showInfo, setShowInfo] = useState(false)
  const normalizedQuery = query.trim().toLowerCase()
  const filtered = allExamples.filter(
    (example) =>
      (category === '全部' || example.category === category) &&
      `${example.name} ${example.description} ${example.file}`
        .toLowerCase()
        .includes(normalizedQuery)
  )

  const groups = categories
    .map((name) => ({ name, examples: filtered.filter((example) => example.category === name) }))
    .filter((group) => group.examples.length > 0)

  return (
    <ComponentGalleryCanvas
      resetKey={`${normalizedQuery}:${category}`}
      toolbar={
        <>
          {heading}
          <div role="group" aria-label="界面风格" className="flex items-center gap-1">
            {(
              [
                { value: 'modern', label: '现代' },
                { value: 'future-retro', label: '未来复古' },
                { value: 'millennium', label: '千禧' },
              ] as const
            ).map((option) => (
              <Button
                key={option.value}
                size="sm"
                variant={themeConfig.dashboardStyle === option.value ? 'default' : 'outline'}
                aria-pressed={themeConfig.dashboardStyle === option.value}
                onClick={() => updateThemeConfig({ dashboardStyle: option.value })}
              >
                {option.label}
              </Button>
            ))}
          </div>
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="搜索公共组件"
            placeholder="搜索组件…"
            controlSize="sm"
            className="w-40 shrink-0"
          />
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger
              className="h-[var(--control-height-sm)] w-28 shrink-0"
              aria-label="组件分类"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {['全部', ...categories].map((value) => (
                <SelectItem key={value} value={value}>
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <SwitchField
            label="显示信息"
            controlSize="sm"
            checked={showInfo}
            onCheckedChange={setShowInfo}
          />
          <span
            className="text-muted-foreground text-xs tabular-nums"
            role="status"
            aria-label="组件数量"
          >
            {filtered.length}/{allExamples.length}
          </span>
          {filtered.length === 0 && (
            <>
              <span className="text-muted-foreground text-xs">没有找到匹配的组件</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setQuery('')
                  setCategory('全部')
                }}
              >
                清除筛选
              </Button>
            </>
          )}
        </>
      }
    >
      {groups.map((group) => (
        <section key={group.name} aria-label={`${group.name}组件`} className="w-max">
          <h2 className="mb-4 flex items-baseline gap-2 border-b pb-2 text-base font-semibold">
            {group.name}
            <span className="text-muted-foreground text-xs font-normal tabular-nums">
              {group.examples.length}
            </span>
          </h2>
          <div
            className="gap-6"
            style={{
              columnCount: Math.min(4, group.examples.length),
              width: Math.min(4, group.examples.length) * 444 - 24,
            }}
          >
            {group.examples.map((example) => (
              <section
                key={example.file}
                data-gallery-item
                aria-label={example.name}
                className="mb-4 inline-block w-full break-inside-avoid align-top"
              >
                {showInfo && (
                  <header className="mb-3 space-y-1 border-b pb-2">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-sm font-semibold break-words">{example.name}</h3>
                      <span className="text-muted-foreground shrink-0 text-xs">
                        {example.category}
                      </span>
                    </div>
                    <p className="text-muted-foreground text-sm">{example.description}</p>
                    <code className="text-muted-foreground text-xs break-all">
                      @/components/ui/{example.file}
                    </code>
                  </header>
                )}
                <div>{example.preview}</div>
              </section>
            ))}
          </div>
        </section>
      ))}
    </ComponentGalleryCanvas>
  )
}
