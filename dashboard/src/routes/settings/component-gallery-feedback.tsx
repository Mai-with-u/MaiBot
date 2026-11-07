/* eslint-disable react-refresh/only-export-components -- 本文件导出示例目录，内部组件随目录整体重新加载。 */
import { useState } from 'react'
import { Icon } from '@iconify/react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { AnnouncerProvider } from '@/components/ui/announcer'
import { useAnnounce } from '@/components/ui/announcer-context'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from '@/components/ui/command'
import { controlSizeClassNames } from '@/components/ui/control-size'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { FloatingPanel } from '@/components/ui/floating-panel'
import { HelpTooltip } from '@/components/ui/help-tooltip'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { Input } from '@/components/ui/input'
import { Kbd, ShortcutKbd } from '@/components/ui/kbd'
import { MILLENNIUM_ICON_SET } from '@/components/ui/millennium-icon-set'
import { MillenniumIcon } from '@/components/ui/millennium-icons'
import { PromptWarningDot } from '@/components/ui/prompt-warning-dot'
import { SegmentedMeter } from '@/components/ui/segmented-meter'
import { SkipNav } from '@/components/ui/skip-nav'
import { StepIndicator } from '@/components/ui/step-indicator'
import { StreamlineIcon } from '@/components/ui/streamline-icon'
import { streamlineIcons } from '@/components/ui/streamline-icons'
import { createStreamlineIcon } from '@/components/ui/streamline-menu-icon'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import {
  Toast,
  ToastAction,
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastTitle,
  ToastViewport,
} from '@/components/ui/toast'
import { toast } from '@/hooks/use-toast'

import type { ComponentExample } from './component-gallery-types'

function ConfirmExample() {
  const [confirmed, setConfirmed] = useState(false)
  return (
    <div className="space-y-2">
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button variant="outline">打开确认框</Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认示例操作？</AlertDialogTitle>
            <AlertDialogDescription>此操作只更新下面的预览文字。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => setConfirmed(true)}>确认</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <p role="status">{confirmed ? '已确认示例操作' : '等待确认'}</p>
    </div>
  )
}

function AnnounceButton() {
  const announce = useAnnounce()
  const [count, setCount] = useState(0)
  return (
    <div className="space-y-2">
      <Button
        variant="outline"
        onClick={() => {
          setCount(count + 1)
          announce(`示例播报 ${count + 1}`)
        }}
      >
        向屏幕阅读器播报
      </Button>
      <p className="text-sm">已触发 {count} 次播报。实际播报区域在视觉上隐藏。</p>
    </div>
  )
}

function CommandExample() {
  const [selected, setSelected] = useState('尚未选择')
  return (
    <div className="space-y-2">
      <Command>
        <CommandInput placeholder="搜索示例命令…" />
        <CommandList>
          <CommandEmpty>没有匹配的命令</CommandEmpty>
          <CommandGroup heading="示例">
            <CommandItem onSelect={() => setSelected('打开')}>
              打开<CommandShortcut>↵</CommandShortcut>
            </CommandItem>
            <CommandItem onSelect={() => setSelected('保存')}>保存</CommandItem>
          </CommandGroup>
          <CommandSeparator />
          <CommandGroup heading="其他">
            <CommandItem disabled>不可用</CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
      <p className="text-sm">选择：{selected}</p>
    </div>
  )
}

function DropdownExample() {
  const [checked, setChecked] = useState(true)
  const [selected, setSelected] = useState('未选择')
  return (
    <div className="space-y-2">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline">展开菜单</Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>示例操作</DropdownMenuLabel>
          <DropdownMenuItem onSelect={() => setSelected('查看详情')}>查看详情</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuCheckboxItem checked={checked} onCheckedChange={setChecked}>
            显示提示
          </DropdownMenuCheckboxItem>
          <DropdownMenuItem disabled>禁用操作</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <p className="text-sm">
        {selected} · 提示{checked ? '开启' : '关闭'}
      </p>
    </div>
  )
}

function FloatingExample() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        打开浮动面板
      </Button>
      {open && (
        <FloatingPanel
          title="示例浮动面板"
          subtitle="可以拖动标题栏移动"
          onClose={() => setOpen(false)}
          initialWidth={360}
          initialHeight={260}
          mobileFullscreen
        >
          <p className="p-4">面板内容仅用于预览。</p>
        </FloatingPanel>
      )}
    </>
  )
}

