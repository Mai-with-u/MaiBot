import {
  type ChangeEvent,
  type ComponentType,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  Activity,
  ArrowLeftRight,
  BarChart3,
  CalendarDays,
  ChevronRight,
  CircleGauge,
  Download,
  Filter,
  FlaskConical,
  HeartHandshake,
  Info,
  LayoutDashboard,
  ListFilter,
  LoaderCircle,
  MessageCircleReply,
  MessagesSquare,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Trash2,
  Upload,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { Badge } from '@/components/ui/badge'
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
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useToast } from '@/hooks/use-toast'
import { backendApi } from '@/lib/http'
import { cn } from '@/lib/utils'

import { ReplyEffectsBrowser } from './reply-effects-browser'

interface Aggregate {
  name: string
  count: number
  response_score: number | null
  response_score_std?: number | null
  reception_score: number | null
  reception_score_std?: number | null
  conversation_score: number | null
  conversation_score_std?: number | null
  raw_score: number | null
  raw_score_std?: number | null
  relative_score: number | null
  relative_score_std?: number | null
  confidence: number | null
  confidence_std?: number | null
}

interface VersionAggregate extends Aggregate {
  model_name: string
  prompt_fingerprint: string
  model_names: string[]
  prompt_fingerprints: string[]
  first_seen: string
  last_seen: string
  collapsed_models: boolean
  collapsed_versions: boolean
  score_distributions?: Partial<Record<DistributionMetric, ScoreDistribution>>
}

type DistributionMetric =
  | 'response_score'
  | 'reception_score'
  | 'conversation_score'
  | 'relative_score'

interface ScoreDistributionBucket {
  score: number
  range: string
  count: number
  percentage: number
}

interface ScoreDistribution {
  sample_count: number
  buckets: ScoreDistributionBucket[]
}

interface Overview {
  summary: Aggregate
  strategies: Aggregate[]
  versions: VersionAggregate[]
  trend: Aggregate[]
  filters: {
    sessions: [string, string][]
    strategies: string[]
    models: string[]
  }
}

interface ComparisonOption {
  key: string
  label: string
  modelNames: string[]
  promptFingerprints: string[]
}

interface SignificanceMetric {
  field: string
  label: string
  left_count: number
  right_count: number
  left_mean: number | null
  right_mean: number | null
  mean_difference: number | null
  confidence_interval: [number, number] | null
  p_value: number | null
  significant: boolean
  hedges_g: number | null
  sufficient: boolean
  reason: string
}

interface SignificanceComparisonResult {
  method: 'two_sided_welch_t_test'
  alpha: number
  left: { name: string; record_count: number }
  right: { name: string; record_count: number }
  metrics: SignificanceMetric[]
  significant_count: number
}

interface ComparisonRequestState {
  signature: string
  loading: boolean
  result: SignificanceComparisonResult | null
  error: string
}

interface PromptVersionSession {
  session_id: string
  session_name: string
  sample_count: number
  last_seen: string
}

interface PromptVersionDetail {
  prompt_fingerprint: string
  model_name: string
  sample_count: number
  first_seen: string
  last_seen: string
  sessions: PromptVersionSession[]
  selected_session_id: string
  system_prompt: string
  current_prompt_fingerprint: string
  current_system_prompt: string
  current_created_at: string
  is_current: boolean
  diff_lines: string[]
}

interface ReplyEffectImportResult {
  total: number
  imported: number
  skipped: number
  conflicts: number
}

interface ReplyEffectClearResult {
  deleted_records: number
  deleted_mirrors: number
  cleared_trackers: number
  space_reclaimed: boolean
}

const STRATEGY_NAMES: Record<string, string> = {
  answer: '信息回答',
  opinion: '观点表达',
  empathy: '共情支持',
  humor: '玩梗调侃',
  question: '追问引导',
  topic_start: '主动开题',
  acknowledgement: '简短接话',
  other: '其他',
}

const DISTRIBUTION_COLORS = [
  '#e4572e',
  '#168aad',
  '#2a9d5b',
  '#8357c5',
  '#d94f91',
  '#d9970b',
  '#008f95',
  '#6f9f18',
  '#d1495b',
  '#5367d5',
]

function scoreWithStdText(
  value: number | null | undefined,
  standardDeviation: number | null | undefined
) {
  if (value == null) return '—'
  if (standardDeviation == null) return value.toFixed(1)
  return `${value.toFixed(1)} ± ${standardDeviation.toFixed(1)}`
}

