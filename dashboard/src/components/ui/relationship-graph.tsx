import { useEffect, useRef, useState } from 'react'
import { MessageCircle, UsersRound } from 'lucide-react'

import { Button } from './button'
import { DataPanel } from './surface'
import { Switch } from './switch'
import { Label } from './label'
import { regionOutline, type RegionRect } from './relationship-regions'

export interface RelationshipNode {
  id: string
  label: string
  description?: string
  opacity?: number
  colors?: string[]
  column: number
}

export interface RelationshipEdge {
  from: string
  to: string
  opacity?: number
  color?: string
}

/** 分栏关系图：节点支持键盘操作，选中节点突出直接关联；独立曲线连接错开的端口，支持滚轮缩放和拖动平移。 */
export function RelationshipGraph({
  nodes,
  edges,
  columns,
  selectedId,
  layout = 'columns',
  onSelect,
}: {
  nodes: RelationshipNode[]
  edges: RelationshipEdge[]
  columns: string[]
  layout?: 'columns' | 'overview'
  selectedId?: string
  onSelect: (id: string) => void
}) {
  const [mergeShared, setMergeShared] = useState(false)
  const [focusedId, setFocusedId] = useState<string | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number } | null>(null)
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 })
  const [viewportWidth, setViewportWidth] = useState(1080)
  const [dragging, setDragging] = useState(false)
  useEffect(() => {
    const element = viewport.current!
    const observer = new ResizeObserver(() => setViewportWidth(element.clientWidth))
    observer.observe(element)
    const wheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = element.getBoundingClientRect()
      const x = event.clientX - rect.left
      const y = event.clientY - rect.top
      const delta =
        event.deltaY *
        (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1)
      setView((current) => {
        const scale = Math.max(0.2, Math.min(2.5, current.scale * Math.exp(-delta * 0.0015)))
        const ratio = scale / current.scale
        return { scale, x: x - (x - current.x) * ratio, y: y - (y - current.y) * ratio }
      })
    }
    element.addEventListener('wheel', wheel, { passive: false })
    return () => {
      observer.disconnect()
      element.removeEventListener('wheel', wheel)
    }
  }, [])
  const activeId = focusedId ?? selectedId
  const columnNodes = columns.map((_, index) => nodes.filter((node) => node.column === index))
  const overview = layout === 'overview'
  const groupColumn = overview ? 0 : 1
  const groupOrder = new Map(columnNodes[groupColumn]?.map((node, index) => [node.id, index]))
  const memberRank = (id: string) => {
    const ranks = edges
      .filter((edge) => edge.to === id)
      .map((edge) => groupOrder.get(edge.from) ?? 0)
    return ranks.length ? ranks.reduce((sum, rank) => sum + rank, 0) / ranks.length : 0
  }
  columnNodes[groupColumn + 1]?.sort((a, b) => memberRank(a.id) - memberRank(b.id))
  const groupNodes = columnNodes[0] ?? []
  const sharedMembers = (columnNodes[1] ?? []).filter(
    (node) => edges.filter((edge) => edge.to === node.id).length > 1
  )
  // 只合并归属完全相同的成员，避免把 A∩B 与 B∩C 画成三组共有。
  const overlapMap = new Map<string, { groups: RelationshipNode[]; members: RelationshipNode[] }>()
  for (const member of sharedMembers) {
    const owners = groupNodes.filter((group) =>
      edges.some((edge) => edge.from === group.id && edge.to === member.id)
    )
    const key = JSON.stringify(owners.map((group) => group.id))
    const region = overlapMap.get(key) ?? { groups: owners, members: [] }
    region.members.push(member)
    overlapMap.set(key, region)
  }
  const overlaps = [...overlapMap.values()]
  const sharedIds = new Set(sharedMembers.map((node) => node.id))
  const membersByGroup = groupNodes.map((group) =>
    (columnNodes[1] ?? []).filter(
      (node) =>
        !sharedIds.has(node.id) && edges.find((edge) => edge.to === node.id)?.from === group.id
    )
  )
  const sectionStarts = [60, 150]
  const regionHeight = Math.max(220, ...membersByGroup.map((items) => items.length * 88 + 120))
  const sharedTop = regionHeight + 80
  const sharedColumns = Math.max(1, groupNodes.length)
  const mergedBands = [
    ...membersByGroup
      .map((members, index) => ({ groups: [groupNodes[index]], members }))
      .filter((band) => band.members.length > 0),
    ...overlaps,
  ]
  const bandX = 320 + groupNodes.length * 16
  const bandColumns = Math.max(1, Math.floor((viewportWidth - bandX - 32) / 260))
  let bandBottom = 60
  const bands = mergedBands.map((band) => {
    const y = bandBottom
    const height = Math.ceil(band.members.length / bandColumns) * 88 + 24
    bandBottom += height + 24
    return { ...band, y, height }
  })
  const width =
    overview && mergeShared
      ? Math.max(viewportWidth, bandX + bandColumns * 260 + 32)
      : overview
        ? Math.max(viewportWidth, groupNodes.length * 280 + 32)
        : columns.length * 430
  const mergedRegions = new Map<string, RegionRect[]>()
  groupNodes.forEach((group, index) => {
    const headerY = 60 + index * 104
    const spineX = 280 + index * 16
    const ownBands = bands.filter((band) => band.groups.some((owner) => owner.id === group.id))
    const top = Math.min(headerY + 26, ...ownBands.map((band) => band.y + 12))
    const bottom = Math.max(headerY + 42, ...ownBands.map((band) => band.y + band.height - 12))
    mergedRegions.set(group.id, [
      { x: 20, y: headerY, width: 244, height: 72 },
      { x: 256, y: headerY + 26, width: spineX - 248, height: 16 },
      { x: spineX, y: top, width: 8, height: bottom - top },
      ...ownBands.map((band) => ({
        x: spineX,
        y: band.y + 12,
        width: bandX - spineX + bandColumns * 260,
        height: band.height - 24,
      })),
    ])
  })
  const height =
    overview && mergeShared
      ? Math.max(bandBottom, groupNodes.length * 104 + 80)
      : overview
        ? sharedTop +
          (sharedMembers.length ? Math.ceil(sharedMembers.length / sharedColumns) * 88 + 80 : 0)
        : Math.max(260, ...columnNodes.map((items) => items.length * 88 + 72))
  const positions = new Map<string, { x: number; y: number }>()
  if (overview) {
    groupNodes.forEach((group, index) => {
      positions.set(group.id, { x: index * 280 + 32, y: 60 })
      membersByGroup[index].forEach((node, row) =>
        positions.set(node.id, { x: index * 280 + 32, y: 150 + row * 88 })
      )
    })
    if (mergeShared) {
      groupNodes.forEach((group, index) => positions.set(group.id, { x: 32, y: 60 + index * 104 }))
      bands.forEach((band) =>
        band.members.forEach((node, index) =>
          positions.set(node.id, {
            x: bandX + 12 + (index % bandColumns) * 260,
            y: band.y + 16 + Math.floor(index / bandColumns) * 88,
          })
        )
      )
    } else {
      sharedMembers.forEach((node, index) =>
        positions.set(node.id, {
          x: (index % sharedColumns) * 280 + 32,
          y: sharedTop + 56 + Math.floor(index / sharedColumns) * 88,
        })
      )
    }
  } else {
    columnNodes.forEach((items, column) =>
      items.forEach((node, row) =>
        positions.set(node.id, { x: column * 430 + 24, y: row * 88 + 60 })
      )
    )
  }
  const fit = () => {
    const element = viewport.current!
    const scale = Math.max(
      0.2,
      Math.min(1, (element.clientWidth - 32) / width, (element.clientHeight - 64) / height)
    )
    setView({ scale, x: (element.clientWidth - width * scale) / 2, y: 48 })
  }
  const linked = new Set([
    activeId,
    ...edges.flatMap((edge) =>
      edge.from === activeId ? [edge.to] : edge.to === activeId ? [edge.from] : []
    ),
  ])
  return (
    <DataPanel
      ref={viewport}
      inset
      padding="none"
      className="relative h-full min-h-0 touch-none overflow-hidden border-2"
      style={{ cursor: dragging ? 'grabbing' : 'grab' }}
      onPointerDown={(event) => {
        if (event.button !== 0 && event.button !== 1) return
        if (event.button === 0 && (event.target as HTMLElement).closest('button')) return
        event.preventDefault()
        drag.current = { x: event.clientX, y: event.clientY }
        setDragging(true)
        event.currentTarget.setPointerCapture(event.pointerId)
      }}
      onMouseDown={(event) => {
        if (event.button === 1) event.preventDefault()
      }}
      onAuxClick={(event) => {
        if (event.button === 1) event.preventDefault()
      }}
      onPointerMove={(event) => {
        if (!drag.current) return
        const dx = event.clientX - drag.current.x
        const dy = event.clientY - drag.current.y
        drag.current = { x: event.clientX, y: event.clientY }
        setView((current) => ({ ...current, x: current.x + dx, y: current.y + dy }))
      }}
      onPointerUp={() => {
        drag.current = null
        setDragging(false)
      }}
      onPointerCancel={() => {
        drag.current = null
        setDragging(false)
      }}
      onLostPointerCapture={() => {
        drag.current = null
        setDragging(false)
      }}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return
        const moves: Record<string, [number, number]> = {
          ArrowLeft: [40, 0],
          ArrowRight: [-40, 0],
          ArrowUp: [0, 40],
          ArrowDown: [0, -40],
        }
        const move = moves[event.key]
        if (move) {
          event.preventDefault()
          setView((current) => ({ ...current, x: current.x + move[0], y: current.y + move[1] }))
        }
      }}
      role="region"
      aria-label="相通关系图"
      tabIndex={0}
    >
      <div className="bg-background absolute top-3 right-3 z-10 flex flex-wrap items-center gap-2 p-1">
        {overview && (
          <Label className="flex cursor-pointer items-center gap-2 text-xs">
            <Switch
              checked={mergeShared}
              onCheckedChange={(checked) => {
                setMergeShared(checked)
                setView({ x: 0, y: 0, scale: 1 })
              }}
              aria-label="合并共同区"
            />
            合并共同区
          </Label>
        )}
        <span className="text-xs tabular-nums" aria-live="polite">
          {Math.round(view.scale * 100)}%
        </span>
        <Button size="xs" variant="outline" onClick={fit}>
          适应全图
        </Button>
        <Button size="xs" variant="outline" onClick={() => setView({ x: 0, y: 0, scale: 1 })}>
          重置
        </Button>
      </div>
      <div
        className="relative origin-top-left select-none"
        style={{
          width,
          height,
          transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
        }}
      >
        {overview && mergeShared && (
          <svg
            aria-hidden="true"
            className="pointer-events-none absolute inset-0"
            width={width}
            height={height}
          >
            {groupNodes.map((group) => (
              <path
                key={group.id}
                data-group-region={group.id}
                d={regionOutline(mergedRegions.get(group.id)!)}
                fill={group.colors?.[0] ?? '#888888'}
                fillOpacity={0.12 * (group.opacity ?? 1)}
                stroke={group.colors?.[0] ?? '#888888'}
                strokeWidth={2}
                opacity={activeId && !linked.has(group.id) ? 0.3 : 1}
              />
            ))}
          </svg>
        )}
        {overview &&
          groupNodes.map((group, index) => (
            <section
              key={`region:${group.id}`}
              aria-label={`${group.label}相通组`}
              className={mergeShared ? 'absolute' : 'absolute border-2'}
              style={{
                left: mergeShared ? 20 : index * 280 + 20,
                top: mergeShared ? 60 + index * 104 : 48,
                width: mergeShared ? 244 : 254,
                height: mergeShared ? 72 : regionHeight,
                borderColor: group.colors?.[0],
                backgroundColor: mergeShared
                  ? 'transparent'
                  : `${group.colors?.[0] ?? '#888888'}12`,
                opacity: (group.opacity ?? 1) * (activeId && !linked.has(group.id) ? 0.4 : 1),
              }}
            >
              <button
                type="button"
                className={`hover:bg-accent flex w-full cursor-pointer items-start gap-2 px-3 py-3 text-left focus-visible:outline-2 ${mergeShared ? 'h-full' : 'border-b-2'}`}
                style={{ borderColor: group.colors?.[0] }}
                title={`编辑${group.label}`}
                onClick={() => onSelect(group.id)}
                onMouseEnter={() => setFocusedId(group.id)}
                onMouseLeave={() => setFocusedId(null)}
                onFocus={() => setFocusedId(group.id)}
                onBlur={() => setFocusedId(null)}
              >
                <UsersRound
                  className="mt-0.5 h-5 w-5 shrink-0"
                  style={{ color: group.colors?.[0] }}
                />
                <span className="min-w-0">
                  <strong className="block truncate">{group.label}</strong>
                  <span className="text-muted-foreground block truncate text-xs">
                    {group.description}
                  </span>
                </span>
              </button>
              {!mergeShared && membersByGroup[index].length === 0 && (
                <p className="text-muted-foreground px-3 pt-8 text-xs">
                  {edges.some((edge) => edge.from === group.id)
                    ? mergeShared
                      ? '成员位于下方交叠区域'
                      : '成员均位于下方共同成员区'
                    : '暂无成员，点击组名添加'}
                </p>
              )}
            </section>
          ))}
        {overview && !mergeShared && sharedMembers.length > 0 && (
          <section
            aria-label="共同成员"
            className="absolute border-2 border-dashed"
            style={{
              left: 20,
              top: sharedTop,
              width: sharedColumns * 280 - 26,
              height: Math.ceil(sharedMembers.length / sharedColumns) * 88 + 72,
            }}
          >
            <h3 className="px-3 py-3 text-sm font-semibold">
              共同成员
              <span className="text-muted-foreground ml-2 text-xs font-normal">
                多色标记对应所属相通组，每个聊天只显示一次
              </span>
            </h3>
          </section>
        )}
        {(overview ? ['点击组名编辑 · 悬停聊天查看所属组'] : columns).map((label, index) => (
          <div
            key={label}
            className="text-muted-foreground absolute text-xs font-semibold"
            style={{
              left: overview ? 32 : index * 430 + 24,
              top: overview ? sectionStarts[index] - 36 : 20,
            }}
          >
            {label}
          </div>
        ))}
        <svg
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          width="100%"
          height="100%"
        >
          {[...edges]
            .filter(() => !overview)
            .sort(
              (a, b) =>
                Number(a.from === activeId || a.to === activeId) -
                Number(b.from === activeId || b.to === activeId)
            )
            .map((edge) => {
              const from = positions.get(edge.from)!
              const to = positions.get(edge.to)!
              const active = edge.from === activeId || edge.to === activeId
              const outgoing = edges
                .filter((item) => item.from === edge.from)
                .sort((a, b) => positions.get(a.to)!.y - positions.get(b.to)!.y)
              const incoming = edges
                .filter((item) => item.to === edge.to)
                .sort((a, b) => positions.get(a.from)!.y - positions.get(b.from)!.y)
              const fromY =
                from.y + 10 + (40 * (outgoing.indexOf(edge) + 1)) / (outgoing.length + 1)
              const toY = to.y + 10 + (40 * (incoming.indexOf(edge) + 1)) / (incoming.length + 1)
              const sourceX =
                from.x + 16 + (198 * (outgoing.indexOf(edge) + 1)) / (outgoing.length + 1)
              // 连线沿网格间隙走，避免穿过上排成员卡片。
              const laneX = to.x - 12 - incoming.indexOf(edge) * 4
              const bendY = sectionStarts[1] - 58 + (outgoing.indexOf(edge) % 5) * 5
              const path = overview
                ? `M ${sourceX} ${from.y + 60} C ${sourceX} ${bendY}, ${laneX} ${bendY - 24}, ${laneX} ${bendY} V ${toY} H ${to.x}`
                : `M ${from.x + 230} ${fromY} C ${from.x + 330} ${fromY}, ${to.x - 100} ${toY}, ${to.x} ${toY}`
              return (
                <path
                  key={`${edge.from}:${edge.to}`}
                  d={path}
                  fill="none"
                  stroke={edge.color ?? 'currentColor'}
                  strokeWidth={active ? 4 : 3}
                  opacity={(edge.opacity ?? 1) * (active ? 1 : activeId ? 0.12 : 0.65)}
                />
              )
            })}
        </svg>
        {nodes
          .filter((node) => !overview || node.column !== 0)
          .map((node) => {
            const position = positions.get(node.id)!
            return (
              <Button
                key={node.id}
                variant="outline"
                className="absolute h-[60px] w-[230px] flex-col items-start gap-1 border-2 px-3 text-left"
                style={{
                  left: position.x,
                  top: position.y,
                  opacity: (node.opacity ?? 1) * (activeId && !linked.has(node.id) ? 0.5 : 1),
                }}
                aria-pressed={selectedId === node.id}
                title={[node.label, node.description].filter(Boolean).join(' · ')}
                onMouseEnter={() => setFocusedId(node.id)}
                onMouseLeave={() => setFocusedId(null)}
                onFocus={() => setFocusedId(node.id)}
                onBlur={() => setFocusedId(null)}
                onClick={() => onSelect(node.id)}
              >
                <span aria-hidden="true" className="absolute inset-x-0 top-0 flex h-1.5">
                  {node.colors?.map((color) => (
                    <span key={color} className="flex-1" style={{ backgroundColor: color }} />
                  ))}
                </span>
                <span className="flex w-full items-center gap-2 font-medium">
                  <MessageCircle aria-hidden="true" className="h-4 w-4 shrink-0 opacity-60" />
                  <span className="truncate">{node.label}</span>
                </span>
                <span className="text-muted-foreground block w-full truncate text-xs">
                  {node.description}
                </span>
              </Button>
            )
          })}
      </div>
    </DataPanel>
  )
}
