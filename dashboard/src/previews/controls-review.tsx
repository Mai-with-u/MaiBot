import { TimeRangeTrack, TimeRangeTicks } from '@/components/ui/time-range-track'
import { SegmentedMeter } from '@/components/ui/segmented-meter'
import { SegmentedControl } from '@/components/ui/management'
import { formatTalkTimeRange, getTimelineSegments } from '@/lib/time-range'
import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowLeft, Copy, Hand, History, Maximize, Minus, Moon, MousePointer2, Palette, Plus, Sun, ThumbsUp, Trash2, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { StatusLight } from '@/components/ui/status-light'
import { StatusIndicator } from '@/components/ui/data-browser'
import { ChatScopeFilterPanel } from '@/components/chat-scope-filter-panel'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { ConversationAvatar, MessageBubble } from '@/components/ui/conversation'
import { Input } from '@/components/ui/input'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { SplitDropdownSelect } from '@/components/ui/split-dropdown-select'
import { Slider } from '@/components/ui/slider'
import { DiscreteSlider } from '@/components/ui/discrete-slider'
import { Switch, SwitchField } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { buildFutureRetroTexture } from '@/lib/theme/future-retro'
import { applyMillenniumFonts } from '@/lib/theme/millennium-font'
import { applyThemePipeline } from '@/lib/theme/pipeline'
import { DEFAULT_DASHBOARD_STYLE_CONFIG, DEFAULT_FUTURE_RETRO_STYLE_CONFIG } from '@/lib/theme/tokens'
import type { DashboardStyle } from '@/lib/theme/tokens'

import '@/index.css'
import '@/styles/controls.css'
import '@/styles/millennium.css'
import '@/styles/modern.css'
import '@/styles/mobile.css'
import '@/styles/surfaces.css'
import '@/styles/data-browser.css'
import '@/styles/workspace.css'
import '@/styles/management.css'
import '@/styles/conversation.css'
import './controls-review.css'
import { ExpressionReview } from './expression-review'
import { JargonReview } from './jargon-review'
import { DataTransferReview } from './data-transfer-review'
import { AdaptersReview } from './adapters-review'
import { EmojiReview } from './emoji-review'
import { ChatReview, ThinkingReview } from './conversation-review'
import { ReviewCanvas } from './review-canvas'
import { PluginPagesReview } from './plugin-pages-review'
import { SetupReview } from './setup-review'
import { OrderControls } from '@/components/ui/order-controls'

// 独立的 Vite 开发入口，不加入生产路由，也不读写用户的主题偏好或业务数据。
const styles: { id: DashboardStyle; label: string }[] = [
  { id: 'modern', label: '现代' },
  { id: 'future-retro', label: '未来复古' },
  { id: 'millennium', label: '千禧' },
]
const params = new URLSearchParams(window.location.search)
const selectedStyle = styles.find((style) => style.id === params.get('style'))
const isDark = params.get('mode') === 'dark'
const embedded = params.get('embedded') === '1'
document.documentElement.dataset.reviewEmbedded = String(embedded)
const previewPage = params.get('page')
const pagePreviews = {
  expression: ExpressionReview,
  jargon: JargonReview,
  transfer: DataTransferReview,
  adapters: AdaptersReview,
  emoji: EmojiReview,
  chat: ChatReview,
  thinking: ThinkingReview,
  'plugin-pages': PluginPagesReview,
  setup: SetupReview,
}
const activePreviewPage = previewPage && Object.hasOwn(pagePreviews, previewPage)
  ? previewPage as keyof typeof pagePreviews
  : null
function applyPreviewTheme(style: DashboardStyle, dark: boolean) {
  const root = document.documentElement
  root.classList.toggle('dark', dark)
  root.classList.toggle('light', !dark)
  root.dataset.dashboardStyle = style
  applyMillenniumFonts('departure', 'default')
  const retro = DEFAULT_FUTURE_RETRO_STYLE_CONFIG
  root.dataset.retroTextureStyle = retro.textureStyle
  root.style.setProperty('--retro-paper-warmth', `${retro.paperWarmth}%`)
  root.style.setProperty('--retro-panel-depth', String(retro.panelDepth / 100))
  root.style.setProperty('--retro-configured-paper-texture', buildFutureRetroTexture(retro.textureStyle, retro.textureIntensity, dark))
  root.style.setProperty('--retro-paper-texture-size', '220px 220px')
  applyThemePipeline({
    selectedPreset: dark ? 'dark' : 'light',
    accentColor: '',
    styleTokenOverrides: {},
    styleCustomCSS: {},
    dashboardStyle: style,
    styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
  }, dark)
}