function ToastExample() {
  const [open, setOpen] = useState(false)
  return (
    <ToastProvider>
      <Button variant="outline" onClick={() => setOpen(true)}>
        显示通知示例
      </Button>
      <Toast open={open} onOpenChange={setOpen} duration={4000}>
        <div>
          <ToastTitle>示例通知</ToastTitle>
          <ToastDescription>四秒后自动关闭，也可手动关闭。</ToastDescription>
        </div>
        <ToastAction altText="知道了">知道了</ToastAction>
        <ToastClose />
      </Toast>
      <ToastViewport />
    </ToastProvider>
  )
}

function StepExample() {
  const [step, setStep] = useState(1)
  return (
    <div className="space-y-4">
      <StepIndicator
        steps={[
          { id: 'one', label: '准备' },
          { id: 'two', label: '处理' },
          { id: 'three', label: '完成' },
        ]}
        currentStep={step}
        label="示例步骤"
      />
      <div className="flex gap-2">
        <Button variant="outline" disabled={step === 0} onClick={() => setStep(step - 1)}>
          上一步
        </Button>
        <Button variant="outline" disabled={step === 2} onClick={() => setStep(step + 1)}>
          下一步
        </Button>
      </div>
    </div>
  )
}

const MenuIcon = createStreamlineIcon('desktop-chat-remix')

function MillenniumIconsExample() {
  return (
    <div className="flex max-h-64 flex-wrap gap-3 overflow-auto">
      {MILLENNIUM_ICON_SET.map((icon, index) => (
        <div
          key={index}
          title={[...(icon.streamline ?? []), ...(icon.lucide ?? [])].join(', ')}
          className="rounded border p-2"
        >
          <MillenniumIcon icon={icon} size={24} />
        </div>
      ))}
    </div>
  )
}

function StreamlineIconsExample() {
  return (
    <div className="flex max-h-64 flex-wrap gap-3 overflow-auto">
      {Object.entries(streamlineIcons).flatMap(([collection, icons]) =>
        Object.entries(icons).map(([name, icon]) => (
          <div
            key={`${collection}:${name}`}
            title={`${collection}:${name}`}
            className="rounded border p-2"
          >
            <Icon icon={icon} width={24} height={24} />
          </div>
        ))
      )}
    </div>
  )
}

