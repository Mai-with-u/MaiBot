import { AgentPage } from '@/routes/agent'
import { ChatConversationHeader } from './ChatConversationHeader'
import { PageFrame } from '@/components/ui/surface'
import { ConversationTransition } from '@/components/ui/conversation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { motion } from 'motion/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  ChatStreamSettingsDialog,
  type ChatStreamSettingsTarget,
} from '@/components/chat-stream-settings-dialog'
import { MOBILE_GLASS_CLASS } from '@/components/layout/mobile-glass'
import { useHideMobileTabBar } from '@/components/layout/mobile-nav-context'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import { uploadWebuiUserAvatar } from '@/lib/avatar-url'
import { chatWsClient } from '@/lib/chat-ws-client'
import { uploadFile, type StoredFile } from '@/lib/files-api'
import { getAllChatStreams } from '@/lib/chat-management-api'
import {
  maisakaMonitorClient,
  type LlmErrorEvent,
  type LlmRetryEvent,
  type MessageIngestedEvent,
  type MessageSentEvent,
  type StageRemovedEvent,
  type StageStatusEvent,
} from '@/lib/maisaka-monitor-client'
import { loadUserEmojiPayload, type UserEmojiItem } from '@/lib/user-emoji-api'
import { cn } from '@/lib/utils'
import { MaisakaMonitor } from '@/routes/monitor/maisaka-monitor'
import { StageStatusLabel } from '@/routes/monitor/StageStatusLabel'
import { useMaisakaMonitorOverview, type SessionInfo } from '@/routes/monitor/use-maisaka-monitor'

import { ChatComposer } from './ChatComposer'
import { ChatWorkspaceSidebar } from './ChatWorkspaceSidebar'
import { MessageList } from './MessageList'
import {
  MobileChatList,
  MobileChatRoomBar,
  MobileChatRoomObservedAvatar,
  MobileChatRoomTabAvatar,
  MobileFloatingHeader,
} from './MobileChat'
import type {
  ChatImageAttachment,
  ChatIncomingImage,
  ChatTab,
  ChatMessage,
  ChatRuntimeStatus,
  MessageSegment,
  ObservedMessagePreview,
  SavedVirtualTab,
  VirtualIdentityConfig,
  WsMessage,
} from './types'
import {
  INTERNAL_CHAT_USER_ID,
  getChatTabDisplayName,
  getStoredUserAvatarVersion,
  getStoredUserName,
  getSavedVirtualTabs,
  saveUserAvatarVersion,
  saveUserName,
  saveVirtualTabs,
} from './utils'

const MAX_CHAT_IMAGES = 8
const MAX_MESSAGES_PER_CHAT_TAB = 1000
const MAX_PROCESSED_MESSAGE_KEYS = 100
const MAX_USER_AVATAR_BYTES = 5 * 1024 * 1024

function getRequestedObservedSessionId(): string | null {
  if (typeof window === 'undefined') {
    return null
  }
  return new URLSearchParams(window.location.search).get('observe')
}

function appendRecentMessage(messages: ChatMessage[], message: ChatMessage): ChatMessage[] {
  if (messages.length < MAX_MESSAGES_PER_CHAT_TAB) {
    return [...messages, message]
  }
  return [...messages.slice(-(MAX_MESSAGES_PER_CHAT_TAB - 1)), message]
}

function buildImageDataUrl(image: ChatImageAttachment | ChatIncomingImage): string {
  const dataUrl = image.data_url || ('dataUrl' in image ? image.dataUrl : undefined)
  if (dataUrl) {
    return dataUrl
  }

  const mimeType =
    image.mime_type || ('mimeType' in image ? image.mimeType : undefined) || 'image/png'
  return image.base64 ? `data:${mimeType};base64,${image.base64}` : ''
}

function buildMessageSegments(
  content: string,
  images: Array<ChatImageAttachment | ChatIncomingImage>,
  emojis: Array<ChatImageAttachment | ChatIncomingImage> = []
): MessageSegment[] {
  const segments: MessageSegment[] = []
  if (content) {
    segments.push({ type: 'text', data: content })
  }

  for (const image of images) {
    const dataUrl = buildImageDataUrl(image)
    if (dataUrl) {
      segments.push({ type: 'image', data: dataUrl })
    }
  }
  for (const emoji of emojis) {
    const dataUrl = buildImageDataUrl(emoji)
    if (dataUrl) {
      segments.push({ type: 'emoji', data: dataUrl })
    }
  }

  return segments
}

function readImageFile(file: File, id: string): Promise<ChatImageAttachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error(`Failed to read image: ${file.name}`))
    reader.onload = () => {
      const dataUrl = typeof reader.result === 'string' ? reader.result : ''
      const base64 = dataUrl.includes(',') ? dataUrl.split(',', 2)[1] : ''
      if (!base64 || !dataUrl.startsWith('data:image/')) {
        reject(new Error(`Invalid image data: ${file.name}`))
        return
      }

      resolve({
        id,
        name: file.name,
        mime_type: file.type || 'image/png',
        base64,
        data_url: dataUrl,
      })
    }
    reader.readAsDataURL(file)
  })
}

function normalizeStatusText(value: string | null | undefined): string {
  return (value || '').trim().toLowerCase()
}

function resolveStatusKind(stage: string, agentState: string): ChatRuntimeStatus['kind'] | null {
  const normalizedStage = normalizeStatusText(stage)
  const normalizedAgentState = normalizeStatusText(agentState)
  if (
    normalizedAgentState === 'wait' ||
    normalizedStage === '空闲' ||
    normalizedStage === '等待消息'
  ) {
    return null
  }

  if (
    normalizedStage.includes('错误') ||
    normalizedStage.includes('异常') ||
    normalizedStage.includes('失败') ||
    normalizedStage.includes('error')
  ) {
    return 'error'
  }

  if (
    normalizedStage.includes('replyer') ||
    normalizedStage.includes('replier') ||
    normalizedStage.includes('回复生成') ||
    (normalizedStage.includes('工具执行') && normalizedStage.includes('reply'))
  ) {
    return 'typing'
  }

  if (
    normalizedStage.includes('planner') ||
    normalizedStage.includes('思考') ||
    normalizedStage === '消息整理' ||
    normalizedStage === '启动循环'
  ) {
    return 'thinking'
  }

  return 'acting'
}