function Samples({ label, dark }: { label: string; dark: boolean }) {
  const [range, setRange] = useState({start: 18 * 60, end: 6 * 60})
  const [period, setPeriod] = useState('7')
  const [liked, setLiked] = useState(true)
  const [stream, setStream] = useState<string | number>('friends')
  const [scope, setScope] = useState('all')
  const [quoteFrequency, setQuoteFrequency] = useState('occasionally')
  const [driftLevel, setDriftLevel] = useState('active')
  const [configVersion, setConfigVersion] = useState('default')
  const [configAction, setConfigAction] = useState('可切换配置或点击左侧操作')
  return (
    <main className="control-samples">
      <h1>全部控件 <small>{label} · {dark ? '夜间' : '日间'}</small></h1>
      <section>
        <h2>按钮 · 普通 / 选中 / 禁用</h2>
        <div className="sample-row">
          <Button><Plus />新增</Button>
          <Button variant="outline">普通按钮</Button>
          <Button variant="outline" aria-pressed={liked} onClick={() => setLiked(!liked)}><ThumbsUp />{liked ? '已点赞' : '点赞'}</Button>
          <Button variant="destructive">删除</Button>
          <Button variant="destructive" size="icon" aria-label="删除" title="删除"><Trash2 /></Button>
          <Button variant="destructive" size="icon-sm" aria-label="删除（小号）" title="删除（小号）"><Trash2 /></Button>
          <Button variant="destructive" size="icon" disabled aria-label="删除（不可用）" title="删除（不可用）"><Trash2 /></Button>
          <Button variant="outline" size="icon" aria-label="返回"><ArrowLeft /></Button>
          <Button disabled>不可用</Button>
          <OrderControls label="排序示例" index={liked ? 0 : 1} count={2} onMove={() => setLiked(!liked)} upLabel="上移" downLabel="下移" />
        </div>
      </section>
      <section>
        <h2>Tab 切换 · 标准 / 紧凑</h2>
        <div className="sample-row">
          <Tabs defaultValue="models">
            <TabsList><TabsTrigger value="models">模型设置</TabsTrigger><TabsTrigger value="tasks">功能分配</TabsTrigger><TabsTrigger value="disabled" disabled>不可用</TabsTrigger></TabsList>
            <TabsContent value="models">当前：模型设置</TabsContent><TabsContent value="tasks">当前：功能分配</TabsContent>
          </Tabs>
          <Tabs defaultValue="readme">
            <TabsList><TabsTrigger size="sm" value="readme">README</TabsTrigger><TabsTrigger size="sm" value="changes">更新日志</TabsTrigger></TabsList>
            <TabsContent value="readme">当前：README</TabsContent><TabsContent value="changes">当前：更新日志</TabsContent>
          </Tabs>
        </div>
      </section>
      <section>
        <h2>Switch 开关 · 关闭 / 开启 / 禁用 / 大尺寸</h2>
        <div className="sample-row">
          <label>关闭 <Switch aria-label="默认关闭" /></label>
          <label>开启 <Switch aria-label="默认开启" defaultChecked /></label>
          <label>禁用 <Switch aria-label="禁用开关" disabled /></label>
          <label>禁用开启 <Switch aria-label="禁用开启" defaultChecked disabled /></label>
          <label>仅显示兼容 <Switch aria-label="仅显示兼容" size="lg" defaultChecked /></label>
        </div>
      </section>
      <section>
        <h2>开关字段 · 与下拉框、按钮等高 / 开启 / 禁用</h2>
        {/* 工具栏里的开关带上与下拉框同款外框，不用 sample-row 的 label 样式覆盖组件间距。 */}
        <div className="flex flex-wrap items-center gap-2">
          <Select defaultValue="time"><SelectTrigger controlSize="sm" className="w-[130px]" aria-label="排序"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="time">最近活跃</SelectItem><SelectItem value="created">最新创建</SelectItem></SelectContent></Select>
          <SwitchField controlSize="sm" label="仅精选" />
          <SwitchField controlSize="sm" label="已开启" defaultChecked />
          <SwitchField controlSize="sm" label="不可用" disabled />
          <Button size="sm"><Plus />新增</Button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Input aria-label="搜索" placeholder="标准高度" className="w-48" />
          <SwitchField label="显示旧格式" defaultChecked />
          <Button variant="outline">普通按钮</Button>
        </div>
      </section>
      <section>
        <h2>输入框与下拉框 · 同档高度 / 禁用</h2>
        <div className="sample-fields">
          <Input aria-label="模型名称" defaultValue="模型名称 MaiBot" />
          <Select defaultValue="random"><SelectTrigger aria-label="选择策略"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="random">随机选择</SelectItem><SelectItem value="sequence">顺序选择</SelectItem></SelectContent></Select>
          <Input aria-label="禁用输入" disabled value="不可编辑" readOnly />
          <Input aria-label="最大 Token" controlSize="sm" defaultValue="8192" />
          <Select defaultValue="small"><SelectTrigger controlSize="sm" aria-label="紧凑下拉"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="small">紧凑下拉</SelectItem></SelectContent></Select>
          <Button size="sm" variant="outline">紧凑按钮</Button>
        </div>
      </section>
      <section id="split-dropdown-select">
        <h2>左右布局下拉菜单 · 内凹入口 / 操作按钮 / 配置列表</h2>
        <div className="sample-fields">
          <SplitDropdownSelect
            label="配置副本示例"
            value={configVersion}
            onValueChange={setConfigVersion}
            icon={<History />}
            options={[
              { value: 'default', label: '默认配置' },
              { value: 'daily', label: '日常聊天', description: '2026-10-19 10:00' },
              { value: 'reasoning', label: '深度思考', description: '2026-10-18 18:30' },
              { value: 'disabled', label: '不可用配置', disabled: true },
            ]}
            actions={[
              { id: 'save', label: '保存副本', icon: <Copy />, onSelect: () => setConfigAction('已点击保存副本') },
              { id: 'manage', label: '管理副本', icon: <History />, onSelect: () => setConfigAction('已点击管理副本') },
            ]}
          />
          <SplitDropdownSelect label="禁用的配置菜单" value="default" onValueChange={setConfigVersion} options={[{ value: 'default', label: '不可用' }]} actions={[]} disabled />
        </div>
        <p role="status" className="text-sm text-muted-foreground">{configAction}</p>
      </section>
      <section>
        <h2>拖动条 · 普通 / 数值 / 禁用</h2>
        <div className="sample-sliders">
          <Slider aria-label="普通拖动条" defaultValue={[40]} />
          <Slider aria-label="温度" variant="value" valueFormat="fixed-1" defaultValue={[0.8]} min={0} max={2} step={0.1} />
          <Slider aria-label="禁用拖动条" variant="value" defaultValue={[40]} disabled />
        </div>
      </section>
      <section>
        <h2>档位滑杆 · 三档 / 四档 / 禁用</h2>
        <div className="sample-sliders">
          <DiscreteSlider label="群聊引用频率" value={quoteFrequency} onValueChange={setQuoteFrequency}
            options={[{ value: 'never', label: '不引用' }, { value: 'occasionally', label: '偶尔引用' }, { value: 'often', label: '经常引用' }]} />
          <DiscreteSlider label="漂移档位" value={driftLevel} onValueChange={setDriftLevel}
            options={[{ value: 'subtle', label: '轻微漂移' }, { value: 'active', label: '活跃联想' }, { value: 'scattered', label: '明显发散' }, { value: 'wild', label: '强烈跳跃' }]} />
          <DiscreteSlider label="禁用档位" value="medium" onValueChange={() => {}} disabled
            options={[{ value: 'low', label: '低' }, { value: 'medium', label: '中' }, { value: 'high', label: '高' }]} />
        </div>
      </section>
      <p className="sample-note">可点击操作；按 Tab 检查焦点，方向键调整拖动条。所有控件均来自公共组件。</p>
      <section id="status-lights">
        <h2>指示灯 · 柔和辉光 / 状态 / 尺寸</h2>
        <div className="sample-row">
          <span className="inline-flex items-center gap-3 text-xl"><StatusLight status="on" size="lg" decorative />运行中 <span className="tabular-nums">3</span></span>
          <StatusIndicator status="mixed" variant="inline">处理中</StatusIndicator>
          <StatusIndicator status="error" variant="inline">异常</StatusIndicator>
          <StatusIndicator status="off" variant="inline">未运行</StatusIndicator>
        </div>
        <div className="sample-row mt-5">
          <span className="inline-flex items-center gap-2"><StatusLight status="on" size="sm" decorative />小</span>
          <span className="inline-flex items-center gap-2"><StatusLight status="on" size="md" decorative />中</span>
          <span className="inline-flex items-center gap-2"><StatusLight status="on" size="lg" decorative />大</span>
          <span className="inline-flex items-center gap-2"><StatusLight status="on" glow={false} decorative />无辉光</span>
          <StatusIndicator status="on">学习中</StatusIndicator>
        </div>
      </section>
      <section id="additional-controls" className="additional-controls">
        <h2>复选框 · 未选 / 全选 / 半选 / 禁用 / 紧凑</h2>
        <div className="sample-row">
          <label><Checkbox aria-label="未选复选框" />未选</label>
          <label><Checkbox aria-label="已选复选框" defaultChecked />已选</label>
          <label><Checkbox aria-label="半选复选框" defaultChecked="indeterminate" />部分选中</label>
          <label><Checkbox aria-label="禁用复选框" defaultChecked disabled />禁用</label>
          <label><Checkbox aria-label="紧凑复选框" size="sm" defaultChecked />紧凑</label>
        </div>
      </section>
      <section>
        <h2>单选框 · 标准 / 禁用 / 紧凑</h2>
        <RadioGroup aria-label="执行方式" defaultValue="auto" className="sample-row">
          <label><RadioGroupItem value="auto" />自动</label>
          <label><RadioGroupItem value="manual" />手动</label>
          <label><RadioGroupItem value="disabled" disabled />不可用</label>
        </RadioGroup>
        <RadioGroup aria-label="紧凑执行方式" defaultValue="auto" className="sample-row sample-radio-compact">
          <label><RadioGroupItem size="sm" value="auto" />自动（紧凑）</label>
          <label><RadioGroupItem size="sm" value="manual" />手动（紧凑）</label>
        </RadioGroup>
      </section>
      <section>
        <h2>多行输入框 · 自动高度 / 只读 / 禁用</h2>
        <div className="sample-fields">
          <Textarea aria-label="自动高度说明" defaultValue={'可编辑说明\n增加或删除内容，高度随之调整。'} maxHeight={160} />
          <Textarea aria-label="只读说明" value="只读说明，可选择复制。" readOnly />
          <Textarea aria-label="禁用说明" value="暂不可编辑。" disabled readOnly />
        </div>
      </section>
      <section id="surface-controls" className="additional-controls">
        <h2>卡片 · 普通 / 内嵌</h2>
        <Card>
          <CardHeader><CardTitle>运行概况</CardTitle></CardHeader>
          <CardContent>
            <Card variant="flat" className="p-3">内嵌说明：共用卡片组件，通过档位选择外观。</Card>
          </CardContent>
        </Card>
      </section>
      <section>
        <h2>聊天流列表 / 页面窗口 / 数据框</h2>
        <div className="sample-surfaces">
          <ChatScopeFilterPanel title="聊天范围" modes={[{label:'全部',value:'all'},{label:'群聊',value:'group'}]} activeMode={scope} onModeChange={setScope} selectedItemId={stream} onItemSelect={setStream} items={[{id:'friends',label:'麦麦交流群',description:'群聊 · 最近有消息'},{id:'private',label:'小明的私聊',description:'私聊 · 昨天'},{id:'disabled',label:'暂不可用',disabled:true}]} footer={<span className="text-xs">当前选择：{stream === 'friends' ? '麦麦交流群' : '小明的私聊'}</span>} />
          <PageFrame className="p-4 space-y-4" aria-label="数据浏览窗口">
            <div>数据浏览</div>
            <Separator variant="groove" />
            <DataPanel className="sample-metrics">
              <span>总数量 <strong>35903</strong></span>
              <Separator orientation="vertical" />
              <span>新增 <strong>5586</strong></span>
            </DataPanel>
            <p className="text-sm">外层窗口和内层数据屏分别复用，各页面只安排内容。</p>
          </PageFrame>
        </div>
      </section>
      <section id="conversation-controls">
        <h2>聊天头像 / 消息气泡 · 列表、聊天与观察共用</h2>
        <div className="sample-row mb-4">
          <ConversationAvatar name="麦麦" bot />
          <ConversationAvatar name="小明" />
          <ConversationAvatar name="交流群" />
        </div>
        <DataPanel className="space-y-4">
          <MessageBubble name="麦麦" time="15:29" avatar={<ConversationAvatar name="麦麦" bot />}>
            你到底说不说
          </MessageBubble>
          <MessageBubble name="小明" time="15:30" outgoing avatar={<ConversationAvatar name="小明" />}>
            头像和气泡使用主题的公共描边。
          </MessageBubble>
        </DataPanel>
      </section>
      <section>
        <h2>进度条 · 0% / 50% / 完成 / 等待进度</h2>
        <div className="sample-progress">
          <Progress aria-label="尚未开始" value={0} />
          <Progress aria-label="完成一半" value={25} max={50} />
          <Progress aria-label="已完成" value={100} />
          <Progress aria-label="正在准备" value={null} />
        </div>
      </section>
      <section id="shared-interactions">
        <h2>公共交互 · 时间范围 / 状态条 / 选项切换</h2>
        <p>拖动两端或聚焦后按方向键调整：{formatTalkTimeRange(range)}</p>
        <TimeRangeTicks />
        <TimeRangeTrack label="预览" range={range} segments={getTimelineSegments(range)} colorClassName="bg-emerald-500/50" onChange={setRange} />
        <div className="sample-row mt-4"><SegmentedControl aria-label="统计范围" value={period} onValueChange={setPeriod} options={[{value: '1', label: '今天'}, {value: '7', label: '7 天'}, {value: '30', label: '30 天'}]} /></div>
        <p className="mt-4">插件状态：启用 6 · 禁用 2 · 失败 1</p>
        <SegmentedMeter label="插件状态" separated segments={[{label: '启用', value: 6, className: 'bg-emerald-500'}, {label: '禁用', value: 2, className: 'bg-gray-400'}, {label: '失败', value: 1, className: 'bg-red-500'}]} />
      </section>
      <section>
        <h2>分割线 · 普通线 / 下刻线 / 竖向下刻线</h2>
        <div className="sample-dividers"><Separator /><Separator variant="groove" /><Separator variant="groove" orientation="vertical" /></div>
      </section>
    </main>
  )
}

