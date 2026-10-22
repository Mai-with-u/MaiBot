export const DAY_MINUTES = 24 * 60
export const TIMELINE_TICKS = [0, 3, 6, 9, 12, 15, 18, 21, 24]
export const TIMELINE_DRAG_STEP_MINUTES = 5
export interface TimelineRange {
  start: number
  end: number
}

export function formatTimelineMinute(minute: number): string {
  const value = Math.max(0, Math.min(DAY_MINUTES - 1, Math.round(minute)))
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
}

export function parseTalkTimeRange(time: string): TimelineRange | null {
  const match = /^(\d{1,2}):(\d{1,2})-(\d{1,2}):(\d{1,2})$/.exec(time.trim())
  if (!match) return null
  const [sh, sm, eh, em] = match.slice(1).map(Number)
  if (sh > 23 || eh > 23 || sm > 59 || em > 59) return null
  return { start: sh * 60 + sm, end: eh * 60 + em }
}

export function formatTalkTimeRange(range: TimelineRange): string {
  return `${formatTimelineMinute(range.start)}-${formatTimelineMinute(range.end)}`
}

/** 结束分钟包含在范围内；跨午夜拆成两段显示。 */
export function getTimelineSegments({
  start,
  end,
}: TimelineRange): { left: number; width: number }[] {
  const segment = (from: number, to: number) => ({
    left: (from / DAY_MINUTES) * 100,
    width: ((to - from) / DAY_MINUTES) * 100,
  })
  return start <= end
    ? [segment(start, end + 1)]
    : [segment(start, DAY_MINUTES), segment(0, end + 1)]
}

/** 先吸附五分钟，再夹到合法范围，最右端始终是 23:59。 */
export function timelineMinuteAtPosition(clientX: number, left: number, width: number): number {
  if (width <= 0) return 0
  const minute =
    Math.round((((clientX - left) / width) * DAY_MINUTES) / TIMELINE_DRAG_STEP_MINUTES) *
    TIMELINE_DRAG_STEP_MINUTES
  return Math.max(0, Math.min(DAY_MINUTES - 1, minute))
}