export const feedbackExamples: ComponentExample[] = [
  {
    file: 'alert-dialog',
    name: 'AlertDialog',
    category: '浮层',
    description: '需要确认或取消的操作对话框。',
    preview: <ConfirmExample />,
  },
  {
    file: 'announcer',
    name: 'AnnouncerProvider',
    category: '无障碍',
    description: '为屏幕阅读器提供独立的 aria-live 播报区域。',
    preview: (
      <AnnouncerProvider>
        <AnnounceButton />
      </AnnouncerProvider>
    ),
  },
  {
    file: 'announcer-context',
    name: 'AnnouncerContext / useAnnounce',
    category: '辅助模块',
    description: '播报上下文和 Hook，需要在 AnnouncerProvider 内调用。',
    preview: (
      <AnnouncerProvider>
        <AnnounceButton />
      </AnnouncerProvider>
    ),
  },
  {
    file: 'command',
    name: 'Command / CommandDialog / CommandInput / CommandList / CommandGroup / CommandItem',
    category: '表单',
    description: '支持键盘导航和搜索的命令列表；CommandDialog 用于弹窗承载。',
    preview: <CommandExample />,
  },
  {
    file: 'control-size',
    name: 'ControlSize / controlSizeClassNames',
    category: '辅助模块',
    description: '控件共用的 xs、sm、default、lg 高度档位。',
    preview: (
      <div className="space-y-2">
        {Object.entries(controlSizeClassNames).map(([size, className]) => (
          <Input
            key={size}
            className={className}
            aria-label={`${size} 尺寸示例`}
            placeholder={size}
          />
        ))}
      </div>
    ),
  },
  {
    file: 'dropdown-menu',
    name: 'DropdownMenu',
    category: '浮层',
    description: '操作菜单、复选项、分隔线与禁用状态。',
    preview: <DropdownExample />,
  },
  {
    file: 'floating-panel',
    name: 'FloatingPanel',
    category: '浮层',
    description: '可拖动浮动窗口，移动端铺满屏幕。',
    preview: <FloatingExample />,
  },
  {
    file: 'help-tooltip',
    name: 'HelpTooltip',
    category: '反馈',
    description: '带帮助图标的悬停说明。',
    preview: <HelpTooltip content="这里是该选项的详细帮助说明。" />,
  },
  {
    file: 'info-tooltip',
    name: 'InfoTooltip',
    category: '反馈',
    description: '可聚焦的信息图标与提示。',
    preview: <InfoTooltip label="示例信息" content="这里是补充信息。" />,
  },
  {
    file: 'kbd',
    name: 'Kbd / ShortcutKbd',
    category: '基础',
    description: '键帽和适配当前平台的快捷键显示。',
    preview: (
      <div className="flex gap-3">
        <Kbd>Esc</Kbd>
        <ShortcutKbd keys={['mod', 'k']} />
      </div>
    ),
  },
  {
    file: 'millennium-icon-set',
    name: 'MILLENNIUM_ICON_SET / getMillenniumIcon',
    category: '辅助模块',
    description: '千禧图标路径与名称映射。悬停查看图标名称，源码包含样式生成工具。',
    preview: <MillenniumIconsExample />,
  },
  {
    file: 'millennium-icons',
    name: 'MillenniumIcon',
    category: '图标',
    description: '本地 SVG 千禧图标渲染器。悬停查看名称。',
    preview: <MillenniumIconsExample />,
  },
  {
    file: 'prompt-warning-dot',
    name: 'PromptWarningDot',
    category: '反馈',
    description: '提示词模板异常的警告点，悬停查看说明。',
    preview: (
      <span className="flex items-center gap-2">
        <PromptWarningDot message="示例：模板缺少必填变量" />
        模板警告示例
      </span>
    ),
  },
  {
    file: 'segmented-meter',
    name: 'SegmentedMeter',
    category: '图表',
    description: '展示多个类别占比的分段计量条。',
    preview: (
      <SegmentedMeter
        label="任务分布"
        segments={[
          { label: '已完成', value: 60, className: 'bg-green-500' },
          { label: '处理中', value: 25, className: 'bg-blue-500' },
          { label: '失败', value: 15, className: 'bg-red-500' },
        ]}
      />
    ),
  },
  {
    file: 'skip-nav',
    name: 'SkipNav',
    category: '无障碍',
    description: '跳过导航直达主内容。聚焦下面按钮后按 Tab，查看页面左上角出现的跳转链接。',
    preview: (
      <div>
        <Button variant="outline">从这里开始按 Tab</Button>
        <SkipNav />
      </div>
    ),
  },
  {
    file: 'step-indicator',
    name: 'StepIndicator',
    category: '反馈',
    description: '已完成、当前与未开始步骤的进度指示。',
    preview: <StepExample />,
  },
  {
    file: 'streamline-icon',
    name: 'StreamlineIcon',
    category: '图标',
    description: '根据当前界面风格选择图标。',
    preview: (
      <div className="flex gap-4">
        <StreamlineIcon name="desktop-chat-remix" size={28} />
        <StreamlineIcon name="application-add-remix" size={28} />
      </div>
    ),
  },
  {
    file: 'streamline-icons',
    name: 'streamlineIcons / getStreamlineIcon',
    category: '辅助模块',
    description: '本地 Streamline 图标集合。悬停查看集合和名称。',
    preview: <StreamlineIconsExample />,
  },
  {
    file: 'streamline-menu-icon',
    name: 'createStreamlineIcon',
    category: '图标',
    description: '将图标名称封装为菜单使用的 React 组件。',
    preview: <MenuIcon size={28} />,
  },
  {
    file: 'thinking-illustration',
    name: 'ThinkingIllustration',
    category: '反馈',
    description: '思考中的插画与尺寸变体。',
    preview: (
      <div className="flex items-center gap-3">
        <ThinkingIllustration size="sm" />
        <ThinkingIllustration size="md" />
      </div>
    ),
  },
  {
    file: 'toast',
    name: 'Toast / ToastProvider / ToastViewport / ToastAction',
    category: '反馈',
    description: '通知基础组件：标题、说明、动作、关闭按钮和进度。',
    preview: <ToastExample />,
  },
  // 应用外壳已经挂载 Toaster；复用该出口，避免重复挂载导致通知显示两次。
  {
    file: 'toaster',
    name: 'Toaster',
    category: '反馈',
    description: '应用级通知出口，按钮通过 useToast 的同一通知队列展示示例。',
    preview: (
      <Button
        variant="outline"
        onClick={() =>
          toast({
            title: '组件库示例',
            description: '这是一条来自应用通知出口的预览通知。',
            duration: 4000,
          })
        }
      >
        显示应用通知
      </Button>
    ),
  },
]
