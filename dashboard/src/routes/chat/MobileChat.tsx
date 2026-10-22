import {
  Bot,
  Camera,
  ChevronLeft,
  Loader2,
  Search,
  Settings,
  UserCircle2,
  UserRound,
  UsersRound,
} from 'lucide-react'
import { motion } from 'motion/react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { MOBILE_GLASS_CLASS } from '@/components/layout/mobile-glass'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { cn } from '@/lib/utils'
import type { SessionInfo, StageStatusInfo } from '@/routes/monitor/use-maisaka-monitor'
import { StageStatusLabel } from '@/routes/monitor/StageStatusLabel'

import type { ChatTab, ObservedMessagePreview } from './types'
import { getChatTabDisplayName } from './utils'

/** 左滑露出的操作按钮宽度 */
const SWIPE_ACTION_WIDTH = 76
const SWIPE_OPEN_THRESHOLD = 40

/** 按同方向累计位移切换顶栏，过滤慢滑的小步进和手指抖动。 */
const FLOATING_HEADER_HIDE_DISTANCE = 32
const FLOATING_HEADER_SHOW_DISTANCE = 16
/** 连续滚动事件延续手势，包含松手后的惯性；自动跟随新消息不收起顶栏。 */
const FLOATING_HEADER_SCROLL_IDLE_MS = 180

interface MobileFloatingHeaderProps {
  children: ReactNode
}

/**
 * 对话页的悬浮顶栏：盖在内容上方，内容从它下面滚过。
 * 向下浏览时上滑收起，向上回滚时滑回；高度写到父元素的 `--observe-header-h`，供内容留出顶部空间。
 */
