import { createContext, useContext, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  conditionMatches,
  nodeChildren,
  nodeVisible,
  resolveNodeValue,
  validateRenderSize,
} from '@/lib/plugin-webui'
import type { DataContexts, Scalar, WebUINode } from '@/lib/plugin-webui'

interface RendererProps {
  nodes: WebUINode[]
  data: Record<string, unknown>
  values: Record<string, Scalar>
  busy: boolean
  pendingData?: boolean
  selectionRevision?: number
  onChange: (name: string, value: Scalar) => void
  onAction: (name: string, contexts?: DataContexts) => void
}
interface InteractionState {
  selection: Record<string, unknown>
  openDialog: string | null
  select: (name: string, row: Record<string, unknown>, detail?: string | null) => void
  close: () => void
}
const InteractionContext = createContext<InteractionState | null>(null)

function display(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') throw new Error('Expected a scalar display value')
  return String(value)
}

// Missing images are empty; malformed/oversized data is an explicit rendering error.
function imageSource(value: unknown): string | null {
  if (value === null) return null
  if (
    typeof value !== 'string' ||
    value.length > 24000 ||
    !/^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(value)
  )
    throw new Error('Image must be a bounded JPEG, PNG or WebP data URL')
  return value
}

function NodeRenderer({ node, ...props }: Omit<RendererProps, 'nodes'> & { node: WebUINode }) {
  const { t } = useTranslation()
  const id = useId()
  const [tablePage, setTablePage] = useState(0)
  const [expanded, setExpanded] = useState(node.type === 'collapsible' && !!node.default_open)
  const interaction = useContext(InteractionContext)!
  const contexts: DataContexts = { selection: interaction.selection }
  const awaitingData =
    props.pendingData && 'value' in node && node.value !== null && typeof node.value === 'object'
  const visible = props.pendingData && node.when ? false : nodeVisible(node, props.data, contexts)
  const value = awaitingData || !visible ? null : resolveNodeValue(node, props.data, contexts)
  const children = <NodesRenderer {...props} nodes={nodeChildren(node)} />
  const fieldValue = 'name' in node ? props.values[node.name] : null
  const change = (next: Scalar) => {
    if ('name' in node) props.onChange(node.name, next)
  }
  const disabledByRule =
    !props.pendingData &&
    'disabled_when' in node &&
    !!node.disabled_when &&
    conditionMatches(node.disabled_when, props.data, contexts)
  const disabled =
    props.busy ||
    (!!props.pendingData && 'disabled_when' in node && !!node.disabled_when) ||
    disabledByRule
  const heading = node.label ? <Label htmlFor={id}>{node.label}</Label> : null

  if (!visible) return null

  if (awaitingData)
    return (
      <p role="status" className="text-muted-foreground text-sm">
        {props.busy ? t('pluginWebUI.loading') : t('pluginWebUI.empty')}
      </p>
    )

  switch (node.type) {
    case 'progress': {
      if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 100)
        throw new Error('Invalid progress percentage')
      return (
        <div className="space-y-1">
          <div className="flex justify-between text-xs">
            {heading}
            <span>{value}%</span>
          </div>
          <progress
            className="accent-primary h-2 w-full"
            value={value}
            max={100}
            aria-label={node.label ?? '进度'}
          />
        </div>
      )
    }
    case 'image': {
      const source = imageSource(value)
      return source ? (
        <img
          src={source}
          alt={node.label ?? ''}
          loading="lazy"
          decoding="async"
          className="bg-muted h-72 w-full rounded-md object-contain"
        />
      ) : (
        <p className="text-muted-foreground text-sm">{t('pluginWebUI.empty')}</p>
      )
    }
    case 'dialog':
      return (
        <Dialog
          open={interaction.openDialog === node.name}
          onOpenChange={(open) => {
            if (!open) interaction.close()
          }}
        >
          <DialogContent aria-describedby={undefined} className="max-h-[85vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>{node.label}</DialogTitle>
            </DialogHeader>
            {children}
          </DialogContent>
        </Dialog>
      )
    case 'collapsible':
      return (
        <Collapsible open={expanded} onOpenChange={setExpanded} className="space-y-3">
          <CollapsibleTrigger asChild>
            <Button variant="outline">{node.label}</Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="space-y-4">{children}</CollapsibleContent>
        </Collapsible>
      )
    case 'stack':
      return (
        <section className="space-y-4">
          {heading}
          {children}
        </section>
      )
    case 'grid': {
      const columns = {
        1: 'md:grid-cols-1',
        2: 'md:grid-cols-2',
        3: 'md:grid-cols-3',
        4: 'md:grid-cols-4',
      }
      return (
        <section>
          {heading}
          <div
            className={`grid grid-cols-1 gap-4 ${columns[node.columns as keyof typeof columns]}`}
          >
            {children}
          </div>
        </section>
      )
    }
    case 'card':
      return (
        <Card>
          {node.label && (
            <CardHeader>
              <CardTitle>{node.label}</CardTitle>
            </CardHeader>
          )}
          <CardContent className="space-y-4 pt-6">{children}</CardContent>
        </Card>
      )
    case 'tabs':
      return (
        <Tabs defaultValue="0">
          <TabsList className="max-w-full overflow-x-auto">
            {node.children.map((child, index) => (
              <TabsTrigger key={index} value={String(index)}>
                {child.label}
              </TabsTrigger>
            ))}
          </TabsList>
          {node.children.map((child, index) => (
            <TabsContent key={index} value={String(index)}>
              <NodeRenderer {...props} node={child} />
            </TabsContent>
          ))}
        </Tabs>
      )
    case 'text':
      if (node.format === 'log')
        return (
          <div className="space-y-2">
            {heading}
            <Textarea
              id={id}
              readOnly
              autoResize={false}
              data-dashboard-input="true"
              aria-label={node.label ?? '日志'}
              value={display(value)}
              className="bg-muted/40 h-64 resize-y font-mono text-xs leading-relaxed shadow-inner"
            />
          </div>
        )
      return (
        <div className="space-y-2">
          {heading}
          <p className="text-muted-foreground break-words whitespace-pre-wrap">{display(value)}</p>
        </div>
      )
    case 'stat':
      return (
        <Card>
          <CardHeader>
            <CardTitle className="text-muted-foreground text-sm">{node.label}</CardTitle>
          </CardHeader>
          <CardContent className="text-3xl font-semibold">{display(value)}</CardContent>
        </Card>
      )
    case 'input':
    case 'date':
      return (
        <div className="space-y-2">
          {heading}
          <Input
            id={id}
            disabled={disabled}
            type={
              node.type === 'date' ? 'date' : typeof node.value === 'number' ? 'number' : 'text'
            }
            value={fieldValue === null || fieldValue === undefined ? '' : String(fieldValue)}
            maxLength={4000}
            onChange={(event) =>
              change(
                typeof node.value === 'number' && event.target.value !== ''
                  ? Number(event.target.value)
                  : event.target.value
              )
            }
          />
        </div>
      )
    case 'switch':
      return (
        <div className="flex items-center gap-3">
          <Switch
            id={id}
            disabled={disabled}
            checked={fieldValue === true}
            onCheckedChange={change}
          />
          {heading}
        </div>
      )
    case 'select':
      return (
        <div className="space-y-2">
          {heading}
          <Select
            disabled={disabled}
            value={typeof fieldValue === 'string' ? fieldValue : undefined}
            onValueChange={change}
          >
            <SelectTrigger id={id}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {node.options.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )
    case 'button':
      return (
        <div className="inline-flex flex-col gap-1">
          <Button
            disabled={disabled}
            variant={
              node.variant === 'danger'
                ? 'destructive'
                : node.variant === 'muted'
                  ? 'secondary'
                  : 'default'
            }
            onClick={() => {
              props.onAction(node.action, contexts)
            }}
          >
            {node.label}
          </Button>
          {disabledByRule && node.disabled_reason && (
            <span className="text-muted-foreground text-xs">{node.disabled_reason}</span>
          )}
        </div>
      )
    case 'pagination': {
      if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new Error('Pagination data must be an object')
      const paging = value as Record<string, unknown>
      const current = paging.page
      const pages = paging.pages
      const total = paging.total
      if (
        typeof current !== 'number' ||
        typeof pages !== 'number' ||
        typeof total !== 'number' ||
        !Number.isSafeInteger(current) ||
        !Number.isSafeInteger(pages) ||
        !Number.isSafeInteger(total) ||
        current < 1 ||
        pages < current ||
        total < 0
      )
        throw new Error('Invalid pagination bounds')
      return (
        <nav
          aria-label={node.label ?? 'Pagination'}
          className="flex flex-wrap items-center justify-between gap-3"
        >
          <span className="text-muted-foreground text-sm">
            {t('pluginWebUI.paginationSummary', { page: current, pages, total })}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={disabled || current === 1}
              onClick={() => change(current - 1)}
            >
              {t('pluginWebUI.previous')}
            </Button>
            <Button
              variant="outline"
              disabled={disabled || current === pages}
              onClick={() => change(current + 1)}
            >
              {t('pluginWebUI.next')}
            </Button>
          </div>
        </nav>
      )
    }
    case 'table': {
      if (
        !Array.isArray(value) ||
        value.some((row) => row === null || typeof row !== 'object' || Array.isArray(row))
      )
        throw new Error('Table data must be an array of objects')
      const rows = value as Record<string, unknown>[]
      const columns = node.columns as Array<{ field: string; label: string }>
      const lastPage = Math.max(0, Math.ceil(rows.length / 50) - 1)
      const current = Math.min(tablePage, lastPage)
      return (
        <div className="space-y-3">
          {heading}
          <Table>
            <TableHeader>
              <TableRow>
                {columns.map((column) => (
                  <TableHead key={column.field}>{column.label}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.slice(current * 50, (current + 1) * 50).map((row, index) => (
                <TableRow
                  key={index}
                  className={
                    node.selection
                      ? 'focus-visible:outline-ring cursor-pointer focus-visible:outline-2'
                      : undefined
                  }
                  tabIndex={node.selection && !props.busy ? 0 : undefined}
                  aria-selected={
                    node.selection ? interaction.selection[node.selection] === row : undefined
                  }
                  onClick={() => {
                    if (node.selection && !props.busy)
                      interaction.select(node.selection, row, node.detail)
                  }}
                  onKeyDown={(event) => {
                    if (
                      node.selection &&
                      !props.busy &&
                      (event.key === 'Enter' || event.key === ' ')
                    ) {
                      event.preventDefault()
                      interaction.select(node.selection, row, node.detail)
                    }
                  }}
                >
                  {columns.map((column) => (
                    <TableCell key={column.field}>{display(row[column.field])}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {rows.length === 0 && (
            <p className="text-muted-foreground text-sm">{t('pluginWebUI.empty')}</p>
          )}
          {lastPage > 0 && (
            <div className="flex items-center gap-3">
              <Button
                variant="outline"
                disabled={current === 0}
                onClick={() => setTablePage(current - 1)}
              >
                {t('pluginWebUI.previous')}
              </Button>
              <span>
                {current + 1} / {lastPage + 1}
              </span>
              <Button
                variant="outline"
                disabled={current === lastPage}
                onClick={() => setTablePage(current + 1)}
              >
                {t('pluginWebUI.next')}
              </Button>
            </div>
          )}
        </div>
      )
    }
    case 'chart': {
      if (
        !Array.isArray(value) ||
        value.length > 2000 ||
        value.some(
          (row) =>
            row === null ||
            typeof row !== 'object' ||
            !node.y ||
            typeof row[node.y] !== 'number' ||
            !Number.isFinite(row[node.y]) ||
            !node.x ||
            !['string', 'number'].includes(typeof row[node.x])
        )
      )
        throw new Error(
          'Chart data must contain scalar x and numeric y fields, with at most 2000 rows'
        )
      const x = node.x as string
      const y = node.y as string
      const chartChildren = (
        <>
          <CartesianGrid vertical={false} />
          <XAxis dataKey={x} />
          <YAxis />
          <ChartTooltip content={<ChartTooltipContent />} />
        </>
      )
      return (
        <div className="space-y-3">
          {heading}
          <ChartContainer
            className="h-72 w-full"
            config={{ [y]: { label: node.label ?? y, color: 'hsl(var(--chart-1))' } }}
          >
            {node.chart_type === 'bar' ? (
              <BarChart data={value}>
                {chartChildren}
                <Bar dataKey={y} fill={`var(--color-${y})`} isAnimationActive={false} />
              </BarChart>
            ) : (
              <LineChart data={value}>
                {chartChildren}
                <Line
                  dataKey={y}
                  stroke={`var(--color-${y})`}
                  strokeWidth={2}
                  dot={{ r: 3, fill: `var(--color-${y})` }}
                  isAnimationActive={false}
                />
              </LineChart>
            )}
          </ChartContainer>
        </div>
      )
    }
    default:
      throw new Error('Unsupported component; migrate complex UI to a custom page')
  }
}

function NodesRenderer({ nodes, ...props }: RendererProps) {
  return (
    <>
      {nodes.map((node, index) => (
        <NodeRenderer key={index} node={node} {...props} />
      ))}
    </>
  )
}

export function PluginWebUIRenderer(props: RendererProps) {
  const revision = props.selectionRevision ?? 0
  const [state, setState] = useState<{
    revision: number
    selection: Record<string, unknown>
    openDialog: string | null
  }>({ revision, selection: {}, openDialog: null })
  // Row details belong to a query snapshot. Invalidate synchronously before children render.
  const current =
    state.revision === revision ? state : { revision, selection: {}, openDialog: null }
  validateRenderSize(props.nodes)
  return (
    <InteractionContext.Provider
      value={{
        selection: current.selection,
        openDialog: current.openDialog,
        select: (name, row, detail) =>
          setState({
            revision,
            selection: { ...current.selection, [name]: row },
            openDialog: detail ?? null,
          }),
        close: () => setState({ ...current, openDialog: null }),
      }}
    >
      <NodesRenderer {...props} />
    </InteractionContext.Provider>
  )
}