function Overview() {
  return (
    <main className="control-overview">
      <h1>基础控件 · 三种主题 × 日间 / 夜间</h1>
      <p>点击标题打开完整尺寸预览。每组均可操作，使用实际主题样式，不修改当前 WebUI 设置。</p>
      <div className="comparison-grid">
        {(['light', 'dark'] as const).flatMap((mode) => styles.map((style) => {
          const url = `?style=${style.id}&mode=${mode}`
          const title = `${style.label} · ${mode === 'dark' ? '夜间' : '日间'}`
          return <section key={`${style.id}-${mode}`}><a href={url}>{title} ↗</a><div className="comparison-frame"><iframe title={title} src={`${url}&embedded=1`} /></div></section>
        }))}
      </div>
    </main>
  )
}

const previewNames = {
  all: '全部控件', comparison: '六种主题对照', expression: '表达方式', jargon: '黑话',
  transfer: '导入导出', adapters: '适配器', emoji: '表情包', chat: '麦麦聊天', thinking: '聊天观察',
  'plugin-pages': '插件页面设置',
  setup: '首次引导',
}
type PreviewView = keyof typeof previewNames

function ReviewPage() {
  const [style, setStyle] = useState<DashboardStyle>(selectedStyle?.id ?? 'millennium')
  const [dark, setDark] = useState(isDark)
  const [view, setView] = useState<PreviewView>(params.get('page') === 'comparison' ? 'comparison' : activePreviewPage ?? 'all')
  const [themeOpen, setThemeOpen] = useState(false)
  const [panning, setPanning] = useState(false)
  const [zoom, setZoom] = useState(100)
  const [command, setCommand] = useState<{ action: 'fit' | 'in' | 'out' | 'reset'; revision: number }>({ action: 'fit', revision: 0 })
  const commandCanvas = (action: typeof command.action) => setCommand(previous => ({ action, revision: previous.revision + 1 }))
  useEffect(() => {
    applyPreviewTheme(style, dark)
    const url = new URL(window.location.href)
    url.searchParams.set('style', style)
    url.searchParams.set('mode', dark ? 'dark' : 'light')
    url.searchParams.set('page', view)
    window.history.replaceState(null, '', url)
  }, [style, dark, view])
  const Preview = view in pagePreviews ? pagePreviews[view as keyof typeof pagePreviews] : null
  const content = Preview ? <Preview /> : view === 'comparison' ? <Overview /> : <Samples label={styles.find(item => item.id === style)!.label} dark={dark} />
  if (embedded) return content
  return (
    <div className="review-studio">
      <nav className="review-toolbar" aria-label="预览浏览工具">
        <strong>控件工坊</strong>
        <select aria-label="浏览视图" value={view} onChange={event => setView(event.target.value as PreviewView)}>
          {Object.entries(previewNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <button aria-pressed={view === 'all'} onClick={() => setView('all')}>全部控件</button>
        <div className="review-toolbar-spacer" />
        <button aria-label="拖动画布" title="拖动画布（H）" aria-pressed={panning} onClick={() => setPanning(true)}><Hand size={18} /></button>
        <button aria-label="操作控件" title="操作控件（V）；按住空格临时拖移" aria-pressed={!panning} onClick={() => setPanning(false)}><MousePointer2 size={18} /></button>
        <button aria-label="缩小" onClick={() => commandCanvas('out')}><Minus size={18} /></button>
        <button className="review-zoom-label" title="恢复 100%" onClick={() => commandCanvas('reset')}>{zoom}%</button>
        <button aria-label="放大" onClick={() => commandCanvas('in')}><Plus size={18} /></button>
        <button aria-label="适应窗口" title="适应窗口（0）" onClick={() => commandCanvas('fit')}><Maximize size={18} /></button>
      </nav>
      <ReviewCanvas view={view} panning={panning} onPanningChange={setPanning} command={command} onZoomChange={setZoom}>
        {content}
      </ReviewCanvas>
      <div className="review-canvas-help">{panning ? '滚轮缩放 · 拖动平移 · 双击适应窗口' : '控件可操作 · Ctrl + 滚轮缩放 · 空格 / 中键拖移'}</div>
      <aside className="review-theme-tools" aria-label="预览外观">
        {themeOpen && <div id="review-theme-panel" className="review-theme-panel">
          <div className="review-theme-heading"><strong>画布外观</strong><button aria-label="关闭外观面板" onClick={() => setThemeOpen(false)}><X size={16} /></button></div>
          <p>风格</p>
          <div className="review-theme-options">{styles.map(item => <button key={item.id} aria-pressed={style === item.id} onClick={() => setStyle(item.id)}>{item.label}</button>)}</div>
          <p>明暗</p>
          <div className="review-theme-options"><button aria-pressed={!dark} onClick={() => setDark(false)}><Sun size={16} />日间</button><button aria-pressed={dark} onClick={() => setDark(true)}><Moon size={16} />夜间</button></div>
          <small>只改变预览，不修改 WebUI 设置</small>
        </div>}
        <div className="review-theme-buttons">
          <button aria-label={dark ? '切换日间' : '切换夜间'} title={dark ? '切换日间' : '切换夜间'} onClick={() => setDark(!dark)}>{dark ? <Sun size={20} /> : <Moon size={20} />}</button>
          <button aria-label="切换风格" aria-expanded={themeOpen} aria-controls="review-theme-panel" onClick={() => setThemeOpen(!themeOpen)}><Palette size={20} />外观</button>
        </div>
      </aside>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<ReviewPage />)