function confidenceText(value: number | null | undefined) {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

function diffLineClass(line: string) {
  if (line.startsWith('+++') || line.startsWith('---')) return 'text-muted-foreground'
  if (line.startsWith('@@')) return 'bg-sky-500/10 text-sky-700 dark:text-sky-300'
  if (line.startsWith('+')) return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  if (line.startsWith('-')) return 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
  return ''
}

function strategyName(value: string) {
  return STRATEGY_NAMES[value] ?? value ?? '未分类'
}

function readablePromptVersionName(
  item: VersionAggregate,
  index: number,
  items: VersionAggregate[]
) {
  if (item.collapsed_versions) {
    return item.collapsed_models
      ? '全部模型 · 全部版本'
      : `${item.model_name || '未知模型'} · 全部版本`
  }
  const versionNumber =
    items.slice(0, index).filter((candidate) => {
      if (item.collapsed_models) return true
      return candidate.model_name === item.model_name
    }).length + 1
  const modelLabel = item.collapsed_models ? '全部模型' : item.model_name || '未知模型'
  return `${modelLabel} · 版本 ${versionNumber}`
}

function buildComparisonOptions(versions: VersionAggregate[]): ComparisonOption[] {
  return versions.map((item, index, items) => ({
    key: `${index}:${item.model_names.join(',')}:${item.prompt_fingerprints.join(',')}`,
    label: readablePromptVersionName(item, index, items),
    modelNames: item.model_names,
    promptFingerprints: item.prompt_fingerprints,
  }))
}

function formatComparisonNumber(value: number | null, digits = 2) {
  if (value == null || !Number.isFinite(value)) return '未计算'
  return value.toFixed(digits)
}

function formatMeanDifference(value: number | null) {
  if (value == null || !Number.isFinite(value)) return '未计算'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`
}

function formatPValue(value: number | null) {
  if (value == null || !Number.isFinite(value)) return '未计算'
  if (value < 0.0001) return '< 0.0001'
  return value.toFixed(4)
}

function effectSizeText(value: number | null) {
  if (value == null || !Number.isFinite(value)) return '未计算'
  const magnitude = Math.abs(value)
  const level = magnitude < 0.2 ? '极小' : magnitude < 0.5 ? '小' : magnitude < 0.8 ? '中' : '大'
  return `${value.toFixed(2)}（${level}）`
}

function ScoreDistributionChart({
  title,
  metric,
  versions,
}: {
  title: string
  metric: DistributionMetric
  versions: VersionAggregate[]
}) {
  const series = versions
    .map((version, index, items) => ({
      key: `series_${index}`,
      label: readablePromptVersionName(version, index, items),
      color: DISTRIBUTION_COLORS[index % DISTRIBUTION_COLORS.length],
      distribution: version.score_distributions?.[metric],
    }))
    .filter((item) => (item.distribution?.sample_count ?? 0) > 0)
  const buckets = series[0]?.distribution?.buckets ?? []
  const chartData = buckets.map((bucket, bucketIndex) => {
    const point: Record<string, string | number> = {
      score: bucket.score,
      range: bucket.range,
    }
    for (const item of series) {
      point[item.key] = item.distribution?.buckets[bucketIndex]?.percentage ?? 0
    }
    return point
  })

  return (
    <div className="bg-muted/15 min-w-0 rounded-xl border p-3 sm:p-4">
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="text-muted-foreground mt-1 text-xs">每 5 分一档 · 纵轴为组内样本占比</p>
      {series.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2">
          {series.map((item) => (
            <div
              key={item.key}
              className="text-muted-foreground flex min-w-0 items-center gap-1.5 text-[11px]"
            >
              <span
                className="h-0.5 w-5 shrink-0 rounded-full"
                style={{ backgroundColor: item.color }}
              />
              <span className="max-w-52 truncate" title={item.label}>
                {item.label}
              </span>
              <span className="shrink-0">({item.distribution?.sample_count ?? 0})</span>
            </div>
          ))}
        </div>
      )}
      <div className="mt-3 h-64">
        {series.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 8, right: 10, left: -10, bottom: 0 }}>
              <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="score"
                domain={[0, 100]}
                ticks={[0, 20, 40, 60, 80, 100]}
                type="number"
                tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                unit="%"
                tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip
                labelFormatter={(_, payload) =>
                  payload?.[0]?.payload?.range ? `分数 ${payload[0].payload.range}` : ''
                }
                formatter={(value, name) => [`${Number(value).toFixed(1)}%`, name]}
              />
              {series.map((item) => (
                <Area
                  key={item.key}
                  type="monotone"
                  dataKey={item.key}
                  name={item.label}
                  stroke={item.color}
                  fill={item.color}
                  fillOpacity={0.2}
                  strokeWidth={2.5}
                  dot={false}
                  activeDot={{ r: 3 }}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            暂无可绘制的分数
          </div>
        )}
      </div>
    </div>
  )
}

function SectionPanel({
  title,
  description,
  icon: Icon,
  action,
  children,
  className,
}: {
  title: string
  description: string
  icon: ComponentType<{ className?: string }>
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={cn('bg-card min-w-0 overflow-hidden rounded-xl border shadow-sm', className)}
    >
      <div className="border-border/70 bg-muted/20 flex flex-wrap items-start justify-between gap-3 border-b px-4 py-4 sm:px-5">
        <div className="flex min-w-0 items-start gap-3">
          <div className="bg-primary/10 text-primary mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
            <Icon className="h-4.5 w-4.5" />
          </div>
          <div className="min-w-0">
            <h2 className="text-base font-semibold">{title}</h2>
            <p className="text-muted-foreground mt-1 text-sm">{description}</p>
          </div>
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone,
}: {
  label: string
  value: string | number
  detail: string
  icon: ComponentType<{ className?: string }>
  tone: 'primary' | 'blue' | 'rose' | 'violet' | 'amber'
}) {
  const toneClasses = {
    primary: 'bg-primary/10 text-primary',
    blue: 'bg-sky-500/10 text-sky-600 dark:text-sky-300',
    rose: 'bg-rose-500/10 text-rose-600 dark:text-rose-300',
    violet: 'bg-violet-500/10 text-violet-600 dark:text-violet-300',
    amber: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
  }

  return (
    <div className="group bg-card hover:border-primary/50 relative min-h-32 overflow-hidden rounded-xl border px-4 py-4 shadow-sm transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-md">
      <div className="bg-primary/70 absolute inset-x-0 top-0 h-0.5" />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-muted-foreground text-xs font-medium tracking-wide">{label}</div>
          <div className="text-foreground mt-2 text-3xl font-bold tracking-tight tabular-nums">
            {value}
          </div>
        </div>
        <div
          className={cn(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
            toneClasses[tone]
          )}
        >
          <Icon className="h-4.5 w-4.5" />
        </div>
      </div>
      <div className="text-muted-foreground mt-3 truncate text-xs">{detail}</div>
    </div>
  )
}

function EmptyTableRow({ colSpan, children }: { colSpan: number; children: ReactNode }) {
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="text-muted-foreground h-28 text-center">
        {children}
      </TableCell>
    </TableRow>
  )
}

function SignificanceComparison({
  options,
  leftKey,
  rightKey,
  loading,
  result,
  error,
  onLeftChange,
  onRightChange,
  onCompare,
}: {
  options: ComparisonOption[]
  leftKey: string
  rightKey: string
  loading: boolean
  result: SignificanceComparisonResult | null
  error: string
  onLeftChange: (value: string) => void
  onRightChange: (value: string) => void
  onCompare: () => void
}) {
  const canCompare = options.length >= 2 && leftKey !== rightKey

  return (
    <div className="border-t p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <div className="bg-primary/10 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
          <FlaskConical className="h-4 w-4" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">显著性对比</h3>
          <p className="text-muted-foreground mt-1 text-xs leading-5">
            对当前筛选范围执行双侧 Welch t 检验。p &lt; 0.05 表示均值差异具有统计显著性。
          </p>
        </div>
      </div>

      {options.length >= 2 ? (
        <div className="mt-4 grid items-end gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto]">
          <div className="space-y-2">
            <label htmlFor="significance-left" className="text-xs font-medium">
              项目 A
            </label>
            <Select value={leftKey} onValueChange={onLeftChange}>
              <SelectTrigger id="significance-left" aria-label="显著性对比项目 A">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((option) => (
                  <SelectItem
                    key={option.key}
                    value={option.key}
                    disabled={option.key === rightKey}
                  >
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <ArrowLeftRight className="text-muted-foreground mb-2 hidden h-4 w-4 md:block" />
          <div className="space-y-2">
            <label htmlFor="significance-right" className="text-xs font-medium">
              项目 B
            </label>
            <Select value={rightKey} onValueChange={onRightChange}>
              <SelectTrigger id="significance-right" aria-label="显著性对比项目 B">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((option) => (
                  <SelectItem key={option.key} value={option.key} disabled={option.key === leftKey}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button type="button" onClick={onCompare} disabled={!canCompare || loading}>
            {loading ? (
              <LoaderCircle className="h-4 w-4 animate-spin" />
            ) : (
              <FlaskConical className="h-4 w-4" />
            )}
            {loading ? '计算中' : '计算显著性'}
          </Button>
        </div>
      ) : (
        <div className="text-muted-foreground mt-4 rounded-lg border border-dashed px-4 py-8 text-center text-sm">
          当前筛选与合并方式下至少需要两个项目
        </div>
      )}

      {error && (
        <div className="border-destructive/30 bg-destructive/10 text-destructive mt-4 rounded-lg border px-3 py-2 text-sm">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-4 space-y-3">
          <div
            className={cn(
              'flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm',
              result.significant_count > 0
                ? 'border-primary/30 bg-primary/8'
                : 'border-border bg-muted/25'
            )}
            role="status"
          >
            <span className="font-medium">
              {result.significant_count > 0
                ? `发现 ${result.significant_count} 项显著差异`
                : '未发现显著差异'}
            </span>
            <span className="text-muted-foreground text-xs">
              A: {result.left.name}（n={result.left.record_count}） / B: {result.right.name}（n=
              {result.right.record_count}）
            </span>
          </div>
          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader className="bg-muted/40">
                <TableRow>
                  <TableHead>指标</TableHead>
                  <TableHead>A 均值</TableHead>
                  <TableHead>B 均值</TableHead>
                  <TableHead>均值差 A-B</TableHead>
                  <TableHead>95% 置信区间</TableHead>
                  <TableHead>Hedges' g</TableHead>
                  <TableHead>p 值</TableHead>
                  <TableHead>结论</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {result.metrics.map((metric) => (
                  <TableRow key={metric.field}>
                    <TableCell className="font-medium">{metric.label}</TableCell>
                    <TableCell className="tabular-nums">
                      {formatComparisonNumber(metric.left_mean)}（n={metric.left_count}）
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {formatComparisonNumber(metric.right_mean)}（n={metric.right_count}）
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {formatMeanDifference(metric.mean_difference)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap tabular-nums">
                      {metric.confidence_interval
                        ? `[${formatComparisonNumber(metric.confidence_interval[0])}, ${formatComparisonNumber(metric.confidence_interval[1])}]`
                        : metric.reason || '未计算'}
                    </TableCell>
                    <TableCell className="whitespace-nowrap tabular-nums">
                      {effectSizeText(metric.hedges_g)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {formatPValue(metric.p_value)}
                    </TableCell>
                    <TableCell>
                      {metric.sufficient ? (
                        <Badge variant={metric.significant ? 'default' : 'outline'}>
                          {metric.significant ? '显著' : '不显著'}
                        </Badge>
                      ) : (
                        <Badge variant="secondary">样本不足</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="text-muted-foreground text-xs leading-5">
            Hedges' g
            表示标准化效应量。各指标独立检验，未做多重比较校正；显著性不等于效果足够大，结果也不能排除聊天流、时段等混杂因素。
          </p>
        </div>
      )}
    </div>
  )
}

export function ReplyEffectsPage() {
  const { toast } = useToast()
  const importInputRef = useRef<HTMLInputElement>(null)
  const [activeView, setActiveView] = useState<'analysis' | 'browser'>('analysis')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [strategy, setStrategy] = useState('')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [minConfidence, setMinConfidence] = useState('0.6')
  const [collapseVersions, setCollapseVersions] = useState(false)
  const [collapseModels, setCollapseModels] = useState(false)
  const [selectedPromptVersion, setSelectedPromptVersion] = useState<VersionAggregate | null>(null)
  const [promptVersionDetail, setPromptVersionDetail] = useState<PromptVersionDetail | null>(null)
  const [promptVersionLoading, setPromptVersionLoading] = useState(false)
  const [comparisonLeftKey, setComparisonLeftKey] = useState('')
  const [comparisonRightKey, setComparisonRightKey] = useState('')
  const [comparisonRequest, setComparisonRequest] = useState<ComparisonRequestState | null>(null)
  const [browserRefreshToken, setBrowserRefreshToken] = useState(0)
  const [browserLoading, setBrowserLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [importing, setImporting] = useState(false)
  const [clearing, setClearing] = useState(false)

  const baseQuery = useMemo(() => {
    const params = new URLSearchParams()
    if (sessionId) params.set('session_id', sessionId)
    if (strategy) params.set('strategy', strategy)
    if (startAt) params.set('start_at', `${startAt}T00:00:00`)
    if (endAt) params.set('end_at', `${endAt}T23:59:59`)
    return params.toString()
  }, [endAt, sessionId, startAt, strategy])

  const overviewQuery = useMemo(() => {
    const params = new URLSearchParams(baseQuery)
    params.set('min_confidence', minConfidence || '0')
    params.set('collapse_versions', String(collapseVersions))
    params.set('collapse_models', String(collapseModels))
    return params.toString()
  }, [baseQuery, collapseModels, collapseVersions, minConfidence])

  const comparisonOptions = useMemo(
    () => buildComparisonOptions(overview?.versions ?? []),
    [overview?.versions]
  )
  const selectedLeftOption =
    comparisonOptions.find((option) => option.key === comparisonLeftKey) ?? comparisonOptions[0]
  const selectedRightOption =
    comparisonOptions.find(
      (option) => option.key === comparisonRightKey && option.key !== selectedLeftOption?.key
    ) ?? comparisonOptions.find((option) => option.key !== selectedLeftOption?.key)
  const resolvedLeftKey = selectedLeftOption?.key ?? ''
  const resolvedRightKey = selectedRightOption?.key ?? ''
  const comparisonSignature = `${overviewQuery}|${resolvedLeftKey}|${resolvedRightKey}`
  const activeComparisonRequest =
    comparisonRequest?.signature === comparisonSignature ? comparisonRequest : null

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const nextOverview = await backendApi.get<Overview>(
        `/api/webui/reply-effects/overview?${overviewQuery}`
      )
      setOverview(nextOverview)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '加载回复效果数据失败')
    } finally {
      setLoading(false)
    }
  }, [overviewQuery])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const loadPromptVersionDetail = async (version: VersionAggregate, targetSessionId?: string) => {
    if (!version.prompt_fingerprint) return
    setSelectedPromptVersion(version)
    setPromptVersionLoading(true)
    try {
      const params = new URLSearchParams()
      if (version.model_name) params.set('model_name', version.model_name)
      if (targetSessionId || sessionId) params.set('session_id', targetSessionId || sessionId)
      const query = params.toString()
      const path = `/api/webui/reply-effects/prompt-versions/${encodeURIComponent(version.prompt_fingerprint)}${query ? `?${query}` : ''}`
      setPromptVersionDetail(await backendApi.get<PromptVersionDetail>(path))
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '加载 Prompt 版本失败')
      setSelectedPromptVersion(null)
    } finally {
      setPromptVersionLoading(false)
    }
  }

  const compareSelectedProjects = async () => {
    if (!selectedLeftOption || !selectedRightOption) return

    const signature = comparisonSignature
    setComparisonRequest({ signature, loading: true, result: null, error: '' })
    const body: Record<string, unknown> = {
      left: {
        name: selectedLeftOption.label,
        model_names: selectedLeftOption.modelNames,
        prompt_fingerprints: selectedLeftOption.promptFingerprints,
      },
      right: {
        name: selectedRightOption.label,
        model_names: selectedRightOption.modelNames,
        prompt_fingerprints: selectedRightOption.promptFingerprints,
      },
      min_confidence: Number(minConfidence || 0),
    }
    if (sessionId) body.session_id = sessionId
    if (strategy) body.strategy = strategy
    if (startAt) body.start_at = `${startAt}T00:00:00`
    if (endAt) body.end_at = `${endAt}T23:59:59`

    try {
      const result = await backendApi.post<SignificanceComparisonResult>(
        '/api/webui/reply-effects/compare',
        { body }
      )
      setComparisonRequest({ signature, loading: false, result, error: '' })
    } catch (requestError) {
      setComparisonRequest({
        signature,
        loading: false,
        result: null,
        error: requestError instanceof Error ? requestError.message : '显著性检验失败',
      })
    }
  }

  const resetFilters = () => {
    setSessionId('')
    setStrategy('')
    setStartAt('')
    setEndAt('')
    setMinConfidence('0.6')
  }

  const summary = overview?.summary
  const hasActiveFilters = Boolean(
    sessionId || strategy || startAt || endAt || minConfidence !== '0.6'
  )
  const refreshPage = () => {
    void refresh()
    if (activeView === 'browser') {
      setBrowserRefreshToken((value) => value + 1)
    }
  }

  const exportScores = async () => {
    setExporting(true)
    try {
      const blob = await backendApi.get<Blob>('/api/webui/reply-effects/export', {
        parse: 'blob',
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `maibot-reply-effects-${new Date().toISOString().slice(0, 10)}.json.gz`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      toast({ title: '导出成功', description: '已导出全部回复效果评分数据' })
    } catch (requestError) {
      toast({
        title: '导出失败',
        description: requestError instanceof Error ? requestError.message : '无法导出评分数据',
        variant: 'destructive',
      })
    } finally {
      setExporting(false)
    }
  }

  const importScores = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return

    setImporting(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const result = await backendApi.post<ReplyEffectImportResult>(
        '/api/webui/reply-effects/import',
        { body: form }
      )
      const conflictText = result.conflicts ? `，${result.conflicts} 条冲突未覆盖` : ''
      toast({
        title: '导入完成',
        description: `新增 ${result.imported} 条，跳过 ${result.skipped} 条相同记录${conflictText}`,
      })
      refreshPage()
    } catch (requestError) {
      toast({
        title: '导入失败',
        description: requestError instanceof Error ? requestError.message : '无法导入评分数据',
        variant: 'destructive',
      })
    } finally {
      setImporting(false)
    }
  }

  const clearScores = async () => {
    setClearing(true)
    try {
      const result = await backendApi.delete<ReplyEffectClearResult>(
        '/api/webui/reply-effects/clear'
      )
      toast({
        title: '评分已清空',
        description: `已删除 ${result.deleted_records} 条评分和 ${result.deleted_mirrors} 个诊断镜像${result.space_reclaimed ? '' : '；数据库空页将在后续写入时复用'}`,
      })
      refreshPage()
    } catch (requestError) {
      toast({
        title: '清空失败',
        description: requestError instanceof Error ? requestError.message : '无法清空评分数据',
        variant: 'destructive',
      })
    } finally {
      setClearing(false)
    }
  }

  return (
    <div className="flex min-h-full flex-col p-4 sm:p-6">
      <div className="mx-auto w-full max-w-[1800px] space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Tabs
            value={activeView}
            onValueChange={(value) => setActiveView(value as 'analysis' | 'browser')}
          >
            <TabsList>
              <TabsTrigger value="analysis" className="gap-2">
                <LayoutDashboard className="h-4 w-4" />
                数据分析
              </TabsTrigger>
              <TabsTrigger value="browser" className="gap-2">
                <ListFilter className="h-4 w-4" />
                评估浏览
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={importInputRef}
              type="file"
              accept=".json,.gz,application/json,application/gzip"
              className="hidden"
              onChange={(event) => void importScores(event)}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => importInputRef.current?.click()}
              disabled={importing || exporting}
            >
              <Upload className="h-4 w-4" />
              {importing ? '导入中' : '导入评分'}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void exportScores()}
              disabled={importing || exporting}
            >
              <Download className="h-4 w-4" />
              {exporting ? '导出中' : '导出评分'}
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  disabled={importing || exporting || clearing}
                >
                  <Trash2 className="h-4 w-4" />
                  {clearing ? '清空中' : '清空评分'}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>确定清空全部评分数据？</AlertDialogTitle>
                  <AlertDialogDescription>
                    将删除全部回复效果评分、上下文与诊断镜像，并取消当前尚未结算的观察任务。聊天记录、模型调用和麦麦观察数据不会受到影响。建议先导出备份。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction
                    variant="destructive"
                    disabled={clearing}
                    onClick={() => void clearScores()}
                  >
                    确认清空
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={refreshPage}
              disabled={loading || browserLoading || importing || clearing}
            >
              <RefreshCw className={cn('h-4 w-4', (loading || browserLoading) && 'animate-spin')} />
              刷新数据
            </Button>
          </div>
        </div>

        {activeView === 'analysis' ? (
          <>
            <section className="border-primary/25 bg-card/50 rounded-xl border p-4 shadow-sm sm:p-5">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Filter className="text-primary h-4 w-4" />
                  <div>
                    <h2 className="text-sm font-semibold">统计范围</h2>
                    <p className="text-muted-foreground text-xs">
                      筛选会自动应用，置信度门槛仅影响汇总统计。
                    </p>
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={resetFilters}
                  disabled={!hasActiveFilters}
                >
                  <RotateCcw className="h-4 w-4" />
                  重置
                </Button>
              </div>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                <div className="space-y-1.5">
                  <span className="text-muted-foreground text-xs font-medium">聊天流</span>
                  <Select
                    value={sessionId || 'all'}
                    onValueChange={(value) => setSessionId(value === 'all' ? '' : value)}
                  >
                    <SelectTrigger aria-label="聊天流">
                      <SelectValue placeholder="全部聊天流" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">全部聊天流</SelectItem>
                      {overview?.filters.sessions.map(([id, name]) => (
                        <SelectItem key={id} value={id}>
                          {name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <span className="text-muted-foreground text-xs font-medium">回复策略</span>
                  <Select
                    value={strategy || 'all'}
                    onValueChange={(value) => setStrategy(value === 'all' ? '' : value)}
                  >
                    <SelectTrigger aria-label="回复策略">
                      <SelectValue placeholder="全部策略" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">全部策略</SelectItem>
                      {Object.entries(STRATEGY_NAMES).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <label htmlFor="reply-effects-start-date" className="space-y-1.5">
                  <span className="text-muted-foreground block text-xs font-medium">开始日期</span>
                  <Input
                    id="reply-effects-start-date"
                    type="date"
                    aria-label="开始日期"
                    value={startAt}
                    onChange={(event) => setStartAt(event.target.value)}
                  />
                </label>
                <label htmlFor="reply-effects-end-date" className="space-y-1.5">
                  <span className="text-muted-foreground block text-xs font-medium">结束日期</span>
                  <Input
                    id="reply-effects-end-date"
                    type="date"
                    aria-label="结束日期"
                    value={endAt}
                    onChange={(event) => setEndAt(event.target.value)}
                  />
                </label>
                <label htmlFor="reply-effects-min-confidence" className="space-y-1.5">
                  <span className="text-muted-foreground block text-xs font-medium">
                    统计最低置信度
                  </span>
                  <Input
                    id="reply-effects-min-confidence"
                    type="number"
                    min="0"
                    max="1"
                    step="0.1"
                    aria-label="统计最低置信度"
                    value={minConfidence}
                    onChange={(event) => setMinConfidence(event.target.value)}
                  />
                </label>
              </div>
            </section>

            {error && (
              <div className="border-destructive/30 bg-destructive/10 text-destructive rounded-lg border px-4 py-3 text-sm">
                {error}
              </div>
            )}

            {!loading && summary?.count === 0 && (
              <div className="border-primary/25 bg-primary/5 flex items-start gap-3 rounded-lg border px-4 py-3 text-sm">
                <Info className="text-primary mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  <div className="font-medium">当前统计门槛下暂无已完成记录</div>
                  <div className="text-muted-foreground mt-0.5">
                    可尝试降低统计最低置信度；全部采集记录及结算状态请在“评估浏览”中查看。
                  </div>
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <MetricCard
                label="有效样本"
                value={loading ? '…' : (summary?.count ?? 0)}
                detail="符合当前统计条件"
                icon={MessageCircleReply}
                tone="primary"
              />
              <MetricCard
                label="回应度"
                value={
                  loading
                    ? '…'
                    : scoreWithStdText(summary?.response_score, summary?.response_score_std)
                }
                detail="回应存在、人数、深度与速度"
                icon={Activity}
                tone="blue"
              />
              <MetricCard
                label="情感接受度"
                value={
                  loading
                    ? '…'
                    : scoreWithStdText(summary?.reception_score, summary?.reception_score_std)
                }
                detail="针对回复内容与 Bot 的态度"
                icon={HeartHandshake}
                tone="rose"
              />
              <MetricCard
                label="聊天推动度"
                value={
                  loading
                    ? '…'
                    : scoreWithStdText(summary?.conversation_score, summary?.conversation_score_std)
                }
                detail="延续、参与和互动链变化"
                icon={MessagesSquare}
                tone="violet"
              />
              <MetricCard
                label="相对分"
                value={
                  loading
                    ? '…'
                    : scoreWithStdText(summary?.relative_score, summary?.relative_score_std)
                }
                detail={summary?.relative_score == null ? '基线建立中' : '同群同场景百分位'}
                icon={CircleGauge}
                tone="amber"
              />
            </div>

            <div className="grid min-w-0 gap-5 xl:grid-cols-2">
              <SectionPanel title="趋势" description="按天汇总三个评分维度" icon={BarChart3}>
                <div className="h-80 p-4 sm:p-5">
                  {overview?.trend.length ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart
                        data={overview.trend}
                        margin={{ top: 8, right: 8, left: -12, bottom: 0 }}
                      >
                        <CartesianGrid
                          stroke="hsl(var(--border))"
                          strokeDasharray="3 3"
                          vertical={false}
                        />
                        <XAxis
                          dataKey="name"
                          tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }}
                          tickLine={false}
                          axisLine={false}
                        />
                        <YAxis
                          domain={[0, 100]}
                          tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }}
                          tickLine={false}
                          axisLine={false}
                        />
                        <Tooltip cursor={{ fill: 'hsl(var(--muted) / 0.35)' }} />
                        <Legend iconType="circle" iconSize={8} />
                        <Bar
                          dataKey="response_score"
                          name="回应度"
                          fill="var(--chart-1)"
                          radius={[3, 3, 0, 0]}
                        />
                        <Bar
                          dataKey="reception_score"
                          name="接受度"
                          fill="var(--chart-2)"
                          radius={[3, 3, 0, 0]}
                        />
                        <Bar
                          dataKey="conversation_score"
                          name="推动度"
                          fill="var(--chart-3)"
                          radius={[3, 3, 0, 0]}
                        />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-3 text-sm">
                      <div className="bg-muted flex h-12 w-12 items-center justify-center rounded-full">
                        <BarChart3 className="h-5 w-5" />
                      </div>
                      <span>有已完成记录后，这里会展示分数趋势</span>
                    </div>
                  )}
                </div>
              </SectionPanel>

              <SectionPanel
                title="策略对比"
                description="比较固定语义策略在当前评分器下的表现"
                icon={Sparkles}
              >
                <div className="overflow-x-auto p-4 sm:p-5">
                  <Table>
                    <TableHeader className="bg-muted/40">
                      <TableRow>
                        <TableHead>策略</TableHead>
                        <TableHead>样本</TableHead>
                        <TableHead>原始分</TableHead>
                        <TableHead>相对分</TableHead>
                        <TableHead>置信度</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {overview?.strategies.length ? (
                        overview.strategies.map((item) => (
                          <TableRow key={item.name}>
                            <TableCell className="font-medium">{strategyName(item.name)}</TableCell>
                            <TableCell>{item.count}</TableCell>
                            <TableCell>
                              {scoreWithStdText(item.raw_score, item.raw_score_std)}
                            </TableCell>
                            <TableCell>
                              {scoreWithStdText(item.relative_score, item.relative_score_std)}
                            </TableCell>
                            <TableCell>{confidenceText(item.confidence)}</TableCell>
                          </TableRow>
                        ))
                      ) : (
                        <EmptyTableRow colSpan={5}>暂无可对比的策略数据</EmptyTableRow>
                      )}
                    </TableBody>
                  </Table>
                </div>
              </SectionPanel>
            </div>

            <SectionPanel
              title="模型与 Prompt 版本"
              description="按回复模型和稳定 Prompt 版本聚合，观察版本变化"
              icon={CalendarDays}
              action={
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant={collapseVersions ? 'outline' : 'secondary'}
                    size="sm"
                    aria-pressed={!collapseVersions}
                    onClick={() => setCollapseVersions((value) => !value)}
                  >
                    {collapseVersions ? '显示不同版本' : '合并不同版本'}
                  </Button>
                  <Button
                    type="button"
                    variant={collapseModels ? 'outline' : 'secondary'}
                    size="sm"
                    aria-pressed={!collapseModels}
                    onClick={() => setCollapseModels((value) => !value)}
                  >
                    {collapseModels ? '显示不同模型' : '合并不同模型'}
                  </Button>
                </div>
              }
            >
              <div className="overflow-x-auto p-4 sm:p-5">
                <Table>
                  <TableHeader className="bg-muted/40">
                    <TableRow>
                      <TableHead>版本</TableHead>
                      <TableHead>样本</TableHead>
                      <TableHead>回应度</TableHead>
                      <TableHead>接受度</TableHead>
                      <TableHead>推动度</TableHead>
                      <TableHead>相对分</TableHead>
                      <TableHead>观察时间</TableHead>
                      <TableHead className="w-10" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {overview?.versions.length ? (
                      overview.versions.map((item, index, items) => (
                        <TableRow
                          key={item.name}
                          className={cn(
                            item.prompt_fingerprint && 'hover:bg-muted/40 cursor-pointer'
                          )}
                          onClick={() => void loadPromptVersionDetail(item)}
                        >
                          <TableCell>{readablePromptVersionName(item, index, items)}</TableCell>
                          <TableCell>{item.count}</TableCell>
                          <TableCell>
                            {scoreWithStdText(item.response_score, item.response_score_std)}
                          </TableCell>
                          <TableCell>
                            {scoreWithStdText(item.reception_score, item.reception_score_std)}
                          </TableCell>
                          <TableCell>
                            {scoreWithStdText(item.conversation_score, item.conversation_score_std)}
                          </TableCell>
                          <TableCell>
                            {scoreWithStdText(item.relative_score, item.relative_score_std)}
                          </TableCell>
                          <TableCell className="text-muted-foreground whitespace-nowrap">
                            {new Date(item.first_seen).toLocaleDateString()}—
                            {new Date(item.last_seen).toLocaleDateString()}
                          </TableCell>
                          <TableCell>
                            {item.prompt_fingerprint && (
                              <ChevronRight className="text-muted-foreground h-4 w-4" />
                            )}
                          </TableCell>
                        </TableRow>
                      ))
                    ) : (
                      <EmptyTableRow colSpan={8}>暂无模型版本统计</EmptyTableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
              <div className="border-t p-4 sm:p-5">
                <div className="mb-4">
                  <div>
                    <h3 className="text-sm font-semibold">评分分布</h3>
                    <p className="text-muted-foreground mt-1 text-xs">
                      基于当前筛选与合并方式，对实际评分记录分桶
                    </p>
                  </div>
                </div>
                <div className="grid min-w-0 gap-4 xl:grid-cols-2">
                  <ScoreDistributionChart
                    title="回应度分布"
                    metric="response_score"
                    versions={overview?.versions ?? []}
                  />
                  <ScoreDistributionChart
                    title="情感接受度分布"
                    metric="reception_score"
                    versions={overview?.versions ?? []}
                  />
                  <ScoreDistributionChart
                    title="聊天推动度分布"
                    metric="conversation_score"
                    versions={overview?.versions ?? []}
                  />
                  <ScoreDistributionChart
                    title="相对分分布"
                    metric="relative_score"
                    versions={overview?.versions ?? []}
                  />
                </div>
              </div>
              <SignificanceComparison
                options={comparisonOptions}
                leftKey={resolvedLeftKey}
                rightKey={resolvedRightKey}
                loading={activeComparisonRequest?.loading ?? false}
                result={activeComparisonRequest?.result ?? null}
                error={activeComparisonRequest?.error ?? ''}
                onLeftChange={setComparisonLeftKey}
                onRightChange={setComparisonRightKey}
                onCompare={() => void compareSelectedProjects()}
              />
            </SectionPanel>
          </>
        ) : (
          <ReplyEffectsBrowser
            filters={overview?.filters}
            refreshToken={browserRefreshToken}
            onLoadingChange={setBrowserLoading}
          />
        )}
      </div>

      <Dialog
        open={selectedPromptVersion !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedPromptVersion(null)
            setPromptVersionDetail(null)
          }
        }}
      >
        <DialogContent className="[--dialog-width:88rem]">
          <DialogHeader>
            <DialogTitle>Prompt 版本与差异</DialogTitle>
          </DialogHeader>
          <DialogBody className="max-h-[82vh] space-y-5">
            {promptVersionLoading && !promptVersionDetail ? (
              <div className="text-muted-foreground py-16 text-center text-sm">
                正在加载 Prompt…
              </div>
            ) : (
              promptVersionDetail && (
                <>
                  <div className="bg-muted/35 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4">
                    <div>
                      <div className="font-medium">{promptVersionDetail.model_name}</div>
                      <div className="text-muted-foreground mt-1 text-xs">
                        {promptVersionDetail.sample_count} 条样本 · 首次使用{' '}
                        {new Date(promptVersionDetail.first_seen).toLocaleString()} · 最近使用{' '}
                        {new Date(promptVersionDetail.last_seen).toLocaleString()}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {promptVersionDetail.is_current && (
                        <Badge variant="secondary">当前版本</Badge>
                      )}
                      <Select
                        value={promptVersionDetail.selected_session_id}
                        onValueChange={(value) => {
                          if (selectedPromptVersion) {
                            void loadPromptVersionDetail(selectedPromptVersion, value)
                          }
                        }}
                      >
                        <SelectTrigger className="w-64" aria-label="Prompt 对比聊天流">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {promptVersionDetail.sessions.map((session) => (
                            <SelectItem key={session.session_id} value={session.session_id}>
                              {session.session_name}（{session.sample_count}）
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>

                  <div>
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <h3 className="text-sm font-semibold">与当前实发 Prompt 的 Diff</h3>
                      <span className="text-muted-foreground text-xs">
                        当前 Prompt：
                        {new Date(promptVersionDetail.current_created_at).toLocaleString()}
                      </span>
                    </div>
                    {promptVersionDetail.diff_lines.length ? (
                      <pre className="bg-muted/30 max-h-80 overflow-auto rounded-lg border p-3 font-mono text-xs leading-5">
                        {promptVersionDetail.diff_lines.map((line, index) => (
                          <div key={`${index}-${line}`} className={cn('px-1', diffLineClass(line))}>
                            {line || ' '}
                          </div>
                        ))}
                      </pre>
                    ) : (
                      <div className="text-muted-foreground rounded-lg border border-dashed py-8 text-center text-sm">
                        该版本就是此聊天流当前使用的 Prompt，没有差异。
                      </div>
                    )}
                  </div>

                  <div className="grid gap-4 xl:grid-cols-2">
                    <div className="min-w-0">
                      <h3 className="mb-2 text-sm font-semibold">所选版本 Prompt</h3>
                      <pre className="bg-muted/30 max-h-[28rem] overflow-auto rounded-lg border p-4 text-xs leading-5 whitespace-pre-wrap">
                        {promptVersionDetail.system_prompt || '未记录 System Prompt'}
                      </pre>
                    </div>
                    <div className="min-w-0">
                      <h3 className="mb-2 text-sm font-semibold">当前实发 Prompt</h3>
                      <pre className="bg-muted/30 max-h-[28rem] overflow-auto rounded-lg border p-4 text-xs leading-5 whitespace-pre-wrap">
                        {promptVersionDetail.current_system_prompt || '未记录 System Prompt'}
                      </pre>
                    </div>
                  </div>
                </>
              )
            )}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </div>
  )
}
