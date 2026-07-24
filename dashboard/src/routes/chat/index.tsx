import { motion } from 'motion/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useToast } from '@/hooks/use-toast'
import { uploadWebuiUserAvatar } from '@/lib/avatar-url'
import { chatWsClient } from '@/lib/chat-ws-client'
import {
  maisakaMonitorClient,
  type LlmErrorEvent,
  type LlmRetryEvent,
  type StageRemovedEvent,
  type StageStatusEvent,
} from '@/lib/maisaka-monitor-client'
import { loadUserEmojiPayload, type UserEmojiItem } from '@/lib/user-emoji-api'

import { ChatComposer } from './ChatComposer'
import { ChatTabBar } from './ChatTabBar'
import { ChatWorkspaceSidebar } from './ChatWorkspaceSidebar'
import { MessageList } from './MessageList'
import type {
  ChatImageAttachment,
  ChatIncomingImage,
  ChatTab,
  ChatMessage,
  ChatRuntimeStatus,
  MessageSegment,
  SavedVirtualTab,
  VirtualIdentityConfig,
  WsMessage,
} from './types'
import {
  getOrCreateUserId,
  getStoredUserAvatarVersion,
  getStoredUserName,
  getSavedVirtualTabs,
  saveUserAvatarVersion,
  saveUserName,
  saveVirtualTabs,
} from './utils'

