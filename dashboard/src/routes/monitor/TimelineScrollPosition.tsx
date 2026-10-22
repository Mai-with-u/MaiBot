import { Component, type ReactNode } from 'react'
import type { VirtualItem } from '@tanstack/react-virtual'

export type TimelineScrollMode = 'follow' | 'read' | 'navigate'
type RowPosition = Pick<VirtualItem, 'key' | 'start'>

interface Props {
  viewport: HTMLDivElement | null
  sessionId: string | null
  positions: readonly RowPosition[]
  getMode: () => TimelineScrollMode
  children: ReactNode
}

type Snapshot =
  | { mode: 'follow'; viewport: HTMLDivElement }
  | { mode: 'read'; viewport: HTMLDivElement; top: number }
  | null

/**
 * 时间线唯一的自动保位入口，行高变化与历史插入/裁剪使用同一套提交前后快照。
 * 必须在 DOM 修改前读取真实 scrollTop：虚拟列表缓存可能落后一帧，DOM 缩短后再读又可能已被截断。
 * getSnapshotBeforeUpdate 正好提供这个时机；不要在 resizeItem 或 layout effect 里重复补偿。
 */
export class TimelineScrollPosition extends Component<Props, object, Snapshot> {
  getSnapshotBeforeUpdate(previous: Props): Snapshot {
    const { viewport, positions, sessionId, getMode } = this.props
    if (!viewport) return null
    const mode = getMode()
    if (mode === 'follow' || previous.sessionId !== sessionId) {
      return { mode: 'follow', viewport }
    }
    if (mode !== 'read' || previous.viewport !== viewport || previous.positions === positions) return null

    // 使用上次已提交的位置，按浏览器当前偏移二分查找；不依赖 scroll 事件是否已经派发。
    const top = viewport.scrollTop
    const oldPositions = previous.positions
    if (oldPositions.length === 0) return null
    let low = 0
    let high = oldPositions.length - 1
    while (low < high) {
      const middle = Math.ceil((low + high) / 2)
      if (oldPositions[middle].start <= top) low = middle
      else high = middle - 1
    }
    const anchor = oldPositions[low]
    const nextAnchor = positions.find((row) => row.key === anchor.key)
    // 阅读的记录已被删除时没有可保留的参照，交给浏览器限制合法滚动范围。
    if (!nextAnchor || nextAnchor.start === anchor.start) return null
    return { mode: 'read', viewport, top: top + nextAnchor.start - anchor.start }
  }

  componentDidMount() {
    const { viewport, getMode } = this.props
    if (viewport && getMode() === 'follow') this.followBottom(viewport)
  }

  componentDidUpdate(previous: Props, _state: object, snapshot: Snapshot) {
    if (!snapshot || snapshot.viewport !== this.props.viewport) return
    const mode = this.props.getMode()
    if (snapshot.mode === 'follow') {
      if (mode === 'follow' || previous.sessionId !== this.props.sessionId) {
        this.followBottom(snapshot.viewport)
      }
    } else if (mode === 'read') {
      // 此时新列表高度已写入 DOM，再恢复位置不会被旧 scrollHeight 截断。
      snapshot.viewport.scrollTop = Math.max(0, snapshot.top)
    }
  }

  private followBottom(viewport: HTMLDivElement) {
    const top = Math.max(0, viewport.scrollHeight - viewport.clientHeight)
    if (viewport.scrollTop !== top) viewport.scrollTop = top
  }

  render() {
    return this.props.children
  }
}
