import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useToast } from '@/hooks/use-toast'
import { chatWsClient } from '@/lib/chat-ws-client'
import { ApiError, backendApi } from '@/lib/http'

import { ChatComposer } from './ChatComposer'
import { ChatTabBar } from './ChatTabBar'
import { ChatWorkspaceSidebar } from './ChatWorkspaceSidebar'
import { MessageList } from './MessageList'
import type {
  ChatImageAttachment,
  ChatIncomingImage,
  ChatTab,
  ChatMessage,
  MessageSegment,
  PersonInfo,
  PlatformInfo,
  SavedVirtualTab,
  VirtualIdentityConfig,
  WsMessage,
} from './types'
import {
  getOrCreateUserId,
  getStoredUserName,
  getSavedVirtualTabs,
  saveUserName,
  saveVirtualTabs,
} from './utils'
import { VirtualIdentityDialog } from './VirtualIdentityDialog'

const MAX_CHAT_IMAGES = 8

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
  images: Array<ChatImageAttachment | ChatIncomingImage>
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

  // 虚拟身份配置对话框状态
  const [showVirtualConfig, setShowVirtualConfig] = useState(false)
  const [platforms, setPlatforms] = useState<PlatformInfo[]>([])
  const [persons, setPersons] = useState<PersonInfo[]>([])
  const [isLoadingPlatforms, setIsLoadingPlatforms] = useState(false)
  const [isLoadingPersons, setIsLoadingPersons] = useState(false)
  const [personSearchQuery, setPersonSearchQuery] = useState('')
  const [tempVirtualConfig, setTempVirtualConfig] = useState<VirtualIdentityConfig>({
    platform: '',
    personId: '',
    userId: '',
    userName: '',
    groupName: '',
    groupId: '',
  })

  // 持久化用户 ID
  const userIdRef = useRef(getOrCreateUserId())

  const messageIdCounterRef = useRef(0)
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

  // 获取平台列表
  const fetchPlatforms = useCallback(async () => {
    setIsLoadingPlatforms(true)
    try {
      const data = await backendApi.get<{ platforms?: PlatformInfo[] }>('/api/chat/platforms')
      setPlatforms(data.platforms || [])
    } catch (e) {
      if (e instanceof ApiError && e.status !== undefined && (e.status < 200 || e.status >= 300)) {
        // HTTP 层失败
        console.error('[Chat] 获取平台列表失败: HTTP', e.status)
        toast({
          title: t('chat.toast.platformFailed'),
          description: t('chat.toast.serverError', { status: e.status }),
          variant: 'destructive',
        })
      } else if (e instanceof ApiError && e.status !== undefined) {
        // HTTP 成功但响应不是合法 JSON（后端不可用，命中了前端页面等）
        console.error('[Chat] 获取平台列表失败: 非 JSON 响应:', e.message)
        toast({
          title: t('chat.toast.connectionFailed'),
          description: t('chat.toast.backendUnavailable'),
          variant: 'destructive',
        })
      } else {
        console.error('[Chat] 获取平台列表失败:', e)
        toast({
          title: t('chat.toast.networkError'),
          description: t('chat.toast.backendUnavailableShort'),
          variant: 'destructive',
        })
      }
    } finally {
      setIsLoadingPlatforms(false)
    }
  }, [t, toast])

  // 获取用户列表
  const fetchPersons = useCallback(async (platform: string, search?: string) => {
    setIsLoadingPersons(true)
    try {
      const data = await backendApi.get<{ persons?: PersonInfo[] }>('/api/chat/persons', {
        query: {
          platform: platform || undefined,
          search: search || undefined,
          limit: 50,
        },
      })
      setPersons(data.persons || [])
    } catch (e) {
      console.error('[Chat] 获取用户列表失败:', e)
    } finally {
      setIsLoadingPersons(false)
    }
  }, [])

  // 当平台选择变化时获取用户列表
  useEffect(() => {
    if (tempVirtualConfig.platform) {
      fetchPersons(tempVirtualConfig.platform, personSearchQuery)
    }
  }, [tempVirtualConfig.platform, personSearchQuery, fetchPersons])

  const handleSessionMessage = useCallback(
    (
      tabId: string,
      tabType: 'webui' | 'virtual',
      config: VirtualIdentityConfig | undefined,
      data: WsMessage
    ) => {
      switch (data.type) {
        case 'session_info':
          updateTab(tabId, {
            sessionInfo: {
              session_id: data.session_id,
              user_id: data.user_id,
              user_name: data.user_name,
              bot_name: data.bot_name,
              bot_qq: data.bot_qq,
            },
          })
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
            message_type: data.images && data.images.length > 0 ? 'rich' : 'text',
            segments:
              data.images && data.images.length > 0
                ? buildMessageSegments(data.content || '', data.images)
                : undefined,
            timestamp: data.timestamp || Date.now() / 1000,
            sender: data.sender,
          })
          break
        }

        case 'bot_message': {
          updateTab(tabId, { isTyping: false })
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
          updateTab(tabId, { isTyping: data.is_typing || false })
          break

        case 'error':
          setTabs((prev) =>
            prev.map((tab) => {
              if (tab.id !== tabId) return tab
              return {
                ...tab,
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
            user_id: config.userId,
            user_name: config.userName,
            platform: config.platform,
            person_id: config.personId,
            group_name: config.groupName || t('chat.virtualGroupFallback'),
            group_id: config.groupId,
          })
        } else {
          await chatWsClient.openSession(tabId, {
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
        void chatWsClient.closeSession(tab.id)
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // 打开虚拟身份配置对话框（新建标签页用）
  const openVirtualConfig = () => {
    setTempVirtualConfig({
      platform: '',
      personId: '',
      userId: '',
      userName: '',
      groupName: '',
      groupId: '',
    })
    setPersonSearchQuery('')
    fetchPlatforms()
    setShowVirtualConfig(true)
  }

  // 创建新的虚拟身份标签页
  const createVirtualTab = () => {
    if (!tempVirtualConfig.platform || !tempVirtualConfig.personId) {
      toast({
        title: t('chat.toast.incompleteConfig'),
        description: t('chat.toast.selectPlatformAndUser'),
        variant: 'destructive',
      })
      return
    }

    // 生成稳定的虚拟群 ID（基于平台和用户 ID，不包含时间戳）
    const stableGroupId = `webui_virtual_group_${tempVirtualConfig.platform}_${tempVirtualConfig.userId}`

    // 生成新标签页ID
    const newTabId = `virtual-${tempVirtualConfig.platform}-${tempVirtualConfig.userId}-${Date.now()}`
    const tabLabel = tempVirtualConfig.userName || tempVirtualConfig.userId

    // 创建新标签页，包含稳定的 groupId
    const newTab: ChatTab = {
      id: newTabId,
      type: 'virtual',
      label: tabLabel,
      virtualConfig: {
        ...tempVirtualConfig,
        groupId: stableGroupId,
      },
      messages: [],
      isConnected: false,
      isTyping: false,
      sessionInfo: {},
    }

    setTabs((prev) => {
      const newTabs = [...prev, newTab]
      // 保存虚拟标签页到 localStorage
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
    setActiveTabId(newTabId)
    setShowVirtualConfig(false)

    // 初始化去重缓存
    processedMessagesMapRef.current.set(newTabId, new Set())

    void openSessionForTab(newTabId, 'virtual', {
      ...tempVirtualConfig,
      groupId: stableGroupId,
    })

    toast({
      title: t('chat.toast.virtualTabCreated'),
      description: t('chat.toast.virtualTabCreatedDesc', { label: tabLabel }),
    })
  }

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

    // 移除标签页并更新存储
    setTabs((prev) => {
      const newTabs = prev.filter((t) => t.id !== tabId)
      // 更新 localStorage 中的虚拟标签页
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

    // 如果关闭的是当前标签页，切换到默认标签页
    if (activeTabId === tabId) {
      setActiveTabId('webui-default')
    }
  }

  // 切换标签页
  const switchTab = (tabId: string) => {
    setActiveTabId(tabId)
  }

  // 选择用户
  const selectPerson = (person: PersonInfo) => {
    setTempVirtualConfig((prev) => ({
      ...prev,
      personId: person.person_id,
      userId: person.user_id,
      userName: person.nickname || person.person_name,
    }))
  }

  return (
    <div className="bg-background flex h-full min-h-0">
      {/* 虚拟身份配置对话框 */}
      <VirtualIdentityDialog
        open={showVirtualConfig}
        onOpenChange={setShowVirtualConfig}
        platforms={platforms}
        persons={persons}
        isLoadingPlatforms={isLoadingPlatforms}
        isLoadingPersons={isLoadingPersons}
        personSearchQuery={personSearchQuery}
        setPersonSearchQuery={setPersonSearchQuery}
        tempVirtualConfig={tempVirtualConfig}
        setTempVirtualConfig={setTempVirtualConfig}
        onSelectPerson={selectPerson}
        onCreateVirtualTab={createVirtualTab}
      />

      {/* 桌面端：左侧会话侧边栏 */}
      <ChatWorkspaceSidebar
        className="hidden md:flex"
        tabs={tabs}
        activeTabId={activeTabId}
        userName={userName}
        onSwitch={switchTab}
        onClose={closeTab}
        onAddVirtual={openVirtualConfig}
        onUpdateUserName={handleUpdateUserName}
      />

      {/* 主聊天区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 移动端会话切换条 */}
        <div className="md:hidden">
          <ChatTabBar
            tabs={tabs}
            activeTabId={activeTabId}
            onSwitch={switchTab}
            onClose={closeTab}
            onAddVirtual={openVirtualConfig}
          />
        </div>

        <MessageList
          messages={activeTab?.messages ?? []}
          isLoadingHistory={isLoadingHistory}
          botDisplayName={activeTab?.sessionInfo.bot_name || t('chat.botNameFallback')}
          botQq={activeTab?.sessionInfo.bot_qq}
          userName={userName}
          language={i18n.language}
        />

        <ChatComposer
          value={inputValue}
          onChange={setInputValue}
          onAddImages={handleAddImages}
          onRemoveImage={handleRemoveImage}
          onSend={() => void sendMessage()}
          disabled={!activeTab?.isConnected}
          images={selectedImages}
          isConnected={!!activeTab?.isConnected}
        />
      </div>
    </div>
  )
}