const MAX_CHAT_IMAGES = 8
const MAX_USER_AVATAR_BYTES = 5 * 1024 * 1024

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
    tab.type === 'virtual' ? tab.virtualConfig?.platform || '' : tab.sessionInfo.platform || 'webui'
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
  const { t, i18n } = useTranslation()

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

  // 当前活动标签页
  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0]

  // 通用状态
  const [inputValue, setInputValue] = useState('')
  const [selectedImages, setSelectedImages] = useState<ChatImageAttachment[]>([])
  const [isLoadingHistory, setIsLoadingHistory] = useState(true)
  const [userName, setUserName] = useState(getStoredUserName())
  const [userAvatarVersion, setUserAvatarVersion] = useState(getStoredUserAvatarVersion)
  const [isUploadingUserAvatar, setIsUploadingUserAvatar] = useState(false)

  // 持久化用户 ID
  const userIdRef = useRef(getOrCreateUserId())

  const messageIdCounterRef = useRef(0)
  const monitorStatusesRef = useRef<Map<string, StageStatusEvent>>(new Map())
  const processedMessagesMapRef = useRef<Map<string, Set<string>>>(new Map())
  const sessionUnsubscribeMapRef = useRef<Map<string, () => void>>(new Map())
  const tabsRef = useRef<ChatTab[]>([])
  const { toast } = useToast()

  useEffect(() => {
    tabsRef.current = tabs
  }, [tabs])

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
      prev.map((tab) => (tab.id === tabId ? { ...tab, messages: [...tab.messages, message] } : tab))
    )
  }, [])

  const handleSessionMessage = useCallback(
    (
      tabId: string,
      tabType: 'webui' | 'virtual',
      config: VirtualIdentityConfig | undefined,
      data: WsMessage
    ) => {
      switch (data.type) {
        case 'session_info':
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
                return nextTab
              }
              return {
                ...nextTab,
                runtimeStatus: buildRuntimeStatusFromStage(currentStatus),
              }
            })
          )
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
          const senderUserId = data.sender?.user_id
          const currentUserId = tabType === 'virtual' && config ? config.userId : userIdRef.current

          const normalizeSenderId = senderUserId ? senderUserId.replace(/^webui_user_/, '') : ''
          const normalizeCurrentId = currentUserId ? currentUserId.replace(/^webui_user_/, '') : ''
          if (normalizeSenderId && normalizeCurrentId && normalizeSenderId === normalizeCurrentId) {
            break
          }

          const processedSet = processedMessagesMapRef.current.get(tabId) || new Set()
          const contentHash = `user-${data.content}-${Math.floor((data.timestamp || 0) * 1000)}`
          if (processedSet.has(contentHash)) {
            break
          }

          processedSet.add(contentHash)
          processedMessagesMapRef.current.set(tabId, processedSet)
          if (processedSet.size > 100) {
            const firstKey = processedSet.values().next().value
            if (firstKey) processedSet.delete(firstKey)
          }

          addMessageToTab(tabId, {
            id: data.message_id || generateMessageId('user'),
            type: 'user',
            content: data.content || '',
            message_type:
              (data.images && data.images.length > 0) || (data.emojis && data.emojis.length > 0)
                ? 'rich'
                : 'text',
            segments:
              (data.images && data.images.length > 0) || (data.emojis && data.emojis.length > 0)
                ? buildMessageSegments(data.raw_content || '', data.images || [], data.emojis || [])
                : undefined,
            timestamp: data.timestamp || Date.now() / 1000,
            sender: data.sender,
          })
          break
        }

        case 'bot_message': {
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
                messages: [...tab.messages, newMessage],
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
                messages: [
                  ...tab.messages,
                  {
                    id: generateMessageId('error'),
                    type: 'error' as const,
                    content: data.content || t('chat.message.errorFallback'),
                    timestamp: data.timestamp || Date.now() / 1000,
                  },
                ],
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
          const formattedMessages: ChatMessage[] = historyMessages.map((msg) => {
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
    (tabId: string, tabType: 'webui' | 'virtual', config?: VirtualIdentityConfig) => {
      if (sessionUnsubscribeMapRef.current.has(tabId)) {
        return
      }

      const unsubscribe = chatWsClient.onSessionMessage(tabId, (message) => {
        handleSessionMessage(tabId, tabType, config, message as unknown as WsMessage)
      })
      sessionUnsubscribeMapRef.current.set(tabId, unsubscribe)
    },
    [handleSessionMessage]
  )

  const openSessionForTab = useCallback(
    async (tabId: string, tabType: 'webui' | 'virtual', config?: VirtualIdentityConfig) => {
      ensureSessionListener(tabId, tabType, config)
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
            user_id: userIdRef.current,
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
    [ensureSessionListener, t, toast, updateTab, userName]
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
              const status = event.data.entries.find((entry) => matchesMonitorTarget(tab, entry))
              if (!status) {
                return tab
              }
              return {
                ...tab,
                runtimeStatus: buildRuntimeStatusFromStage(status),
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
              if (!nextStatus && tab.runtimeStatus?.kind === 'error') {
                return tab
              }
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
    if ((!inputValue.trim() && selectedImages.length === 0) || !activeTab?.isConnected) {
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
      message_type: imagesToSend.length > 0 ? 'rich' : 'text',
      segments:
        imagesToSend.length > 0 ? buildMessageSegments(messageContent, imagesToSend) : undefined,
      timestamp: currentTimestamp,
      sender: {
        name: displayName,
        is_bot: false,
      },
    }
    addMessageToTab(activeTabId, userMessage)

    setInputValue('')
    setSelectedImages([])

    try {
      await chatWsClient.sendMessage(activeTabId, messageContent, displayName, {
        images: imagesToSend.map((image) => ({
          name: image.name,
          mime_type: image.mime_type,
          base64: image.base64,
        })),
      })
    } catch (error) {
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
  }, [activeTab, activeTabId, addMessageToTab, inputValue, selectedImages, t, toast, userName])

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

  // 处理键盘事件
  // 处理昵称变更（来自侧边栏）
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

  const handleUpdateUserName = useCallback(
    (newName: string) => {
      const trimmed = newName.trim() || t('chat.userNameFallback')
      setUserName(trimmed)
      saveUserName(trimmed)

      if (activeTab?.isConnected) {
        void chatWsClient.updateNickname(activeTabId, trimmed)
      }
    },
    [activeTab?.isConnected, activeTabId, t]
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
        await uploadWebuiUserAvatar(userIdRef.current, file)
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
    [t, toast]
  )

  // 关闭标签页
  const closeTab = (tabId: string, e?: React.MouseEvent | React.KeyboardEvent) => {
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

    if (activeTabId === tabId) {
      setActiveTabId('webui-default')
    }
  }

  // 切换标签页
  const switchTab = (tabId: string) => {
    setActiveTabId(tabId)
  }

  return (
    <div className="bg-background flex h-full min-h-0">
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
          tabs={tabs}
          activeTabId={activeTabId}
          userId={userIdRef.current}
          userName={userName}
          userAvatarVersion={userAvatarVersion}
          isUploadingUserAvatar={isUploadingUserAvatar}
          onSwitch={switchTab}
          onClose={closeTab}
          onUpdateUserAvatar={handleUpdateUserAvatar}
          onUpdateUserName={handleUpdateUserName}
        />
      </motion.div>

      {/* 主聊天区 */}
      <motion.div
        className="flex min-w-0 flex-1 flex-col"
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
        {/* 移动端会话切换条 */}
        <div className="md:hidden">
          <ChatTabBar
            tabs={tabs}
            activeTabId={activeTabId}
            userId={userIdRef.current}
            userName={userName}
            userAvatarVersion={userAvatarVersion}
            isUploadingUserAvatar={isUploadingUserAvatar}
            onSwitch={switchTab}
            onClose={closeTab}
            onUpdateUserAvatar={handleUpdateUserAvatar}
          />
        </div>

        <MessageList
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
            activeTab?.type === 'virtual' ? activeTab.virtualConfig?.userId : userIdRef.current
          }
          userAvatarVersion={activeTab?.type === 'virtual' ? undefined : userAvatarVersion}
          language={i18n.language}
          runtimeStatus={activeTab?.runtimeStatus ?? null}
        />

        <ChatComposer
          value={inputValue}
          onChange={setInputValue}
          onAddImages={handleAddImages}
          onRemoveImage={handleRemoveImage}
          onSendEmoji={sendUserEmoji}
          onSend={() => void sendMessage()}
          disabled={!activeTab?.isConnected}
          images={selectedImages}
          isConnected={!!activeTab?.isConnected}
          userId={userIdRef.current}
        />
      </motion.div>
    </div>
  )
}