export function MobileFloatingHeader({ children }: MobileFloatingHeaderProps) {
  const headerRef = useRef<HTMLElement>(null)
  const [hidden, setHidden] = useState(false)
  const [height, setHeight] = useState(0)

  useLayoutEffect(() => {
    const header = headerRef.current
    const container = header?.parentElement
    if (!header || !container) return
    const update = () => {
      setHeight(header.offsetHeight)
      container.style.setProperty('--observe-header-h', `${header.offsetHeight}px`)
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(header)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const container = headerRef.current?.parentElement
    if (!container) return
    let lastGestureAt = -Infinity
    let directionDistance = 0
    let gestureViewport: HTMLElement | null = null
    const lastScrollTops = new WeakMap<HTMLElement, number>()
    const markGesture = (event: Event) => {
      const target = event.target
      if (!(target instanceof Element) || headerRef.current?.contains(target)) return
      const viewport = target.closest<HTMLElement>('[data-radix-scroll-area-viewport]')
      if (!viewport) return
      if (event.type === 'touchstart' || gestureViewport !== viewport || Date.now() - lastGestureAt > FLOATING_HEADER_SCROLL_IDLE_MS) {
        directionDistance = 0
        lastScrollTops.set(viewport, viewport.scrollTop)
      }
      gestureViewport = viewport
      lastGestureAt = Date.now()
    }
    const handleScroll = (event: Event) => {
      const target = event.target
      // 只观察时间线视口，代码块等内嵌滚动区域不控制顶栏。
      if (!(target instanceof HTMLElement) || !target.matches('[data-radix-scroll-area-viewport]')) return
      const scrollTop = Math.max(0, Math.min(target.scrollTop, target.scrollHeight - target.clientHeight))
      const previous = lastScrollTops.get(target) ?? scrollTop
      lastScrollTops.set(target, scrollTop)
      // 回到顶部附近始终显示，边界回弹不触发方向切换。
      if (scrollTop <= 8) {
        directionDistance = 0
        setHidden(false)
        return
      }
      if (target !== gestureViewport || Date.now() - lastGestureAt > FLOATING_HEADER_SCROLL_IDLE_MS) return
      lastGestureAt = Date.now()
      const delta = scrollTop - previous
      if (delta === 0) return
      directionDistance = Math.sign(delta) === Math.sign(directionDistance)
        ? directionDistance + delta
        : delta
      if (directionDistance >= FLOATING_HEADER_HIDE_DISTANCE) setHidden(true)
      else if (directionDistance <= -FLOATING_HEADER_SHOW_DISTANCE) setHidden(false)
    }
    container.addEventListener('touchstart', markGesture, { passive: true })
    container.addEventListener('touchmove', markGesture, { passive: true })
    container.addEventListener('wheel', markGesture, { passive: true })
    container.addEventListener('scroll', handleScroll, { capture: true, passive: true })
    return () => {
      container.removeEventListener('touchstart', markGesture)
      container.removeEventListener('touchmove', markGesture)
      container.removeEventListener('wheel', markGesture)
      container.removeEventListener('scroll', handleScroll, { capture: true })
    }
  }, [])

  return (
    <motion.header
      ref={headerRef}
      data-mobile-floating-header="true"
      data-hidden={hidden ? 'true' : 'false'}
      initial={false}
      animate={{ y: hidden ? -height : 0, opacity: hidden ? 0 : 1 }}
      transition={{ type: 'spring', stiffness: 420, damping: 38, mass: 0.8 }}
      className="pointer-events-none absolute inset-x-0 top-0 z-30 [&>*]:pointer-events-auto"
    >
      {children}
    </motion.header>
  )
}

/** 会话行的时间：今天显示时刻，昨天显示「昨天」，更早显示日期 */
function formatRowTime(seconds: number | undefined, language: string): string {
  if (!seconds) return ''
  const date = new Date(seconds * 1000)
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (date.getTime() >= startOfToday) {
    return new Intl.DateTimeFormat(language, { hour: '2-digit', minute: '2-digit' }).format(date)
  }
  if (date.getTime() >= startOfToday - 86400_000) {
    return new Intl.RelativeTimeFormat(language, { numeric: 'auto' }).format(-1, 'day')
  }
  return new Intl.DateTimeFormat(language, { month: 'numeric', day: 'numeric' }).format(date)
}

interface SwipeAction {
  label: string
  destructive?: boolean
  onClick: () => void
}

/**
 * 可左滑露出操作的会话行。操作按钮始终在 DOM 中（被行内容盖住），
 * 键盘与读屏用户可以直接聚焦到它们，滑动只是触屏上的快捷方式。
 */
function SwipeRow({ actions, children }: { actions: SwipeAction[]; children: ReactNode }) {
  const [offset, setOffset] = useState(0)
  const dragRef = useRef<{
    pointerId: number
    startX: number
    startOffset: number
    moved: boolean
  } | null>(null)
  const maxOffset = actions.length * SWIPE_ACTION_WIDTH

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (actions.length === 0 || event.pointerType === 'mouse') return
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startOffset: offset,
      moved: false,
    }
  }
  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const deltaX = event.clientX - drag.startX
    if (!drag.moved && Math.abs(deltaX) < 8) return
    drag.moved = true
    setOffset(Math.min(maxOffset, Math.max(0, drag.startOffset - deltaX)))
  }
  const handlePointerEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    dragRef.current = null
    if (drag.moved) {
      setOffset((current) => (current > SWIPE_OPEN_THRESHOLD ? maxOffset : 0))
    }
  }

  return (
    <div data-mobile-chat-swipe-row="true" className="relative overflow-hidden">
      {actions.length > 0 && (
        <div className="absolute inset-y-0 right-0 flex">
          {actions.map((action) => (
            <button
              key={action.label}
              type="button"
              data-mobile-chat-row-action={action.destructive ? 'destructive' : 'default'}
              onClick={() => {
                setOffset(0)
                action.onClick()
              }}
              style={{ width: SWIPE_ACTION_WIDTH }}
              className={cn(
                'text-sm font-medium text-white',
                action.destructive ? 'bg-[#ff3b30]' : 'bg-[#8e8e93]'
              )}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
      <div
        className="bg-background relative touch-pan-y transition-transform duration-200 ease-out"
        style={{ transform: `translateX(${-offset}px)` }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onClickCapture={(event) => {
          // 已经滑开时，点按行先收回而不是进入对话
          if (offset > 0) {
            event.preventDefault()
            event.stopPropagation()
            setOffset(0)
          }
        }}
      >
        {children}
      </div>
    </div>
  )
}

function ChatRow({
  avatar,
  title,
  badge,
  preview,
  time,
  onOpen,
}: {
  avatar: ReactNode
  title: string
  badge?: string
  preview: ReactNode
  time?: string
  onOpen: () => void
}) {
  return (
    <button
      type="button"
      data-mobile-chat-row="true"
      onClick={onOpen}
      className="flex h-[76px] w-full min-w-0 items-center gap-3 px-4 text-left"
    >
      {avatar}
      <span className="flex min-w-0 flex-1 items-center gap-2.5 self-stretch shadow-[inset_0_-0.5px_0_hsl(var(--foreground)/0.12)]">
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate text-base font-semibold">{title}</span>
            {badge && (
              <span
                data-mobile-chat-badge="true"
                className="bg-muted text-muted-foreground shrink-0 rounded-[5px] px-1.5 py-px text-[11px] font-semibold"
              >
                {badge}
              </span>
            )}
          </span>
          <span className="text-muted-foreground block truncate text-[15px]">{preview}</span>
        </span>
        {time && (
          <span className="text-muted-foreground shrink-0 self-start pt-[18px] text-[13px] tabular-nums">
            {time}
          </span>
        )}
      </span>
    </button>
  )
}

function TabAvatar({ tab }: { tab: ChatTab }) {
  const { t } = useTranslation()
  const isVirtual = tab.type === 'virtual'
  const displayName = getChatTabDisplayName(tab, t('chat.botNameFallback'))
  const avatarUrl = useResolvedAvatarUrl(
    isVirtual ? tab.virtualConfig?.platform : 'qq',
    isVirtual ? tab.virtualConfig?.userId : tab.sessionInfo.bot_qq
  )
  const Icon = isVirtual ? UserCircle2 : Bot
  return (
    <span className="relative shrink-0">
      <Avatar data-mobile-chat-avatar="true" className="h-[50px] w-[50px]">
        {avatarUrl && (
          <AvatarImage
            src={avatarUrl}
            alt={t('chat.sidebar.userAvatarAlt', { name: displayName })}
            className="object-cover"
          />
        )}
        <AvatarFallback
          className={isVirtual ? 'bg-muted text-foreground' : 'bg-foreground text-background'}
        >
          <Icon className="h-6 w-6" strokeWidth={1.7} />
        </AvatarFallback>
      </Avatar>
      <span
        aria-hidden="true"
        className={cn(
          'border-background absolute right-0 bottom-0 h-3.5 w-3.5 rounded-full border-2',
          tab.isConnected ? 'bg-[#34c759]' : 'bg-muted-foreground/40'
        )}
      />
    </span>
  )
}

function ObservedAvatar({ session }: { session: SessionInfo }) {
  const { t } = useTranslation()
  const targetId = session.isGroupChat ? session.groupId : session.userId
  const avatarUrl = useResolvedAvatarUrl(
    session.platform,
    targetId ?? undefined,
    session.isGroupChat ? 'group' : 'user'
  )
  const Icon = session.isGroupChat ? UsersRound : UserRound
  return (
    <Avatar data-mobile-chat-avatar="true" className="h-[50px] w-[50px] shrink-0">
      {avatarUrl && (
        <AvatarImage
          src={avatarUrl}
          alt={t('chat.sidebar.observedAvatarAlt', { name: session.sessionName })}
          className="object-cover"
        />
      )}
      <AvatarFallback className="bg-muted text-foreground">
        <Icon className="h-6 w-6" strokeWidth={1.7} />
      </AvatarFallback>
    </Avatar>
  )
}

interface MobileChatListProps {
  tabs: ChatTab[]
  observedSessions: Map<string, SessionInfo>
  observedSessionsError?: boolean
  observedStageStatuses: Map<string, StageStatusInfo>
  observedLatestMessages: Map<string, ObservedMessagePreview>
  onOpenTab: (tabId: string) => void
  onOpenObserved: (sessionId: string) => void
  onOpenObservedSettings: (sessionId: string) => void
  onClose: (tabId: string) => void
}

/** 手机端会话列表：点按进入全屏对话页，左滑露出设置/关闭 */
export function MobileChatList({
  tabs,
  observedSessions,
  observedSessionsError = false,
  observedStageStatuses,
  observedLatestMessages,
  onOpenTab,
  onOpenObserved,
  onOpenObservedSettings,
  onClose,
}: MobileChatListProps) {
  const { t, i18n } = useTranslation()
  const [query, setQuery] = useState('')
  const language = i18n.resolvedLanguage ?? i18n.language
  const normalizedQuery = query.trim().toLowerCase()

  const visibleTabs = tabs.filter(
    (tab) =>
      !normalizedQuery ||
      getChatTabDisplayName(tab, t('chat.botNameFallback')).toLowerCase().includes(normalizedQuery)
  )
  const visibleObserved = Array.from(observedSessions.values())
    .sort((a, b) => b.lastActivity - a.lastActivity)
    .filter(
      (session) => !normalizedQuery || session.sessionName.toLowerCase().includes(normalizedQuery)
    )

  const localSection = (
    // 与麦麦的会话固定在最上面
    <section data-mobile-chat-section="local" aria-label={t('chat.sidebar.myChats')}>
      {visibleTabs.map((tab) => {
        const displayName = getChatTabDisplayName(tab, t('chat.botNameFallback'))
        const lastMessage = tab.messages[tab.messages.length - 1]
        return (
          <SwipeRow
            key={tab.id}
            actions={
              tab.id === 'webui-default'
                ? []
                : [
                    {
                      label: t('chat.mobile.close'),
                      destructive: true,
                      onClick: () => onClose(tab.id),
                    },
                  ]
            }
          >
            <ChatRow
              avatar={<TabAvatar tab={tab} />}
              title={displayName}
              badge={tab.type === 'virtual' ? t('chat.sidebar.virtualBadge') : undefined}
              preview={lastMessage?.content || t('chat.sidebar.emptyPreview')}
              time={formatRowTime(lastMessage?.timestamp, language)}
              onOpen={() => onOpenTab(tab.id)}
            />
          </SwipeRow>
        )
      })}
    </section>
  )

  const observedSection = (
    // 麦麦的聊天流接在后面，按最近活跃排序，行内标「观察」区分
    <section data-mobile-chat-section="observed" aria-label={t('chat.sidebar.observedChats')}>
      {observedSessionsError ? (
        <p className="text-destructive px-5 py-4 text-sm">
          {t('chat.sidebar.loadObservedChatsFailed')}
        </p>
      ) : visibleObserved.length === 0 ? (
        <p className="text-muted-foreground px-5 py-4 text-sm">
          {t('chat.sidebar.waitingObservedChats')}
        </p>
      ) : (
        visibleObserved.map((session) => {
          const status = observedStageStatuses.get(session.sessionId)
          const latest = observedLatestMessages.get(session.sessionId)
          const content = latest?.content || latest?.mediaText || t('chat.sidebar.emptyPreview')
          const preview =
            status && status.stage && status.stage !== '等待消息' ? (
              <StageStatusLabel status={status} />
            ) : session.isGroupChat && latest?.speakerName ? (
              `${latest.speakerName}: ${content}`
            ) : (
              content
            )
          return (
            <SwipeRow
              key={session.sessionId}
              actions={[
                {
                  label: t('chat.mobile.settings'),
                  onClick: () => onOpenObservedSettings(session.sessionId),
                },
              ]}
            >
              <ChatRow
                avatar={<ObservedAvatar session={session} />}
                title={session.sessionName}
                badge={t('chat.sidebar.observedBadge')}
                preview={preview}
                time={formatRowTime(session.lastActivity, language)}
                onOpen={() => onOpenObserved(session.sessionId)}
              />
            </SwipeRow>
          )
        })
      )}
    </section>
  )

  return (
    <div
      data-mobile-chat-list="true"
      className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto pt-3 pb-4"
    >
      <div className="px-5">
        <h1 data-mobile-large-title="true" className="text-[34px] leading-tight font-bold">
          {t('chat.sidebar.title')}
        </h1>
      </div>
      <div className="px-4">
        <label
          data-mobile-chat-search="true"
          className="bg-muted flex h-10 items-center gap-2 rounded-xl px-3"
        >
          <Search className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('chat.sidebar.searchPlaceholder')}
            aria-label={t('chat.sidebar.searchPlaceholder')}
            className="min-w-0 flex-1 bg-transparent text-[15px] outline-none"
          />
        </label>
      </div>
      {/* 与麦麦的会话和麦麦的聊天流放在同一个列表里 */}
      <div data-mobile-chat-screen="true">
        {localSection}
        {observedSection}
      </div>
    </div>
  )
}

interface MobileChatRoomBarProps {
  title: string
  subtitle: ReactNode
  online: boolean
  avatar: ReactNode
  onBack: () => void
  /** 观察聊天流显示设置入口，本地会话显示「我的头像」入口 */
  onOpenSettings?: () => void
  userAvatar?: {
    userId: string
    userName: string
    userAvatarVersion?: number
    isUploading: boolean
    onUpdate: (file: File) => Promise<void>
  }
}

/** 手机端全屏对话页的顶部：返回、会话名片、右侧单个操作，都是悬浮的玻璃按钮 */
export function MobileChatRoomBar({
  title,
  subtitle,
  online,
  avatar,
  onBack,
  onOpenSettings,
  userAvatar,
}: MobileChatRoomBarProps) {
  const { t } = useTranslation()
  const avatarInputRef = useRef<HTMLInputElement>(null)
  const userAvatarUrl = useResolvedAvatarUrl(
    userAvatar?.userAvatarVersion ? 'webui' : undefined,
    userAvatar?.userId,
    'user',
    userAvatar?.userAvatarVersion
  )

  return (
    <div
      data-mobile-chat-room-bar="true"
      className="flex shrink-0 items-center gap-2.5 px-4 pt-[calc(env(safe-area-inset-top)+10px)] pb-2"
    >
      <button
        type="button"
        onClick={onBack}
        aria-label={t('chat.mobile.back')}
        data-mobile-chat-room-button="true"
        className={cn(
          'flex h-11 w-11 shrink-0 items-center justify-center rounded-full',
          MOBILE_GLASS_CLASS
        )}
      >
        <ChevronLeft className="h-[22px] w-[22px]" strokeWidth={1.7} aria-hidden="true" />
      </button>
      <div
        data-mobile-chat-room-title="true"
        className={cn(
          'flex h-11 min-w-0 items-center gap-2 rounded-full pr-4 pl-[5px]',
          MOBILE_GLASS_CLASS
        )}
      >
        {avatar}
        <span className="flex min-w-0 flex-col leading-tight">
          <b className="truncate text-[15px] font-semibold">{title}</b>
          <span className="text-muted-foreground flex items-center gap-1 truncate text-[11px]">
            <span
              data-mobile-chat-room-led="true"
              aria-hidden="true"
              className={cn(
                'h-1.5 w-1.5 shrink-0 rounded-full',
                online ? 'bg-[#34c759]' : 'bg-muted-foreground/40'
              )}
            />
            {subtitle}
          </span>
        </span>
      </div>
      <span className="flex-1" />
      {onOpenSettings && (
        <button
          type="button"
          onClick={onOpenSettings}
          aria-label={t('chat.sidebar.openSettings', { name: title })}
          data-mobile-chat-room-button="true"
          className={cn(
            'flex h-11 w-11 shrink-0 items-center justify-center rounded-full',
            MOBILE_GLASS_CLASS
          )}
        >
          <Settings className="h-5 w-5" strokeWidth={1.7} aria-hidden="true" />
        </button>
      )}
      {userAvatar && (
        <>
          <button
            type="button"
            aria-label={t('chat.sidebar.editAvatar')}
            disabled={userAvatar.isUploading}
            onClick={() => avatarInputRef.current?.click()}
            data-mobile-chat-room-button="true"
            className={cn(
              'relative flex h-11 w-11 shrink-0 items-center justify-center rounded-full disabled:cursor-wait',
              MOBILE_GLASS_CLASS
            )}
          >
            <Avatar className="h-[34px] w-[34px]">
              {userAvatarUrl && (
                <AvatarImage
                  src={userAvatarUrl}
                  alt={t('chat.sidebar.userAvatarAlt', { name: userAvatar.userName })}
                  className="object-cover"
                />
              )}
              <AvatarFallback className="bg-muted text-foreground">
                <UserCircle2 className="h-4 w-4" />
              </AvatarFallback>
            </Avatar>
            <span className="bg-foreground text-background absolute right-0 bottom-0 flex h-4 w-4 items-center justify-center rounded-full">
              {userAvatar.isUploading ? (
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
              ) : (
                <Camera className="h-2.5 w-2.5" />
              )}
            </span>
          </button>
          <input
            ref={avatarInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif,image/bmp"
            className="hidden"
            onChange={(event) => {
              const file = event.currentTarget.files?.[0]
              event.currentTarget.value = ''
              if (file) {
                void userAvatar.onUpdate(file)
              }
            }}
          />
        </>
      )}
    </div>
  )
}

/** 对话页顶栏里的小头像：本地会话 */
export function MobileChatRoomTabAvatar({ tab }: { tab: ChatTab }) {
  const { t } = useTranslation()
  const isVirtual = tab.type === 'virtual'
  const avatarUrl = useResolvedAvatarUrl(
    isVirtual ? tab.virtualConfig?.platform : 'qq',
    isVirtual ? tab.virtualConfig?.userId : tab.sessionInfo.bot_qq
  )
  const Icon = isVirtual ? UserCircle2 : Bot
  return (
    <Avatar className="h-[34px] w-[34px] shrink-0">
      {avatarUrl && (
        <AvatarImage
          src={avatarUrl}
          alt={t('chat.sidebar.userAvatarAlt', {
            name: getChatTabDisplayName(tab, t('chat.botNameFallback')),
          })}
          className="object-cover"
        />
      )}
      <AvatarFallback
        className={isVirtual ? 'bg-muted text-foreground' : 'bg-foreground text-background'}
      >
        <Icon className="h-4 w-4" />
      </AvatarFallback>
    </Avatar>
  )
}

/** 对话页顶栏里的小头像：观察聊天流 */
export function MobileChatRoomObservedAvatar({ session }: { session: SessionInfo }) {
  const { t } = useTranslation()
  const targetId = session.isGroupChat ? session.groupId : session.userId
  const avatarUrl = useResolvedAvatarUrl(
    session.platform,
    targetId ?? undefined,
    session.isGroupChat ? 'group' : 'user'
  )
  const Icon = session.isGroupChat ? UsersRound : UserRound
  return (
    <Avatar className="h-[34px] w-[34px] shrink-0">
      {avatarUrl && (
        <AvatarImage
          src={avatarUrl}
          alt={t('chat.sidebar.observedAvatarAlt', { name: session.sessionName })}
          className="object-cover"
        />
      )}
      <AvatarFallback className="bg-muted text-foreground">
        <Icon className="h-4 w-4" />
      </AvatarFallback>
    </Avatar>
  )
}
