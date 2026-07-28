import { createContext, useContext } from 'react'

export type Politeness = 'polite' | 'assertive'

export interface AnnouncerContextValue {
  announce: (message: string, politeness?: Politeness) => void
}

export const AnnouncerContext = createContext<AnnouncerContextValue | null>(null)

/**
 * useAnnounce — 向屏幕阅读器播报消息
 *
 * @example
 * const announce = useAnnounce()
 * announce('保存成功')                    // polite（默认）
 * announce('操作失败，请重试', 'assertive') // assertive（立即打断）
 */
export function useAnnounce(): (message: string, politeness?: Politeness) => void {
  const ctx = useContext(AnnouncerContext)
  if (!ctx) {
    // 未在 AnnouncerProvider 内时静默降级，不抛错
    return () => {}
  }
  return ctx.announce
}
