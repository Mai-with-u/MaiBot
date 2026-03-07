import type { ReactNode } from 'react'
import { createContext, useCallback, useContext, useRef, useState } from 'react'

type Politeness = 'polite' | 'assertive'

interface AnnouncerContextValue {
  announce: (message: string, politeness?: Politeness) => void
}

const AnnouncerContext = createContext<AnnouncerContextValue | null>(null)

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

interface AnnouncerState {
  polite: string
  assertive: string
}

/**
 * AnnouncerProvider — 在应用根部挂载两个 aria-live 区域
 *
 * 将此组件包裹在应用根节点，所有子组件即可通过 useAnnounce() 播报消息。
 * aria-live 区域视觉上隐藏（sr-only），不影响布局。
 */
export function AnnouncerProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<AnnouncerState>({ polite: '', assertive: '' })
  // 用于清空 -> 重新设置，触发屏幕阅读器重新朗读相同消息
  const politeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const assertiveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const announce = useCallback((message: string, politeness: Politeness = 'polite') => {
    if (politeness === 'assertive') {
      // 先清空，再填入，确保屏幕阅读器重新朗读
      setMessages((prev: AnnouncerState) => ({ ...prev, assertive: '' }))
      if (assertiveTimerRef.current) clearTimeout(assertiveTimerRef.current)
      assertiveTimerRef.current = setTimeout(() => {
        setMessages((prev: AnnouncerState) => ({ ...prev, assertive: message }))
      }, 50)
    } else {
      setMessages((prev: AnnouncerState) => ({ ...prev, polite: '' }))
      if (politeTimerRef.current) clearTimeout(politeTimerRef.current)
      politeTimerRef.current = setTimeout(() => {
        setMessages((prev: AnnouncerState) => ({ ...prev, polite: message }))
      }, 50)
    }
  }, [])

  return (
    <AnnouncerContext.Provider value={{ announce }}>
      {children}
      {/* aria-live 区域：视觉隐藏，屏幕阅读器可读 */}
      <div
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      >
        {messages.polite}
      </div>
      <div
        aria-live="assertive"
        aria-atomic="true"
        className="sr-only"
      >
        {messages.assertive}
      </div>
    </AnnouncerContext.Provider>
  )
}