function resolveRetryStatusKind(data: LlmRetryEvent): ChatRuntimeStatus['kind'] {
  const taskText = normalizeStatusText(`${data.task_name} ${data.request_type}`)
  if (taskText.includes('replyer') || taskText.includes('replier')) {
    return 'typing'
  }
  if (taskText.includes('planner')) {
    return 'thinking'
  }
  return 'acting'
}

// 侧边栏观察聊天流的最新消息预览：优先正文，纯媒体消息退回媒体占位文案
function buildObservedMessagePreview(
  data: MessageIngestedEvent | MessageSentEvent
): ObservedMessagePreview {
  const content = data.content.trim()
  const mediaText = (data.media ?? []).find((media) => media.text.trim())?.text.trim() ?? ''
  return {
    speakerName: data.speaker_name,
    content,
    mediaText,
  }
}

function matchesMonitorTarget(
  tab: ChatTab,
  data: StageStatusEvent | StageRemovedEvent | LlmRetryEvent | LlmErrorEvent
): boolean {
  if (data.session_id && data.session_id === tab.sessionInfo.session_id) {
    return true
  }

  const eventGroupId = typeof data.group_id === 'string' ? data.group_id : ''
  const tabGroupId = tab.sessionInfo.group_id || tab.virtualConfig?.groupId || ''
  if (eventGroupId && tabGroupId) {
    return eventGroupId === tabGroupId
  }

  const eventUserId = typeof data.user_id === 'string' ? data.user_id : ''
  const tabUserId =
    tab.type === 'virtual' ? tab.virtualConfig?.userId || '' : tab.sessionInfo.user_id || ''
  if (!eventUserId || !tabUserId || eventUserId !== tabUserId) {
    return false
  }

  const eventPlatform = typeof data.platform === 'string' ? data.platform : ''
  const tabPlatform =
    tab.type === 'virtual' ? tab.virtualConfig?.platform || '' : tab.sessionInfo.platform || 'internal'
  return !eventPlatform || !tabPlatform || eventPlatform === tabPlatform
}

function buildRuntimeStatusFromStage(data: StageStatusEvent): ChatRuntimeStatus | null {
  const kind = resolveStatusKind(data.stage, data.agent_state)
  if (!kind) {
    return null
  }

  return {
    kind,
    stage: data.stage,
    detail: data.detail,
    updatedAt: data.updated_at || data.timestamp || Date.now() / 1000,
  }
}

