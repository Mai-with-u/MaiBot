import { createContext, useContext, useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart'
import { Input } from '@/components/ui/input'
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
import { nodeVisible, resolveNodeValue, validateRenderSize } from '@/lib/plugin-webui'
import type { DataContexts, Scalar, WebUINode } from '@/lib/plugin-webui'
import { prepareUploadImage } from '@/lib/upload-image'
import type { PreparedImage } from '@/lib/upload-image'

interface RendererProps {
  nodes: WebUINode[]
  data: Record<string, unknown>
  values: Record<string, Scalar>
  busy: boolean
  pendingData?: boolean
  selectionRevision?: number
  galleryPreferences?: Record<string, string>
  onChange: (name: string, value: Scalar) => void
  onAction: (name: string, contexts?: DataContexts) => void
  onUpload?: (name: string, file: File, progress: (percent: number) => void) => Promise<unknown>
  onUploadComplete?: () => Promise<void>
  items?: Record<string, unknown>
  compact?: boolean
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

// Gallery previews are bounded raster data, never plugin-supplied HTML or remote tracking URLs.
function galleryThumbnail(value: unknown): string | undefined {
  return typeof value === 'string' &&
    value.length <= 24000 &&
    /^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(value)
    ? value
    : undefined
}

function galleryLink(value: unknown): string | undefined {
  if (typeof value !== 'string' || value.length > 2000) return undefined
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined
  } catch {
    return undefined
  }
}

function UploadControl({ label, action, busy, upload, complete, maxEdge }: {
  label: string; action: string; busy: boolean; upload: RendererProps['onUpload']; complete: RendererProps['onUploadComplete']; maxEdge?: number | null
}) {
  const id = useId()
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState<Array<{ name: string; progress: number; error?: string; note?: string }>>([])
  return <div className="space-y-2">
    <Label htmlFor={id}>{label}</Label>
    {!upload && <p role="alert">Host does not support file_upload_v1</p>}
    <Input id={id} type="file" multiple accept="image/jpeg,image/png,image/webp" disabled={busy || running || !upload}
      onChange={async (event) => {
        const files = Array.from(event.target.files ?? [])
        event.target.value = ''
        setStatus(files.map(file => ({ name: file.name, progress: 0 })))
        setRunning(true)
        const update = (index: number, change: { progress?: number; error?: string; note?: string }) =>
          setStatus(rows => rows.map((row, i) => i === index ? { ...row, ...change } : row))
        try {
          for (const [index, file] of files.entries()) {
            try {
              const prepared: PreparedImage = maxEdge ? await prepareUploadImage(file, maxEdge) : { file }
              if (prepared.file.size > 20 * 1024 * 1024) throw new Error('File exceeds 20 MiB')
              if (prepared.note) update(index, { note: prepared.note })
              await upload!(action, prepared.file, percent => update(index, { progress: percent }))
              update(index, { progress: 100 })
            } catch (error) { update(index, { error: String(error) }) }
          }
          await complete?.()
        } finally { setRunning(false) }
      }} />
    {status.map((row, index) => <div key={index} role={row.error ? 'alert' : 'status'}>
      {row.name}: {row.error ?? `${row.progress}%`}
      {row.note && <span className="ml-2 text-muted-foreground">{row.note}</span>}
      {!row.error && <progress max={100} value={row.progress} aria-label={row.name} />}
    </div>)}
  </div>
}

function NodeRenderer({ node, ...props }: Omit<RendererProps, 'nodes'> & { node: WebUINode }) {
  const { t } = useTranslation()
  const id = useId()
  const [tablePage, setTablePage] = useState(0)
  const [expanded, setExpanded] = useState(node.default_open ?? false)
  const interaction = useContext(InteractionContext)!
  const contexts: DataContexts = { selection: interaction.selection, item: props.items }
  const galleryKey =
    node.value !== null && typeof node.value === 'object'
      ? `${node.value.source}.${node.value.field}`
      : ''
  const [galleryColumns, setGalleryColumns] = useState(
    () => props.galleryPreferences?.[galleryKey] ?? String(node.columns ?? 'auto')
  )
  const awaitingData = props.pendingData && node.value !== null && typeof node.value === 'object'
  const visible = props.pendingData && node.when ? false : nodeVisible(node, props.data, contexts)
  const value = awaitingData || !visible ? null : resolveNodeValue(node, props.data, contexts)
  const children = <NodesRenderer {...props} compact={props.compact || node.compact} nodes={node.children} />
  const fieldValue = node.name === null ? null : props.values[node.name]
  const change = (next: Scalar) => {
    if (node.name !== null) props.onChange(node.name, next)
  }
  const heading = node.label ? <Label htmlFor={id}>{node.label}</Label> : null

  if (!visible) return null

  if (awaitingData)
    return (
      <p role="status" className="text-muted-foreground text-sm">
        {props.busy ? t('pluginWebUI.loading') : t('pluginWebUI.empty')}
      </p>
    )

  switch (node.type) {
    case 'image': {
      const source = galleryThumbnail(value)
      return source ? <img src={source} alt={node.label ?? ''} loading="lazy" decoding="async"
        className={`${props.compact ? 'h-40' : 'h-72'} w-full object-contain bg-muted rounded-md`} /> :
        <p className="text-muted-foreground text-sm">{t('pluginWebUI.empty')}</p>
    }
    case 'upload':
      return <UploadControl label={node.label!} action={node.action!} busy={props.busy} upload={props.onUpload} complete={props.onUploadComplete} maxEdge={node.image_max_edge} />
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
    case 'repeat': {
      if (!Array.isArray(value)) throw new Error('Repeat data must be an array')
      return (
        <>
          {value.slice(0, node.max_items ?? 50).map((item, index) => (
            <NodesRenderer
              key={index}
              {...props}
              items={{ ...props.items, [node.name!]: item }}
              nodes={node.children}
            />
          ))}
        </>
      )
    }
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
            className={props.compact ? `grid gap-1 ${({1:'grid-cols-1',2:'grid-cols-2',3:'grid-cols-3',4:'grid-cols-4'})[node.columns as 1|2|3|4]}` : `grid grid-cols-1 gap-4 ${columns[node.columns as keyof typeof columns]}`}
          >
            {children}
          </div>
        </section>
      )
    }
    case 'card':
      return (
        <Card className={node.compact ? 'py-0 gap-0' : undefined}>
          {node.label && (
            <CardHeader>
              <CardTitle>{node.label}</CardTitle>
            </CardHeader>
          )}
          <CardContent className={node.compact ? 'p-2 sm:p-2 space-y-1' : 'space-y-4 pt-6'}>{children}</CardContent>
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
      return (
        <div className={props.compact ? 'space-y-0 text-xs' : 'space-y-2'}>
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
            disabled={props.busy}
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
            disabled={props.busy}
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
            disabled={props.busy}
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
        <Button
          size={props.compact ? 'sm' : 'default'}
          className={props.compact ? 'h-7 px-1 text-xs' : undefined}
          disabled={props.busy}
          variant={
            node.variant === 'danger'
              ? 'destructive'
              : node.variant === 'muted'
                ? 'secondary'
                : 'default'
          }
          onClick={() => {
            if (node.action !== null) props.onAction(node.action, contexts)
          }}
        >
          {node.label}
        </Button>
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
              disabled={props.busy || current === 1}
              onClick={() => change(current - 1)}
            >
              {t('pluginWebUI.previous')}
            </Button>
            <Button
              variant="outline"
              disabled={props.busy || current === pages}
              onClick={() => change(current + 1)}
            >
              {t('pluginWebUI.next')}
            </Button>
          </div>
        </nav>
      )
    }
    case 'gallery': {
      if (
        !Array.isArray(value) ||
        value.length > 24 ||
        value.some((row) => row === null || typeof row !== 'object' || Array.isArray(row))
      )
        throw new Error('Gallery data must be an array of at most 24 objects')
      const columns = {
        1: 'md:grid-cols-1',
        2: 'sm:grid-cols-2',
        3: 'sm:grid-cols-2 lg:grid-cols-3',
        4: 'sm:grid-cols-2 lg:grid-cols-4',
      }
      return (
        <section className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            {heading}
            <Select
              value={galleryColumns}
              onValueChange={(next) => {
                if (props.galleryPreferences) props.galleryPreferences[galleryKey] = next
                setGalleryColumns(next)
              }}
            >
              <SelectTrigger aria-label={t('pluginWebUI.galleryLayout')} className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">{t('pluginWebUI.galleryAuto')}</SelectItem>
                {[1, 2, 3, 4].map((count) => (
                  <SelectItem key={count} value={String(count)}>
                    {t('pluginWebUI.galleryColumns', { count })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div
            className={`grid gap-4 ${galleryColumns === 'auto' ? '' : 'grid-cols-1 ' + columns[Number(galleryColumns) as keyof typeof columns]}`}
            style={
              galleryColumns === 'auto'
                ? { gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 240px), 1fr))' }
                : undefined
            }
          >
            {(value as Record<string, unknown>[]).map((row, index) => {
              const thumbnail = galleryThumbnail(row.thumbnail)
              const href = galleryLink(row.url)
              return (
                <Card key={index} className="overflow-hidden py-0">
                  <div className="bg-muted flex h-56 items-center justify-center">
                    {thumbnail ? (
                      <img
                        src={thumbnail}
                        alt={display(row.title)}
                        loading="lazy"
                        decoding="async"
                        className="h-full w-full object-contain"
                      />
                    ) : (
                      <p className="text-muted-foreground text-sm">{t('pluginWebUI.empty')}</p>
                    )}
                  </div>
                  <CardContent className="space-y-2 p-4">
                    <div className="flex items-center justify-between gap-2">
                      <span className="bg-muted rounded-full px-2 py-1 text-xs">
                        {display(row.badge)}
                      </span>
                      <span className="text-muted-foreground text-xs">{display(row.subtitle)}</span>
                    </div>
                    <p className="truncate font-medium" title={display(row.title)}>
                      {display(row.title)}
                    </p>
                    <p className="text-muted-foreground truncate text-sm">{display(row.author)}</p>
                    <p className="text-muted-foreground text-xs">{display(row.description)}</p>
                    {href && (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary text-sm underline underline-offset-4"
                      >
                        {href}
                      </a>
                    )}
                  </CardContent>
                </Card>
              )
            })}
          </div>
          {value.length === 0 && (
            <p className="text-muted-foreground text-sm">{t('pluginWebUI.empty')}</p>
          )}
        </section>
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
            config={{ [y]: { label: node.label ?? y, color: 'var(--primary)' } }}
          >
            {node.chart_type === 'bar' ? (
              <BarChart data={value}>
                {chartChildren}
                <Bar dataKey={y} fill="var(--primary)" isAnimationActive={false} />
              </BarChart>
            ) : (
              <LineChart data={value}>
                {chartChildren}
                <Line dataKey={y} stroke="var(--primary)" dot={false} isAnimationActive={false} />
              </LineChart>
            )}
          </ChartContainer>
        </div>
      )
    }
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
  const [selection, setSelection] = useState<Record<string, unknown>>({})
  const [openDialog, setOpenDialog] = useState<string | null>(null)
  useEffect(() => {
    setSelection({})
    setOpenDialog(null)
  }, [props.selectionRevision])
  if (!props.pendingData)
    validateRenderSize(props.nodes, props.data, { selection, item: props.items }, openDialog)
  return (
    <InteractionContext.Provider
      value={{
        selection,
        openDialog,
        select: (name, row, detail) => {
          setSelection((previous) => ({ ...previous, [name]: row }))
          if (detail) setOpenDialog(detail)
        },
        close: () => setOpenDialog(null),
      }}
    >
      <NodesRenderer {...props} />
    </InteractionContext.Provider>
  )
}