export function ChatPage() {
  const navigate = useNavigate()
  const setupTrial = useRouterState({
    select: (state) => new URLSearchParams(state.location.searchStr).get('setupTrial') === 'true',
  })
  const agentParam = useRouterState({
    select: (state) => new URLSearchParams(state.location.searchStr).get('agent') === 'true',
  })
  const [agentSelected, setAgentSelected] = useState(agentParam)
  const [handledAgentParam, setHandledAgentParam] = useState(agentParam)
  const [trialReplied, setTrialReplied] = useState(false)
  const trialMessagePending = useRef(false)
  const canOpenMessagePlatform = typeof window.maibotDesktopHost?.openMessagePlatform === 'function'
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const {
    sessions: observedSessions,
    stageStatuses: observedStageStatuses,
    latestMessages,
    setSelectedSession: setSelectedObservedSession,
  } = useMaisakaMonitorOverview()

  const { data: knownChatStreams = [], isError: knownChatStreamsError } = useQuery({
    queryKey: ['chat-streams', 'all'],
    queryFn: getAllChatStreams,
    staleTime: 30_000,
  })
  // 监控中新出现的会话可能刚写入数据库，及时刷新已知聊天流列表。
  const previousObservedSessionIds = useRef(new Set(observedSessions.keys()))
  useEffect(() => {
    const currentIds = new Set(observedSessions.keys())
    const hasNewSession = Array.from(currentIds).some(
      (sessionId) => !previousObservedSessionIds.current.has(sessionId)
    )
    previousObservedSessionIds.current = currentIds
    if (hasNewSession) {
      void queryClient.invalidateQueries({ queryKey: ['chat-streams', 'all'] })
    }
  }, [observedSessions, queryClient])

  // 数据库中的真实聊天流为列表来源，实时监控事件只更新对应项的活动时间。
  const knownSessions = useMemo(() => {
    const sessions = new Map<string, SessionInfo>()
    for (const chat of knownChatStreams) {
      const live = observedSessions.get(chat.session_id)
      sessions.set(chat.session_id, {
        sessionId: chat.session_id,
        sessionName: chat.display_name,
        isGroupChat: chat.chat_type === 'group',
        groupId: chat.group_id,
        userId: chat.user_id,
        platform: chat.platform,
        lastActivity: Math.max(
          chat.last_active_at ?? chat.created_at ?? 0,
          live?.lastActivity ?? 0
        ),
        eventCount: live?.eventCount ?? 0,
      })
    }
    return sessions
  }, [knownChatStreams, observedSessions])

  // 最新消息由监控仓库增量维护，状态/Planner 更新不再触发扫描全部历史。
  const observedLatestMessages = useMemo(() => {
    const previews = new Map<string, ObservedMessagePreview>()
    for (const [sessionId, entry] of latestMessages) {
      previews.set(
        sessionId,
        buildObservedMessagePreview(entry.data as MessageIngestedEvent | MessageSentEvent)
      )
    }
    return previews
  }, [latestMessages])

  // 默认本地聊天标签页
  const defaultTab: ChatTab = {
    id: 'webui-default',
    type: 'webui',
    label: t('chat.botNameFallback'),
    messages: [],
    isConnected: false,
    isTyping: false,
    runtimeStatus: null,
    sessionInfo: {},
  }

  // 从存储中恢复虚拟标签页
  const initializeTabs = (): ChatTab[] => {
    const savedVirtualTabs = getSavedVirtualTabs()
    const restoredTabs: ChatTab[] = savedVirtualTabs.map((saved) => {
      // 确保 virtualConfig 有 groupId（兼容旧数据）
      const config = saved.virtualConfig
      if (!config.groupId && config.platform && config.userId) {
        config.groupId = `webui_virtual_group_${config.platform}_${config.userId}`
      }
      return {
        id: saved.id,
        type: 'virtual' as const,
        label: saved.label,
        virtualConfig: config,
        messages: [],
        isConnected: false,
        isTyping: false,
        runtimeStatus: null,
        sessionInfo: {},
      }
    })
    return [defaultTab, ...restoredTabs]
  }

  // 多标签页状态
  const [tabs, setTabs] = useState<ChatTab[]>(initializeTabs)
  const [activeTabId, setActiveTabId] = useState('webui-default')
  const [activeObservedSessionId, setActiveObservedSessionId] = useState(
    getRequestedObservedSessionId
  )

  const activeObservedSession = activeObservedSessionId
    ? knownSessions.get(activeObservedSessionId)
    : undefined
  const activeObservedStatus = activeObservedSessionId
    ? observedStageStatuses.get(activeObservedSessionId)
    : undefined

  // 手机端（md 以下）先显示会话列表，点进会话后进入全屏对话页并隐藏底部标签栏
  const isMobileChat = useMediaQuery('(max-width: 767.98px)')
  // 手机端观察页的工具栏挂到悬浮顶栏里
  const [observedToolbarSlot, setObservedToolbarSlot] = useState<HTMLDivElement | null>(null)
  const [mobileRoomOpen, setMobileRoomOpen] = useState(
    () => setupTrial || agentParam || getRequestedObservedSessionId() !== null
  )
  if (agentParam !== handledAgentParam) {
    setHandledAgentParam(agentParam)
    setAgentSelected(agentParam)
    if (agentParam) setMobileRoomOpen(true)
  }
  useHideMobileTabBar(isMobileChat && mobileRoomOpen)

  // 「麦麦实时」条等入口通过 ?observe= 直达某个聊天流；已在聊天页时也要跟着切换
  const observeParam = useRouterState({
    select: (state) => new URLSearchParams(state.location.searchStr).get('observe'),
  })
  const [handledObserveParam, setHandledObserveParam] = useState(observeParam)
  if (observeParam !== handledObserveParam) {
    setHandledObserveParam(observeParam)
    if (observeParam) {
      setAgentSelected(false)
      setActiveObservedSessionId(observeParam)
      setMobileRoomOpen(true)
    }
  }

  // 当前活动标签页
  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0]

  // 通用状态
  const [inputValue, setInputValue] = useState('')
  const [selectedImages, setSelectedImages] = useState<ChatImageAttachment[]>([])
  const { flags: featureFlags } = useFeatureFlags()
  const agentEnabled = featureFlags?.agent === true
  const agentActive = agentEnabled && agentSelected
  const filesEnabled = featureFlags?.files === true
  const [filesByTab, setFilesByTab] = useState<Record<string, StoredFile[]>>({})
  const [uploadingFiles, setUploadingFiles] = useState(false)
  const [isLoadingHistory, setIsLoadingHistory] = useState(true)
  const [userName, setUserName] = useState(getStoredUserName())
  const [userAvatarVersion, setUserAvatarVersion] = useState(getStoredUserAvatarVersion)
  const [isUploadingUserAvatar, setIsUploadingUserAvatar] = useState(false)
  // 页内聊天流设置弹窗：非空时打开
  const [settingsChat, setSettingsChat] = useState<ChatStreamSettingsTarget | null>(null)

  // 网页聊天室统一以主人身份进入 Bot 内部统一聊天
  const userId = INTERNAL_CHAT_USER_ID

  const messageIdCounterRef = useRef(0)
  const monitorStatusesRef = useRef<Map<string, StageStatusEvent>>(new Map())
  const processedMessagesMapRef = useRef<Map<string, Set<string>>>(new Map())
  const sessionUnsubscribeMapRef = useRef<Map<string, () => void>>(new Map())
  const tabsRef = useRef<ChatTab[]>([])
  const { toast } = useToast()

  useEffect(() => {
    tabsRef.current = tabs
  }, [tabs])

  useEffect(() => {
    if (activeObservedSessionId) {
      setSelectedObservedSession(activeObservedSessionId)
    }
  }, [activeObservedSessionId, setSelectedObservedSession])

  // 生成唯一消息 ID
  const generateMessageId = (prefix: string) => {
    messageIdCounterRef.current += 1
    return `${prefix}-${Date.now()}-${messageIdCounterRef.current}-${Math.random().toString(36).substr(2, 9)}`
  }

  // 更新指定标签页
  const updateTab = useCallback((tabId: string, updates: Partial<ChatTab>) => {
    setTabs((prev) => prev.map((tab) => (tab.id === tabId ? { ...tab, ...updates } : tab)))
  }, [])

  // 向指定标签页添加消息
  const addMessageToTab = useCallback((tabId: string, message: ChatMessage) => {
    setTabs((prev) =>
      prev.map((tab) =>
        tab.id === tabId ? { ...tab, messages: appendRecentMessage(tab.messages, message) } : tab
      )
    )
  }, [])

  const handleSessionMessage = useCallback(
    (tabId: string, tabType: 'webui' | 'virtual', data: WsMessage) => {
      switch (data.type) {
        case 'session_info':
          if (tabType === 'webui' && data.user_name) {
            setUserName(data.user_name)
            saveUserName(data.user_name)
          }
          setTabs((prev) =>
            prev.map((tab) => {
              if (tab.id !== tabId) {
                return tab
              }
              const nextTab: ChatTab = {
                ...tab,
                sessionInfo: {
                  session_id: data.session_id,
                  user_id: data.user_id,
                  user_name: data.user_name,
                  bot_name: data.bot_name,
                  bot_qq: data.bot_qq,
                  group_id: data.group_id,
                  platform: data.platform,
                  virtual_mode: data.virtual_mode,
                },
              }
              const currentStatus = Array.from(monitorStatusesRef.current.values()).find((status) =>
                matchesMonitorTarget(nextTab, status)
              )
              if (!currentStatus) {
                return {
                  ...nextTab,
                  isTyping: false,
                  runtimeStatus: null,
                }
              }
              const runtimeStatus = buildRuntimeStatusFromStage(currentStatus)
              return {
                ...nextTab,
                isTyping: runtimeStatus?.kind === 'typing',
                runtimeStatus,
              }
            })
          )
          break

        case 'nickname_updated':
          if (tabType === 'webui' && data.user_name) {
            setUserName(data.user_name)
            saveUserName(data.user_name)
            setTabs((prev) =>
              prev.map((tab) =>
                tab.id === tabId
                  ? { ...tab, sessionInfo: { ...tab.sessionInfo, user_name: data.user_name } }
                  : tab
              )
            )
          }
          break

        case 'system':
          addMessageToTab(tabId, {
            id: generateMessageId('sys'),
            type: 'system',
            content: data.content || '',
            timestamp: data.timestamp || Date.now() / 1000,
          })
          break

        case 'user_message': {
          updateTab(tabId, { runtimeStatus: null })
          // 同一身份可能有多个网页和桌宠等插件界面同时在线，只跳过本标签页自己发出的回显
          const ownSessionId = tabsRef.current.find((tab) => tab.id === tabId)?.sessionInfo.session_id
          if (data.origin && data.origin === ownSessionId) {
            break
          }

          const processedSet = processedMessagesMapRef.current.get(tabId) || new Set()
          const contentHash = `user-${data.content}-${Math.floor((data.timestamp || 0) * 1000)}`
          if (processedSet.has(contentHash)) {
            break
          }

          processedSet.add(contentHash)
          processedMessagesMapRef.current.set(tabId, processedSet)
          while (processedSet.size > MAX_PROCESSED_MESSAGE_KEYS) {
            const firstKey = processedSet.values().next().value
            if (!firstKey) break
            processedSet.delete(firstKey)
          }

          addMessageToTab(tabId, {
            id: data.message_id || generateMessageId('user'),
            type: 'user',
            content: data.content || '',
            message_type:
              (data.images && data.images.length > 0) || (data.emojis && data.emojis.length > 0) || (data.files && data.files.length > 0)
                ? 'rich'
                : 'text',
            segments:
              (data.images && data.images.length > 0) || (data.emojis && data.emojis.length > 0) || (data.files && data.files.length > 0)
                ? [...buildMessageSegments(data.raw_content || '', data.images || [], data.emojis || []), ...(data.files || []).map(file => ({ type: 'file' as const, data: file }))]
                : undefined,
            timestamp: data.timestamp || Date.now() / 1000,
            sender: data.sender,
          })
          break
        }

        case 'bot_message': {
          // Only a live reply after this trial's own message completes the trial, not loaded history.
          if (tabId === 'webui-default' && trialMessagePending.current && (data.content?.trim() || data.segments?.length)) {
            trialMessagePending.current = false
            setTrialReplied(true)
          }
          updateTab(tabId, { isTyping: false, runtimeStatus: null })
          const processedSet = processedMessagesMapRef.current.get(tabId) || new Set()
          const contentHash = `bot-${data.content}-${Math.floor((data.timestamp || 0) * 1000)}`
          if (processedSet.has(contentHash)) {
            break
          }

          processedSet.add(contentHash)
          processedMessagesMapRef.current.set(tabId, processedSet)
          if (processedSet.size > 100) {
            const firstKey = processedSet.values().next().value
            if (firstKey) processedSet.delete(firstKey)
          }

          setTabs((prev) =>
            prev.map((tab) => {
              if (tab.id !== tabId) return tab
              const newMessage: ChatMessage = {
                id: generateMessageId('bot'),
                type: 'bot',
                content: data.content || '',
                message_type: (data.message_type === 'rich' ? 'rich' : 'text') as 'text' | 'rich',
                segments: data.segments,
                timestamp: data.timestamp || Date.now() / 1000,
                sender: data.sender,
              }
              return {
                ...tab,
                messages: appendRecentMessage(tab.messages, newMessage),
              }
            })
          )
          break
        }

        case 'typing':
          updateTab(tabId, {
            isTyping: data.is_typing || false,
            runtimeStatus: data.is_typing
              ? {
                  kind: 'typing',
                  stage: 'typing',
                  updatedAt: data.timestamp || Date.now() / 1000,
                }
              : null,
          })
          break

        case 'error':
          setTabs((prev) =>
            prev.map((tab) => {
              if (tab.id !== tabId) return tab
              return {
                ...tab,
                runtimeStatus: {
                  kind: 'error',
                  detail: data.content,
                  updatedAt: data.timestamp || Date.now() / 1000,
                },
                messages: appendRecentMessage(tab.messages, {
                  id: generateMessageId('error'),
                  type: 'error' as const,
                  content: data.content || t('chat.message.errorFallback'),
                  timestamp: data.timestamp || Date.now() / 1000,
                }),
              }
            })
          )
          toast({
            title: t('chat.toast.error'),
            description: data.content,
            variant: 'destructive',
          })
          break

        case 'history': {
          const historyMessages = data.messages || []
          const processedSet = new Set<string>()
          // 聊天页没有向前翻页能力，只保留最近一段历史，避免多标签长期占用无限内存。
          const recentHistoryMessages = historyMessages.slice(-MAX_MESSAGES_PER_CHAT_TAB)
          const formattedMessages: ChatMessage[] = recentHistoryMessages.map((msg) => {
            const isBot = msg.is_bot || false
            const msgId = msg.id || generateMessageId(isBot ? 'bot' : 'user')
            const contentHash = `${isBot ? 'bot' : 'user'}-${msg.content}-${Math.floor(msg.timestamp * 1000)}`
            processedSet.add(contentHash)
            const isRich =
              msg.message_type === 'rich' && Array.isArray(msg.segments) && msg.segments.length > 0
            return {
              id: msgId,
              type: isBot ? 'bot' : ('user' as const),
              content: msg.content,
              timestamp: msg.timestamp,
              message_type: isRich ? 'rich' : 'text',
              segments: isRich ? (msg.segments ?? undefined) : undefined,
              sender: {
                name:
                  msg.sender_name || (isBot ? t('chat.botNameFallback') : t('chat.userFallback')),
                user_id: msg.sender_id,
                is_bot: isBot,
              },
            }
          })

          while (processedSet.size > MAX_PROCESSED_MESSAGE_KEYS) {
            const firstKey = processedSet.values().next().value
            if (!firstKey) break
            processedSet.delete(firstKey)
          }
          processedMessagesMapRef.current.set(tabId, processedSet)
          updateTab(tabId, { messages: formattedMessages })
          setIsLoadingHistory(false)
          break
        }

        default:
          break
      }
    },
    [addMessageToTab, t, toast, updateTab]
  )

  const ensureSessionListener = useCallback(
    (tabId: string, tabType: 'webui' | 'virtual') => {
      if (sessionUnsubscribeMapRef.current.has(tabId)) {
        return
      }

      const unsubscribe = chatWsClient.onSessionMessage(tabId, (message) => {
        handleSessionMessage(tabId, tabType, message as unknown as WsMessage)
      })
      sessionUnsubscribeMapRef.current.set(tabId, unsubscribe)
    },
    [handleSessionMessage]
  )

  const openSessionForTab = useCallback(
    async (tabId: string, tabType: 'webui' | 'virtual', config?: VirtualIdentityConfig) => {
      ensureSessionListener(tabId, tabType)
      setIsLoadingHistory(true)

      try {
        if (tabType === 'virtual' && config) {
          await chatWsClient.openSession(tabId, {
            client: { type: 'webui', name: 'MaiBot WebUI' },
            user_id: config.userId,
            user_name: config.userName,
            platform: config.platform,
            person_id: config.personId,
            group_name: config.groupName || t('chat.virtualGroupFallback'),
            group_id: config.groupId,
          })
        } else {
          await chatWsClient.openSession(tabId, {
            client: { type: 'webui', name: 'MaiBot WebUI' },
            user_id: userId,
            user_name: userName,
          })
        }

        updateTab(tabId, { isConnected: true })
      } catch (error) {
        console.error(`[Tab ${tabId}] 打开聊天会话失败:`, error)
        setIsLoadingHistory(false)
        toast({
          title: t('chat.toast.connectionFailed'),
          description: t('chat.toast.sessionUnavailable'),
          variant: 'destructive',
        })
      }
    },
    [ensureSessionListener, t, toast, updateTab, userId, userName]
  )

  // 用于追踪组件是否已卸载
  const isUnmountedRef = useRef(false)

  // 初始化连接（默认本地聊天标签页）
  useEffect(() => {
    isUnmountedRef.current = false

    // 在 effect 内部保存 ref 当前值，以供 cleanup 安全使用
    const sessionUnsubscribeMap = sessionUnsubscribeMapRef.current
    const tabsRefSnapshot = tabsRef

    const unsubscribeConnection = chatWsClient.onConnectionChange((connected) => {
      if (isUnmountedRef.current) {
        return
      }

      setTabs((prev) =>
        prev.map((tab) => ({
          ...tab,
          isConnected: connected,
        }))
      )
    })

    tabs.forEach((tab) => {
      processedMessagesMapRef.current.set(tab.id, new Set())
      void openSessionForTab(tab.id, tab.type, tab.virtualConfig)
    })

    return () => {
      isUnmountedRef.current = true
      unsubscribeConnection()

      sessionUnsubscribeMap.forEach((unsubscribe) => {
        unsubscribe()
      })
      sessionUnsubscribeMap.clear()

      tabsRefSnapshot.current.forEach((tab) => {
        chatWsClient.releaseSession(tab.id)
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    let active = true
    let unsubscribe: (() => Promise<void>) | undefined

    void maisakaMonitorClient
      .subscribe((event) => {
        if (!active) {
          return
        }

        if (event.type === 'stage.snapshot') {
          monitorStatusesRef.current = new Map(
            event.data.entries.map((entry) => [entry.session_id, entry])
          )
          setTabs((prev) =>
            prev.map((tab) => {
              // 阶段快照描述的是服务端“此刻”的完整状态。刷新后此前回放的
              // llm.error 可能已过期，未命中快照时必须清空，不能保留旧状态。
              const status = event.data.entries.find((entry) => matchesMonitorTarget(tab, entry))
              const runtimeStatus = status ? buildRuntimeStatusFromStage(status) : null
              return {
                ...tab,
                isTyping: runtimeStatus?.kind === 'typing',
                runtimeStatus,
              }
            })
          )
          return
        }

        if (event.type === 'stage.status') {
          monitorStatusesRef.current.set(event.data.session_id, event.data)
          setTabs((prev) =>
            prev.map((tab) => {
              if (!matchesMonitorTarget(tab, event.data)) {
                return tab
              }
              const nextStatus = buildRuntimeStatusFromStage(event.data)
              return {
                ...tab,
                runtimeStatus: nextStatus,
                isTyping: nextStatus?.kind === 'typing',
              }
            })
          )
          return
        }

        if (event.type === 'stage.removed') {
          monitorStatusesRef.current.delete(event.data.session_id)
          setTabs((prev) =>
            prev.map((tab) =>
              matchesMonitorTarget(tab, event.data)
                ? { ...tab, isTyping: false, runtimeStatus: null }
                : tab
            )
          )
          return
        }

        if (event.type === 'llm.retry') {
          setTabs((prev) =>
            prev.map((tab) => {
              if (!matchesMonitorTarget(tab, event.data)) {
                return tab
              }
              const currentStatus = tab.runtimeStatus
              const nextStatus: ChatRuntimeStatus = {
                kind: currentStatus?.kind ?? resolveRetryStatusKind(event.data),
                stage: currentStatus?.stage,
                detail: event.data.reason,
                retry: {
                  attempt: event.data.attempt,
                  maxAttempts: event.data.max_attempts,
                },
                updatedAt: event.data.timestamp || Date.now() / 1000,
              }
              return {
                ...tab,
                runtimeStatus: nextStatus,
                isTyping: nextStatus.kind === 'typing',
              }
            })
          )
          return
        }

        if (event.type === 'llm.error') {
          setTabs((prev) =>
            prev.map((tab) => {
              if (!matchesMonitorTarget(tab, event.data)) {
                return tab
              }
              const currentStatus = tab.runtimeStatus
              return {
                ...tab,
                isTyping: false,
                runtimeStatus: {
                  kind: 'error',
                  stage: currentStatus?.stage,
                  detail: event.data.message,
                  retry: currentStatus?.retry,
                  updatedAt: event.data.timestamp || Date.now() / 1000,
                },
              }
            })
          )
        }
      })
      .then((cleanup) => {
        if (active) {
          unsubscribe = cleanup
          return
        }
        void cleanup()
      })
      .catch((error) => {
        console.error('[Chat] 订阅 MaiSaka 状态失败:', error)
      })

    return () => {
      active = false
      if (unsubscribe) {
        void unsubscribe()
      }
    }
  }, [])

  // 发送消息到当前活动标签页
  const sendMessage = useCallback(async () => {
    const filesToSend = filesEnabled ? filesByTab[activeTabId] || [] : []
    if (uploadingFiles || (!inputValue.trim() && selectedImages.length === 0 && filesToSend.length === 0) || !activeTab?.isConnected) {
      return
    }

    const displayName =
      activeTab?.type === 'virtual' ? activeTab.virtualConfig?.userName || userName : userName

    const messageContent = inputValue.trim()
    const imagesToSend = selectedImages
    const currentTimestamp = Date.now() / 1000

    // 添加到去重缓存，防止服务器广播回来的消息重复显示
    const processedSet = processedMessagesMapRef.current.get(activeTabId) || new Set()
    const contentHash = `user-${messageContent}-${imagesToSend.length}-${Math.floor(currentTimestamp * 1000)}`
    processedSet.add(contentHash)
    processedMessagesMapRef.current.set(activeTabId, processedSet)

    if (processedSet.size > 100) {
      const firstKey = processedSet.values().next().value
      if (firstKey) processedSet.delete(firstKey)
    }

    // 先添加用户消息（立即显示）
    const userMessage: ChatMessage = {
      id: generateMessageId('user'),
      type: 'user',
      content: messageContent,
      message_type: imagesToSend.length > 0 || filesToSend.length > 0 ? 'rich' : 'text',
      segments:
        [...buildMessageSegments(messageContent, imagesToSend), ...filesToSend.map(file => ({ type: 'file' as const, data: file }))],
      timestamp: currentTimestamp,
      sender: {
        name: displayName,
        is_bot: false,
      },
    }
    addMessageToTab(activeTabId, userMessage)

    setInputValue('')
    setSelectedImages([])
    setFilesByTab(previous => ({ ...previous, [activeTabId]: [] }))

    try {
      if (setupTrial && activeTabId === 'webui-default') trialMessagePending.current = true
      await chatWsClient.sendMessage(activeTabId, messageContent, displayName, {
        files: filesToSend.map(file => ({ file_ref: file.file_ref, name: file.name })),
        images: imagesToSend.map((image) => ({
          name: image.name,
          mime_type: image.mime_type,
          base64: image.base64,
        })),
      })
    } catch (error) {
      if (activeTabId === 'webui-default') trialMessagePending.current = false
      console.error('发送聊天消息失败:', error)
      setTabs((prev) =>
        prev.map((tab) => {
          if (tab.id !== activeTabId) return tab
          return {
            ...tab,
            isTyping: false,
            runtimeStatus: null,
          }
        })
      )
      toast({
        title: t('chat.toast.sendFailed'),
        description: t('chat.toast.currentSessionUnavailable'),
        variant: 'destructive',
      })
    }
  }, [activeTab, activeTabId, addMessageToTab, inputValue, selectedImages, filesByTab, filesEnabled, uploadingFiles, setupTrial, t, toast, userName])

  const handleAddFiles = async (files: FileList) => {
    if (!filesEnabled) return
    const sessionId = activeTab?.sessionInfo.session_id
    if (!sessionId || uploadingFiles) return
    const tabId = activeTabId
    setUploadingFiles(true)
    try {
      for (const file of Array.from(files).slice(0, Math.max(0, 6 - (filesByTab[tabId]?.length || 0)))) {
        const stored = await uploadFile(file, sessionId, true)
        setFilesByTab(previous => ({ ...previous, [tabId]: [...(previous[tabId] || []), stored] }))
      }
    } catch (e) {
      toast({ title: t('files.uploadFailed'), description: String(e), variant: 'destructive' })
    } finally { setUploadingFiles(false) }
  }

  const sendUserEmoji = useCallback(
    async (item: UserEmojiItem) => {
      if (!activeTab?.isConnected) {
        throw new Error(t('chat.toast.currentSessionUnavailable'))
      }

      const emoji = await loadUserEmojiPayload(item)
      const displayName =
        activeTab.type === 'virtual' ? activeTab.virtualConfig?.userName || userName : userName
      const timestamp = Date.now() / 1000

      await chatWsClient.sendMessage(activeTabId, '', displayName, {
        emojis: [
          {
            name: emoji.name,
            mime_type: emoji.mime_type,
            base64: emoji.base64,
          },
        ],
      })

      addMessageToTab(activeTabId, {
        id: generateMessageId('user-emoji'),
        type: 'user',
        content: `[${t('chat.media.emoji')}]`,
        message_type: 'rich',
        segments: [{ type: 'emoji', data: emoji.data_url }],
        timestamp,
        sender: {
          name: displayName,
          is_bot: false,
        },
      })
    },
    [activeTab, activeTabId, addMessageToTab, t, userName]
  )

  // 处理输入区的图片附件
  const handleAddImages = useCallback(
    async (files: FileList) => {
      const imageFiles = Array.from(files).filter((file) => file.type.startsWith('image/'))
      if (imageFiles.length === 0) {
        toast({
          title: t('chat.toast.imageUnsupported'),
          description: t('chat.toast.imageUnsupportedDesc'),
          variant: 'destructive',
        })
        return
      }

      const remainingSlots = MAX_CHAT_IMAGES - selectedImages.length
      if (remainingSlots <= 0) {
        toast({
          title: t('chat.toast.imageLimit'),
          description: t('chat.toast.imageLimitDesc', { count: MAX_CHAT_IMAGES }),
          variant: 'destructive',
        })
        return
      }

      const filesToRead = imageFiles.slice(0, remainingSlots)
      if (imageFiles.length > remainingSlots) {
        toast({
          title: t('chat.toast.imageLimit'),
          description: t('chat.toast.imageLimitDesc', { count: MAX_CHAT_IMAGES }),
        })
      }

      try {
        const attachments = await Promise.all(
          filesToRead.map((file) => readImageFile(file, generateMessageId('img')))
        )
        setSelectedImages((prev) => [...prev, ...attachments])
      } catch (error) {
        console.error('读取聊天图片失败', error)
        toast({
          title: t('chat.toast.imageReadFailed'),
          description: t('chat.toast.imageReadFailedDesc'),
          variant: 'destructive',
        })
      }
    },
    [selectedImages.length, t, toast]
  )

  const handleRemoveImage = useCallback((id: string) => {
    setSelectedImages((prev) => prev.filter((image) => image.id !== id))
  }, [])

  // 处理输入框左侧设置中的昵称变更
  const handleUpdateUserName = useCallback(
    (newName: string) => {
      const trimmed = newName.trim() || t('chat.userNameFallback')
      setUserName(trimmed)
      saveUserName(trimmed)

      if (tabsRef.current.find((tab) => tab.id === 'webui-default')?.isConnected) {
        void chatWsClient.updateNickname('webui-default', trimmed)
      }
    },
    [t]
  )

  const handleUpdateUserAvatar = useCallback(
    async (file: File) => {
      if ((file.type && !file.type.startsWith('image/')) || file.size > MAX_USER_AVATAR_BYTES) {
        toast({
          title: t('chat.toast.avatarUnsupported'),
          description: t('chat.toast.avatarUnsupportedDesc'),
          variant: 'destructive',
        })
        return
      }

      setIsUploadingUserAvatar(true)
      try {
        await uploadWebuiUserAvatar(userId, file)
        const nextAvatarVersion = Date.now()
        setUserAvatarVersion(nextAvatarVersion)
        saveUserAvatarVersion(nextAvatarVersion)
        toast({
          title: t('chat.toast.avatarSaved'),
          description: t('chat.toast.avatarSavedDesc'),
        })
      } catch (error) {
        console.error('保存用户头像失败', error)
        toast({
          title: t('chat.toast.avatarSaveFailed'),
          description:
            error instanceof Error ? error.message : t('chat.toast.avatarSaveFailedDesc'),
          variant: 'destructive',
        })
      } finally {
        setIsUploadingUserAvatar(false)
      }
    },
    [t, toast, userId]
  )

  // 关闭标签页
  const closeTab = useCallback((tabId: string, e?: React.MouseEvent | React.KeyboardEvent) => {
    e?.stopPropagation()

    // 不能关闭默认本地聊天标签页
    if (tabId === 'webui-default') {
      return
    }

    const unsubscribe = sessionUnsubscribeMapRef.current.get(tabId)
    if (unsubscribe) {
      unsubscribe()
      sessionUnsubscribeMapRef.current.delete(tabId)
    }

    void chatWsClient.closeSession(tabId)

    // 清理去重缓存
    processedMessagesMapRef.current.delete(tabId)

    // 移除标签页并更新存储；历史虚拟身份会话仍保留，只有用户主动关闭时才移除。
    setTabs((prev) => {
      const newTabs = prev.filter((t) => t.id !== tabId)
      const virtualTabsToSave: SavedVirtualTab[] = newTabs
        .filter((t) => t.type === 'virtual' && t.virtualConfig)
        .map((t) => ({
          id: t.id,
          label: t.label,
          virtualConfig: t.virtualConfig!,
          createdAt: Date.now(),
        }))
      saveVirtualTabs(virtualTabsToSave)
      return newTabs
    })

    setActiveTabId((current) => current === tabId ? 'webui-default' : current)
  }, [])

  // 切换标签页
  const switchTab = useCallback((tabId: string) => {
    setAgentSelected(false)
    setActiveObservedSessionId(null)
    setActiveTabId(tabId)
  }, [])

  const selectObservedSession = useCallback((sessionId: string) => {
    setAgentSelected(false)
    setSelectedObservedSession(sessionId)
    setActiveObservedSessionId(sessionId)
  }, [setSelectedObservedSession])

  // 在页内直接打开观察聊天流的设置弹窗，不再跳转到聊天管理页
  const openObservedSettings = useCallback((sessionId: string) => {
    // 名称来自聊天流资料，不依赖实时活动时间，避免每个监控事件都改变行回调。
    const info = knownChatStreams.find((chat) => chat.session_id === sessionId)
    setSettingsChat({
      session_id: sessionId,
      display_name: info?.display_name || sessionId,
    })
  }, [knownChatStreams])

  return (
    <div data-chat-workspace="true">
      {/* 桌面端：左侧会话侧边栏 */}
      <motion.div
        className="hidden shrink-0 md:block"
        variants={{
          initial: { opacity: 0, x: '-100%' },
          animate: { opacity: 1, x: 0 },
          exit: {
            opacity: 0,
            x: '-100%',
            transition: { duration: 0.28, ease: [0.22, 1, 0.36, 1] },
          },
        }}
        transition={{ type: 'spring', stiffness: 360, damping: 30, mass: 0.75 }}
      >
        <ChatWorkspaceSidebar
          agentEnabled={agentEnabled}
          agentActive={agentActive}
          onSelectAgent={() => setAgentSelected(true)}
          tabs={tabs}
          activeTabId={activeTabId}
          activeObservedSessionId={activeObservedSessionId}
          observedSessions={knownSessions}
          observedSessionsError={knownChatStreamsError}
          observedStageStatuses={observedStageStatuses}
          observedLatestMessages={observedLatestMessages}
          onSwitch={switchTab}
          onSelectObserved={selectObservedSession}
          onOpenObservedSettings={openObservedSettings}
          onClose={closeTab}
        />
      </motion.div>

      {/* 移动端：会话列表（未进入对话时） */}
      {!mobileRoomOpen && (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col md:hidden">
          <MobileChatList
            agentEnabled={agentEnabled}
            onOpenAgent={() => {
              setAgentSelected(true)
              setMobileRoomOpen(true)
            }}
            tabs={tabs}
            observedSessions={knownSessions}
            observedSessionsError={knownChatStreamsError}
            observedStageStatuses={observedStageStatuses}
            observedLatestMessages={observedLatestMessages}
            onOpenTab={(tabId) => {
              switchTab(tabId)
              setMobileRoomOpen(true)
            }}
            onOpenObserved={(sessionId) => {
              selectObservedSession(sessionId)
              setMobileRoomOpen(true)
            }}
            onOpenObservedSettings={openObservedSettings}
            onClose={(tabId) => closeTab(tabId)}
          />
        </div>
      )}

      {/* 主聊天区 */}
      <PageFrame asChild><motion.div
        className={cn('flex min-h-0 min-w-0 flex-1 flex-col', !mobileRoomOpen && 'max-md:hidden')}
        variants={{
          initial: { opacity: 0, y: '100%' },
          animate: { opacity: 1, y: 0 },
          exit: {
            opacity: 0,
            y: '100%',
            transition: { duration: 0.28, ease: [0.22, 1, 0.36, 1] },
          },
        }}
        transition={{ type: 'spring', stiffness: 360, damping: 30, mass: 0.75 }}
      >
        {setupTrial && (
          <section className="bg-card mx-4 mt-4 flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-lg border p-4" aria-live="polite">
            <div>
              <p className="text-sm font-semibold">{t(trialReplied ? 'chat.setupTrial.repliedTitle' : 'chat.setupTrial.title')}</p>
              <p className="text-muted-foreground mt-1 text-xs">{t(trialReplied ? 'chat.setupTrial.repliedDescription' : 'chat.setupTrial.description')}</p>
            </div>
            {trialReplied && (
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" onClick={() => navigate({ to: '/chat', search: {}, replace: true })}>
                  {t('chat.setupTrial.keepChatting')}
                </Button>
                {canOpenMessagePlatform && (
                  <Button size="sm" onClick={() => {
                    window.maibotDesktopHost!.openMessagePlatform()
                    void navigate({ to: '/chat', search: {}, replace: true })
                  }}>{t('chat.setupTrial.continue')}</Button>
                )}
              </div>
            )}
          </section>
        )}
        {agentActive ? (
          <>
            <div className="md:hidden">
              <Button variant="ghost" onClick={() => setMobileRoomOpen(false)}>{t('chat.mobile.back')}</Button>
            </div>
            <div className="min-h-0 flex-1"><AgentPage /></div>
          </>
        ) : (
        <ConversationTransition mode={activeObservedSessionId ? 'observe' : 'chat'}>
        {/* 移动端对话页顶栏：返回列表、会话名片与单个操作；观察页的顶栏悬浮在时间线上方 */}
        <div className="md:hidden">
          {!activeObservedSessionId && activeTab && (
            <MobileChatRoomBar
              title={getChatTabDisplayName(activeTab, t('chat.botNameFallback'))}
              subtitle={
                activeTab.isConnected ? t('chat.sidebar.online') : t('chat.sidebar.offline')
              }
              online={activeTab.isConnected}
              avatar={<MobileChatRoomTabAvatar tab={activeTab} />}
              onBack={() => setMobileRoomOpen(false)}
              userAvatar={{
                userId,
                userName,
                userAvatarVersion,
                isUploading: isUploadingUserAvatar,
                onUpdate: handleUpdateUserAvatar,
              }}
            />
          )}
        </div>

        {activeObservedSessionId ? (
          <div className="relative min-h-0 min-w-0 flex-1" data-chat-observed-session>
            <MaisakaMonitor sessionName={activeObservedSession?.sessionName}
              embedded
              reasoningReturnTo={`/chat?observe=${encodeURIComponent(activeObservedSessionId)}`}
              floatingToolbarContainer={isMobileChat ? observedToolbarSlot : undefined}
            />
            {isMobileChat && (
              <MobileFloatingHeader>
                <MobileChatRoomBar
                  title={activeObservedSession?.sessionName ?? activeObservedSessionId}
                  subtitle={
                    activeObservedStatus ? (
                      <StageStatusLabel status={activeObservedStatus} />
                    ) : (
                      t('chat.sidebar.observedBadge')
                    )
                  }
                  online={activeObservedStatus !== undefined}
                  avatar={
                    activeObservedSession ? (
                      <MobileChatRoomObservedAvatar session={activeObservedSession} />
                    ) : null
                  }
                  onBack={() => setMobileRoomOpen(false)}
                  onOpenSettings={() => openObservedSettings(activeObservedSessionId)}
                />
                <div
                  ref={setObservedToolbarSlot}
                  data-mobile-floating-toolbar="true"
                  className={cn('mx-3 mb-2 overflow-hidden rounded-full', MOBILE_GLASS_CLASS)}
                />
              </MobileFloatingHeader>
            )}
          </div>
        ) : (
          <>
            {activeTab && (
              <div className="hidden md:block">
                <ChatConversationHeader tab={activeTab} onClear={() => setTabs(current => current.map(tab => tab.id === activeTab.id ? { ...tab, messages: [] } : tab))} />
              </div>
            )}
            <MessageList
              key={activeTab?.id ?? 'empty-chat'}
              messages={activeTab?.messages ?? []}
              isLoadingHistory={isLoadingHistory}
              botDisplayName={activeTab?.sessionInfo.bot_name || t('chat.botNameFallback')}
              botQq={activeTab?.sessionInfo.bot_qq}
              userName={userName}
              userAvatarPlatform={
                activeTab?.type === 'virtual'
                  ? activeTab.virtualConfig?.platform
                  : userAvatarVersion
                    ? 'webui'
                    : undefined
              }
              userAvatarId={
                activeTab?.type === 'virtual' ? activeTab.virtualConfig?.userId : userId
              }
              userAvatarVersion={activeTab?.type === 'virtual' ? undefined : userAvatarVersion}
              language={i18n.language}
              runtimeStatus={activeTab?.runtimeStatus ?? null}
            />

            <ChatComposer
              files={filesEnabled ? filesByTab[activeTabId] || [] : []}
              onAddFiles={filesEnabled ? handleAddFiles : undefined}
              onRemoveFile={ref => setFilesByTab(previous => ({ ...previous, [activeTabId]: (previous[activeTabId] || []).filter(file => file.file_ref !== ref) }))}
              value={inputValue}
              onChange={setInputValue}
              onAddImages={handleAddImages}
              onRemoveImage={handleRemoveImage}
              onSendEmoji={sendUserEmoji}
              onSend={() => void sendMessage()}
              disabled={!activeTab?.isConnected || uploadingFiles}
              images={selectedImages}
              isConnected={!!activeTab?.isConnected}
              userId={userId}
              userName={userName}
              userAvatarVersion={userAvatarVersion}
              isUploadingUserAvatar={isUploadingUserAvatar}
              onUpdateUserAvatar={handleUpdateUserAvatar}
              onUpdateUserName={handleUpdateUserName}
            />
          </>
        )}
        </ConversationTransition>
        )}
      </motion.div></PageFrame>

      <ChatStreamSettingsDialog
        chat={settingsChat}
        onOpenChange={(open) => !open && setSettingsChat(null)}
        onDeleted={(sessionId) => {
          setSettingsChat(null)
          if (activeObservedSessionId === sessionId) {
            setActiveObservedSessionId(null)
          }
        }}
      />
    </div>
  )
}
