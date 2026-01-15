import { useState, useRef, useEffect, useCallback } from 'react'
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
// Card 组件已移除，改用更简洁的全屏布局
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Send, Bot, User, Loader2, WifiOff, Wifi, RefreshCw, Edit2, Users, Search, X, UserCircle2, Globe, Plus, MessageSquare } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useToast } from '@/hooks/use-toast'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"

// 生成唯一用户 ID
function generateUserId(): string {
  return 'webui_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now().toString(36)
}

// 从 localStorage 获取或生成用户 ID
function getOrCreateUserId(): string {
  const storageKey = 'maibot_webui_user_id'
  let userId = localStorage.getItem(storageKey)
  if (!userId) {
    userId = generateUserId()
    localStorage.setItem(storageKey, userId)
  }
  return userId
}

// 从 localStorage 获取用户昵称
function getStoredUserName(): string {
  return localStorage.getItem('maibot_webui_user_name') || 'WebUI用户'
}

// 保存用户昵称到 localStorage
function saveUserName(name: string): void {
  localStorage.setItem('maibot_webui_user_name', name)
}

// 虚拟标签页持久化存储 key
const VIRTUAL_TABS_STORAGE_KEY = 'maibot_webui_virtual_tabs'

// 保存的虚拟标签页配置
interface SavedVirtualTab {
  id: string
  label: string
  virtualConfig: VirtualIdentityConfig
  createdAt: number
}

// 从 localStorage 获取保存的虚拟标签页
function getSavedVirtualTabs(): SavedVirtualTab[] {
  try {
    const saved = localStorage.getItem(VIRTUAL_TABS_STORAGE_KEY)
    if (saved) {
      return JSON.parse(saved)
    }
  } catch (e) {
    console.error('[Chat] 加载虚拟标签页失败:', e)
  }
  return []
}

// 保存虚拟标签页到 localStorage
function saveVirtualTabs(tabs: SavedVirtualTab[]): void {
  try {
    localStorage.setItem(VIRTUAL_TABS_STORAGE_KEY, JSON.stringify(tabs))
  } catch (e) {
    console.error('[Chat] 保存虚拟标签页失败:', e)
  }
}

// 平台信息类型
interface PlatformInfo {
  platform: string
  count: number
}

// 用户信息类型（从后端获取的人物信息）
interface PersonInfo {
  person_id: string
  user_id: string
  person_name: string
  nickname: string | null
  platform: string
  is_known: boolean
}

// 虚拟身份配置
interface VirtualIdentityConfig {
  platform: string
  personId: string
  userId: string
  userName: string
  groupName: string
  groupId: string  // 虚拟群 ID，用于持久化历史记录
}

// 聊天标签页
interface ChatTab {
  id: string
  type: 'webui' | 'virtual'
  label: string
  virtualConfig?: VirtualIdentityConfig
  messages: ChatMessage[]
  isConnected: boolean
  isTyping: boolean
  sessionInfo: {
    session_id?: string
    user_id?: string
    user_name?: string
    bot_name?: string
  }
}

// 消息段类型
interface MessageSegment {
  type: 'text' | 'image' | 'emoji' | 'face' | 'voice' | 'video' | 'music' | 'file' | 'reply' | 'forward' | 'unknown'
  data: string | number | object
  original_type?: string
}

// 消息类型
interface ChatMessage {
  id: string
  type: 'user' | 'bot' | 'system' | 'error' | 'thinking'
  content: string
  timestamp: number
  message_type?: 'text' | 'rich'  // 消息格式类型
  segments?: MessageSegment[]  // 富文本消息段
  sender?: {
    name: string
    user_id?: string
    is_bot?: boolean
  }
}

// WebSocket 消息类型
interface WsMessage {
  type: string
  content?: string
  message_id?: string
  timestamp?: number
  is_typing?: boolean
  session_id?: string
  user_id?: string
  user_name?: string
  bot_name?: string
  sender?: {
    name: string
    user_id?: string
    is_bot?: boolean
  }
  // 历史消息列表（用于 type: 'history'）
  messages?: Array<{
    id?: string
    content: string
    timestamp: number
    sender_name?: string
    sender_id?: string
    is_bot?: boolean
  }>
  group_id?: string
  // 富文本消息
  message_type?: string
  segments?: MessageSegment[]
}

// 渲染单个消息段
function RenderMessageSegment({ segment }: { segment: MessageSegment }) {
  switch (segment.type) {
    case 'text':
      return <span className="whitespace-pre-wrap">{String(segment.data)}</span>
    
    case 'image':
    case 'emoji':
      return (
        <img 
          src={String(segment.data)} 
          alt={segment.type === 'emoji' ? '表情包' : '图片'}
          className={cn(
            "rounded-lg max-w-full",
            segment.type === 'emoji' ? "max-h-32" : "max-h-64"
          )}
          loading="lazy"
          onError={(e) => {
            // 图片加载失败时显示占位符
            const target = e.target as HTMLImageElement
            target.style.display = 'none'
            target.parentElement?.insertAdjacentHTML(
              'beforeend',
              `<span class="text-muted-foreground text-xs">[${segment.type === 'emoji' ? '表情包' : '图片'}加载失败]</span>`
            )
          }}
        />
      )
    
    case 'voice':
      return (
        <div className="flex items-center gap-2">
          <audio 
            controls 
            src={String(segment.data)} 
            className="max-w-[200px] h-8"
          >
            您的浏览器不支持音频播放
          </audio>
        </div>
      )
    
    case 'video':
      return (
        <video 
          controls 
          src={String(segment.data)} 
          className="rounded-lg max-w-full max-h-64"
        >
          您的浏览器不支持视频播放
        </video>
      )
    
    case 'face':
      // QQ 原生表情，显示为文本
      return <span className="text-muted-foreground">[表情:{String(segment.data)}]</span>
    
    case 'music':
      return <span className="text-muted-foreground">[音乐分享]</span>
    
    case 'file':
      return <span className="text-muted-foreground">[文件: {String(segment.data)}]</span>
    
    case 'reply':
      return <span className="text-muted-foreground text-xs">[回复消息]</span>
    
    case 'forward':
      return <span className="text-muted-foreground">[转发消息]</span>
    
    case 'unknown':
    default:
      return <span className="text-muted-foreground">[{segment.original_type || '未知消息'}]</span>
  }
}

// 渲染消息内容（支持富文本）
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function RenderMessageContent({ message, isBot: _isBot }: { message: ChatMessage; isBot: boolean }) {
  // 如果是富文本消息，渲染消息段
  if (message.message_type === 'rich' && message.segments && message.segments.length > 0) {
    return (
      <div className="flex flex-col gap-2">
        {message.segments.map((segment, index) => (
          <RenderMessageSegment key={index} segment={segment} />
        ))}
      </div>
    )
  }
  
  // 普通文本消息
  return <span className="whitespace-pre-wrap">{message.content}</span>
}

export function ChatPage() {
  // 默认 WebUI 标签页
  const defaultTab: ChatTab = {
    id: 'webui-default',
    type: 'webui',
    label: 'WebUI',
    messages: [],
    isConnected: false,
    isTyping: false,
    sessionInfo: {},
  }

  // 从存储中恢复虚拟标签页
  const initializeTabs = (): ChatTab[] => {
    const savedVirtualTabs = getSavedVirtualTabs()
    const restoredTabs: ChatTab[] = savedVirtualTabs.map(saved => {
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
  const activeTab = tabs.find(t => t.id === activeTabId) || tabs[0]
  
  // 通用状态
  const [inputValue, setInputValue] = useState('')
  const [isConnecting, setIsConnecting] = useState(false)
  const [isLoadingHistory, setIsLoadingHistory] = useState(true)
  const [userName, setUserName] = useState(getStoredUserName())
  const [isEditingName, setIsEditingName] = useState(false)
  const [tempUserName, setTempUserName] = useState('')
  
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
  
  // 每个标签页的 WebSocket 连接
  const wsMapRef = useRef<Map<string, WebSocket>>(new Map())
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const reconnectTimeoutMapRef = useRef<Map<string, number>>(new Map())
  const messageIdCounterRef = useRef(0)
  const processedMessagesMapRef = useRef<Map<string, Set<string>>>(new Map())
  const { toast } = useToast()

  // 生成唯一消息 ID
  const generateMessageId = (prefix: string) => {
    messageIdCounterRef.current += 1
    return `${prefix}-${Date.now()}-${messageIdCounterRef.current}-${Math.random().toString(36).substr(2, 9)}`
  }

  // 更新指定标签页
  const updateTab = useCallback((tabId: string, updates: Partial<ChatTab>) => {
    setTabs(prev => prev.map(tab => 
      tab.id === tabId ? { ...tab, ...updates } : tab
    ))
  }, [])

  // 向指定标签页添加消息
  const addMessageToTab = useCallback((tabId: string, message: ChatMessage) => {
    setTabs(prev => prev.map(tab => 
      tab.id === tabId ? { ...tab, messages: [...tab.messages, message] } : tab
    ))
  }, [])

  // 滚动到底部
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  // 自动滚动
  useEffect(() => {
    scrollToBottom()
  }, [activeTab?.messages, scrollToBottom])

  // 获取平台列表
  const fetchPlatforms = useCallback(async () => {
    setIsLoadingPlatforms(true)
    try {
      const response = await fetchWithAuth('/api/chat/platforms')
      console.log('[Chat] 平台列表响应:', response.status, response.headers.get('content-type'))
      if (response.ok) {
        const contentType = response.headers.get('content-type')
        if (contentType && contentType.includes('application/json')) {
          const data = await response.json()
          console.log('[Chat] 平台列表数据:', data)
          setPlatforms(data.platforms || [])
        } else {
          const text = await response.text()
          console.error('[Chat] 获取平台列表失败: 非 JSON 响应:', text.substring(0, 200))
          toast({
            title: '连接失败',
            description: '无法连接到后端服务，请确保 MaiBot 已启动',
            variant: 'destructive',
          })
        }
      } else {
        console.error('[Chat] 获取平台列表失败: HTTP', response.status)
        toast({
          title: '获取平台失败',
          description: `服务器返回错误: ${response.status}`,
          variant: 'destructive',
        })
      }
    } catch (e) {
      console.error('[Chat] 获取平台列表失败:', e)
      toast({
        title: '网络错误',
        description: '无法连接到后端服务',
        variant: 'destructive',
      })
    } finally {
      setIsLoadingPlatforms(false)
    }
  }, [toast])

  // 获取用户列表
  const fetchPersons = useCallback(async (platform: string, search?: string) => {
    setIsLoadingPersons(true)
    try {
      const params = new URLSearchParams()
      if (platform) params.append('platform', platform)
      if (search) params.append('search', search)
      params.append('limit', '50')
      
      const response = await fetchWithAuth(`/api/chat/persons?${params.toString()}`)
      if (response.ok) {
        const contentType = response.headers.get('content-type')
        if (contentType && contentType.includes('application/json')) {
          const data = await response.json()
          setPersons(data.persons || [])
        } else {
          console.error('[Chat] 获取用户列表失败: 后端返回非 JSON 响应')
        }
      }
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

  // 加载聊天历史到指定标签页
  const loadChatHistoryForTab = useCallback(async (tabId: string, groupId?: string) => {
    setIsLoadingHistory(true)
    try {
      const params = new URLSearchParams()
      params.append('user_id', userIdRef.current)
      params.append('limit', '50')
      if (groupId) {
        params.append('group_id', groupId)
      }
      const url = `/api/chat/history?${params.toString()}`
      console.log('[Chat] 正在加载历史消息:', url)
      
      const response = await fetchWithAuth(url)
      
      if (response.ok) {
        const text = await response.text()
        try {
          const data = JSON.parse(text)
          
          if (data.messages && data.messages.length > 0) {
            const historyMessages: ChatMessage[] = data.messages.map((msg: {
              id: string
              type: string
              content: string
              timestamp: number
              sender_name?: string
              user_id?: string
              is_bot?: boolean
            }) => ({
              id: msg.id,
              type: msg.type as 'user' | 'bot' | 'system' | 'error',
              content: msg.content,
              timestamp: msg.timestamp,
              sender: {
                name: msg.sender_name || (msg.is_bot ? '麦麦' : 'WebUI用户'),
                user_id: msg.user_id,
                is_bot: msg.is_bot
              }
            }))
            
            // 更新标签页的消息
            updateTab(tabId, { messages: historyMessages })
            
            // 将历史消息添加到去重缓存
            const processedSet = processedMessagesMapRef.current.get(tabId) || new Set()
            historyMessages.forEach(msg => {
              if (msg.type === 'bot') {
                const contentHash = `bot-${msg.content}-${Math.floor(msg.timestamp * 1000)}`
                processedSet.add(contentHash)
              }
            })
            processedMessagesMapRef.current.set(tabId, processedSet)
          }
        } catch (parseError) {
          console.error('[Chat] JSON 解析失败:', parseError)
        }
      }
    } catch (e) {
      console.error('[Chat] 加载历史消息失败:', e)
    } finally {
      setIsLoadingHistory(false)
    }
  }, [updateTab])

  // 为指定标签页连接 WebSocket（异步，需要先获取认证 token）
  const connectWebSocketForTab = useCallback(async (tabId: string, tabType: 'webui' | 'virtual', config?: VirtualIdentityConfig) => {
    // 如果已经有连接，不要重复创建
    const existingWs = wsMapRef.current.get(tabId)
    if (existingWs?.readyState === WebSocket.OPEN || 
        existingWs?.readyState === WebSocket.CONNECTING) {
      console.log(`[Tab ${tabId}] WebSocket 已存在，跳过连接`)
      return
    }

    setIsConnecting(true)

    // 先获取临时 WebSocket token
    let wsToken: string | null = null
    try {
      const tokenResponse = await fetchWithAuth('/api/webui/ws-token')
      if (tokenResponse.ok) {
        const tokenData = await tokenResponse.json()
        if (tokenData.success && tokenData.token) {
          wsToken = tokenData.token
        } else {
          console.warn(`[Tab ${tabId}] 获取 WebSocket token 失败: ${tokenData.message || '未登录'}`)
          setIsConnecting(false)
          return
        }
      }
    } catch (error) {
      console.error(`[Tab ${tabId}] 获取 WebSocket token 失败:`, error)
      setIsConnecting(false)
      return
    }

    // 此时 wsToken 一定有值（前面已经 return）
    if (!wsToken) {
      setIsConnecting(false)
      return
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const params = new URLSearchParams()
    
    // 添加 token 到参数
    params.append('token', wsToken)
    
    if (tabType === 'virtual' && config) {
      params.append('user_id', config.userId)
      params.append('user_name', config.userName)
      params.append('platform', config.platform)
      params.append('person_id', config.personId)
      params.append('group_name', config.groupName || 'WebUI虚拟群聊')
      // 传递稳定的 group_id，确保历史记录能正确加载
      if (config.groupId) {
        params.append('group_id', config.groupId)
      }
    } else {
      params.append('user_id', userIdRef.current)
      params.append('user_name', userName)
    }
    
    const wsUrl = `${protocol}//${window.location.host}/api/chat/ws?${params.toString()}`
    console.log(`[Tab ${tabId}] 正在连接 WebSocket:`, wsUrl)

    try {
      const ws = new WebSocket(wsUrl)
      wsMapRef.current.set(tabId, ws)

      ws.onopen = () => {
        updateTab(tabId, { isConnected: true })
        setIsConnecting(false)
        console.log(`[Tab ${tabId}] WebSocket 已连接`)
      }

      ws.onmessage = (event) => {
        try {
          const data: WsMessage = JSON.parse(event.data)
          
          switch (data.type) {
            case 'session_info':
              updateTab(tabId, {
                sessionInfo: {
                  session_id: data.session_id,
                  user_id: data.user_id,
                  user_name: data.user_name,
                  bot_name: data.bot_name,
                }
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
              // 检查是否是自己发的消息（已在发送时显示，跳过广播回来的）
              const senderUserId = data.sender?.user_id
              const currentUserId = tabType === 'virtual' && config 
                ? config.userId 
                : userIdRef.current
              
              console.log(`[Tab ${tabId}] 收到 user_message, sender: ${senderUserId}, current: ${currentUserId}`)
              
              // 标准化 user_id（去掉可能的前缀）
              const normalizeSenderId = senderUserId ? senderUserId.replace(/^webui_user_/, '') : ''
              const normalizeCurrentId = currentUserId ? currentUserId.replace(/^webui_user_/, '') : ''
              
              // 如果是自己发的消息，跳过（避免重复显示）
              if (normalizeSenderId && normalizeCurrentId && normalizeSenderId === normalizeCurrentId) {
                console.log(`[Tab ${tabId}] 跳过自己的消息（user_id 匹配）`)
                break
              }
              
              // 额外的消息去重：检查内容和时间戳
              const processedSet = processedMessagesMapRef.current.get(tabId) || new Set()
              const contentHash = `user-${data.content}-${Math.floor((data.timestamp || 0) * 1000)}`
              if (processedSet.has(contentHash)) {
                console.log(`[Tab ${tabId}] 跳过自己的消息（内容去重）`)
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
              
              // 移除"思考中"占位消息，添加真实的机器人回复
              setTabs(prev => prev.map(tab => {
                if (tab.id !== tabId) return tab
                // 过滤掉 thinking 类型的消息
                const filteredMessages = tab.messages.filter(msg => msg.type !== 'thinking')
                const newMessage: ChatMessage = {
                  id: generateMessageId('bot'),
                  type: 'bot',
                  content: data.content || '',
                  message_type: (data.message_type === 'rich' ? 'rich' : 'text') as 'text' | 'rich',
                  segments: data.segments as MessageSegment[] | undefined,
                  timestamp: data.timestamp || Date.now() / 1000,
                  sender: data.sender,
                }
                return {
                  ...tab,
                  messages: [...filteredMessages, newMessage]
                }
              }))
              break
            }

            case 'typing':
              updateTab(tabId, { isTyping: data.is_typing || false })
              break

            case 'error':
              // 移除"思考中"占位消息，显示错误
              setTabs(prev => prev.map(tab => {
                if (tab.id !== tabId) return tab
                const filteredMessages = tab.messages.filter(msg => msg.type !== 'thinking')
                return {
                  ...tab,
                  messages: [...filteredMessages, {
                    id: generateMessageId('error'),
                    type: 'error' as const,
                    content: data.content || '发生错误',
                    timestamp: data.timestamp || Date.now() / 1000,
                  }]
                }
              }))
              toast({
                title: '错误',
                description: data.content,
                variant: 'destructive',
              })
              break

            case 'pong':
              break

            case 'history': {
              // 处理服务端发送的历史消息
              const historyMessages = data.messages || []
              if (historyMessages.length > 0) {
                const processedSet = processedMessagesMapRef.current.get(tabId) || new Set()
                const formattedMessages: ChatMessage[] = historyMessages.map((msg: {
                  id?: string
                  content: string
                  timestamp: number
                  sender_name?: string
                  sender_id?: string
                  is_bot?: boolean
                }) => {
                  const isBot = msg.is_bot || false
                  const msgId = msg.id || generateMessageId(isBot ? 'bot' : 'user')
                  // 添加到去重集合
                  const contentHash = `${isBot ? 'bot' : 'user'}-${msg.content}-${Math.floor(msg.timestamp * 1000)}`
                  processedSet.add(contentHash)
                  return {
                    id: msgId,
                    type: isBot ? 'bot' : 'user' as const,
                    content: msg.content,
                    timestamp: msg.timestamp,
                    sender: {
                      name: msg.sender_name || (isBot ? '麦麦' : '用户'),
                      user_id: msg.sender_id,
                      is_bot: isBot,
                    },
                  }
                })
                processedMessagesMapRef.current.set(tabId, processedSet)
                // 替换当前标签页的所有消息
                updateTab(tabId, { messages: formattedMessages })
                console.log(`[Tab ${tabId}] 已加载 ${formattedMessages.length} 条历史消息`)
              }
              break
            }

            default:
              console.log('未知消息类型:', data.type)
          }
        } catch (e) {
          console.error('解析消息失败:', e)
        }
      }

      ws.onclose = () => {
        updateTab(tabId, { isConnected: false })
        setIsConnecting(false)
        wsMapRef.current.delete(tabId)
        console.log(`[Tab ${tabId}] WebSocket 已断开`)

        // 清除旧的重连定时器
        const oldTimeout = reconnectTimeoutMapRef.current.get(tabId)
        if (oldTimeout) {
          clearTimeout(oldTimeout)
        }
        
        // 5秒后尝试重连
        const timeout = window.setTimeout(() => {
          if (!isUnmountedRef.current) {
            const tab = tabs.find(t => t.id === tabId)
            if (tab) {
              connectWebSocketForTab(tabId, tab.type, tab.virtualConfig)
            }
          }
        }, 5000)
        reconnectTimeoutMapRef.current.set(tabId, timeout)
      }

      ws.onerror = (error) => {
        console.error(`[Tab ${tabId}] WebSocket 错误:`, error)
        setIsConnecting(false)
      }
    } catch (e) {
      console.error(`[Tab ${tabId}] 创建 WebSocket 失败:`, e)
      setIsConnecting(false)
    }
  }, [userName, updateTab, addMessageToTab, toast, tabs])

  // 用于追踪组件是否已卸载
  const isUnmountedRef = useRef(false)

  // 初始化连接（默认 WebUI 标签页）
  useEffect(() => {
    isUnmountedRef.current = false
    
    // 保存 ref 的当前值，用于清理
    const wsMap = wsMapRef.current
    const reconnectTimeoutMap = reconnectTimeoutMapRef.current
    const processedMessagesMap = processedMessagesMapRef.current
    
    // 加载默认标签页历史消息
    loadChatHistoryForTab('webui-default')
    
    // 延迟连接
    const connectTimer = setTimeout(() => {
      if (!isUnmountedRef.current) {
        connectWebSocketForTab('webui-default', 'webui')
        
        // 恢复的虚拟标签页也需要建立连接
        tabs.forEach(tab => {
          if (tab.type === 'virtual' && tab.virtualConfig) {
            // 初始化去重缓存
            processedMessagesMap.set(tab.id, new Set())
            // 建立 WebSocket 连接
            setTimeout(() => {
              if (!isUnmountedRef.current) {
                connectWebSocketForTab(tab.id, 'virtual', tab.virtualConfig)
              }
            }, 200)
          }
        })
      }
    }, 100)

    // 心跳定时器 - 向所有活动连接发送
    const heartbeat = setInterval(() => {
      wsMap.forEach((ws) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      })
    }, 30000)

    return () => {
      isUnmountedRef.current = true
      clearTimeout(connectTimer)
      clearInterval(heartbeat)
      
      // 清理所有重连定时器
      reconnectTimeoutMap.forEach((timeout) => {
        clearTimeout(timeout)
      })
      reconnectTimeoutMap.clear()
      
      // 关闭所有 WebSocket 连接
      wsMap.forEach((ws) => {
        ws.close()
      })
      wsMap.clear()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 发送消息到当前活动标签页
  const sendMessage = useCallback(() => {
    const ws = wsMapRef.current.get(activeTabId)
    if (!inputValue.trim() || !ws || ws.readyState !== WebSocket.OPEN) {
      return
    }

    const displayName = activeTab?.type === 'virtual' 
      ? activeTab.virtualConfig?.userName || userName
      : userName

    const messageContent = inputValue.trim()
    const currentTimestamp = Date.now() / 1000

    ws.send(JSON.stringify({
      type: 'message',
      content: messageContent,
      user_name: displayName,
    }))

    // 添加到去重缓存，防止服务器广播回来的消息重复显示
    const processedSet = processedMessagesMapRef.current.get(activeTabId) || new Set()
    const contentHash = `user-${messageContent}-${Math.floor(currentTimestamp * 1000)}`
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
      timestamp: currentTimestamp,
      sender: {
        name: displayName,
        is_bot: false,
      }
    }
    addMessageToTab(activeTabId, userMessage)

    // 再添加"思考中"占位消息
    const thinkingMessage: ChatMessage = {
      id: generateMessageId('thinking'),
      type: 'thinking',
      content: '',
      timestamp: currentTimestamp + 0.001, // 稍微晚一点确保顺序
      sender: {
        name: activeTab?.sessionInfo.bot_name || '麦麦',
        is_bot: true,
      }
    }
    addMessageToTab(activeTabId, thinkingMessage)

    setInputValue('')
  }, [inputValue, userName, activeTabId, activeTab, addMessageToTab])

  // 处理键盘事件
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  // 处理昵称编辑
  const startEditingName = () => {
    setTempUserName(userName)
    setIsEditingName(true)
  }

  const saveEditedName = () => {
    const newName = tempUserName.trim() || 'WebUI用户'
    setUserName(newName)
    saveUserName(newName)
    setIsEditingName(false)
    // 通知当前标签页的后端昵称变更
    const ws = wsMapRef.current.get(activeTabId)
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'update_nickname',
        user_name: newName
      }))
    }
  }

  const cancelEditingName = () => {
    setTempUserName('')
    setIsEditingName(false)
  }

  // 格式化时间
  const formatTime = (timestamp: number) => {
    const date = new Date(timestamp * 1000)
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  // 重新连接当前标签页
  const handleReconnect = () => {
    const ws = wsMapRef.current.get(activeTabId)
    if (ws) {
      ws.close()
      wsMapRef.current.delete(activeTabId)
    }
    connectWebSocketForTab(activeTabId, activeTab?.type || 'webui', activeTab?.virtualConfig)
  }

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
        title: '配置不完整',
        description: '请选择平台和用户',
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
    
    setTabs(prev => {
      const newTabs = [...prev, newTab]
      // 保存虚拟标签页到 localStorage
      const virtualTabsToSave: SavedVirtualTab[] = newTabs
        .filter(t => t.type === 'virtual' && t.virtualConfig)
        .map(t => ({
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
    
    // 连接 WebSocket
    setTimeout(() => {
      connectWebSocketForTab(newTabId, 'virtual', tempVirtualConfig)
    }, 100)
    
    toast({
      title: '虚拟身份标签页',
      description: `已创建 ${tabLabel} 的对话`,
    })
  }

  // 关闭标签页
  const closeTab = (tabId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    
    // 不能关闭默认 WebUI 标签页
    if (tabId === 'webui-default') {
      return
    }
    
    // 关闭 WebSocket 连接
    const ws = wsMapRef.current.get(tabId)
    if (ws) {
      ws.close()
      wsMapRef.current.delete(tabId)
    }
    
    // 清理重连定时器
    const timeout = reconnectTimeoutMapRef.current.get(tabId)
    if (timeout) {
      clearTimeout(timeout)
      reconnectTimeoutMapRef.current.delete(tabId)
    }
    
    // 清理去重缓存
    processedMessagesMapRef.current.delete(tabId)
    
    // 移除标签页并更新存储
    setTabs(prev => {
      const newTabs = prev.filter(t => t.id !== tabId)
      // 更新 localStorage 中的虚拟标签页
      const virtualTabsToSave: SavedVirtualTab[] = newTabs
        .filter(t => t.type === 'virtual' && t.virtualConfig)
        .map(t => ({
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
    setTempVirtualConfig(prev => ({
      ...prev,
      personId: person.person_id,
      userId: person.user_id,
      userName: person.nickname || person.person_name,
    }))
  }

  return (
    <div className="h-full flex flex-col">
      {/* 虚拟身份配置对话框 */}
      <Dialog open={showVirtualConfig} onOpenChange={setShowVirtualConfig}>
        <DialogContent className="sm:max-w-[500px] max-h-[85vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <UserCircle2 className="h-5 w-5" />
              新建虚拟身份对话
            </DialogTitle>
            <DialogDescription>
              选择一个麦麦已认识的用户，以该用户的身份与麦麦对话。麦麦将使用她对该用户的记忆和认知来回应。
            </DialogDescription>
          </DialogHeader>
          
          <div className="space-y-4 flex-1 overflow-hidden flex flex-col">
            {/* 平台选择 */}
            <div className="space-y-2">
              <Label className="flex items-center gap-2">
                <Globe className="h-4 w-4" />
                选择平台
              </Label>
              <Select
                value={tempVirtualConfig.platform}
                onValueChange={(value) => {
                  setTempVirtualConfig(prev => ({
                    ...prev,
                    platform: value,
                    personId: '',
                    userId: '',
                    userName: '',
                  }))
                  setPersons([])
                }}
              >
                <SelectTrigger disabled={isLoadingPlatforms}>
                  <SelectValue placeholder={isLoadingPlatforms ? "加载中..." : "选择平台"} />
                </SelectTrigger>
                <SelectContent>
                  {platforms.map((p) => (
                    <SelectItem key={p.platform} value={p.platform}>
                      {p.platform} ({p.count} 人)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* 用户搜索和选择 */}
            {tempVirtualConfig.platform && (
              <div className="space-y-2 flex-1 overflow-hidden flex flex-col">
                <Label className="flex items-center gap-2">
                  <Users className="h-4 w-4" />
                  选择用户
                </Label>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="搜索用户名..."
                    value={personSearchQuery}
                    onChange={(e) => setPersonSearchQuery(e.target.value)}
                    className="pl-9"
                  />
                </div>
                <ScrollArea className="h-[250px] border rounded-md">
                  <div className="p-2">
                    {isLoadingPersons ? (
                      <div className="flex items-center justify-center py-8">
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                      </div>
                    ) : persons.length === 0 ? (
                      <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                        <Users className="h-8 w-8 mb-2 opacity-50" />
                        <p className="text-sm">没有找到用户</p>
                      </div>
                    ) : (
                      <div className="space-y-1">
                        {persons.map((person) => (
                          <button
                            key={person.person_id}
                            onClick={() => selectPerson(person)}
                            className={cn(
                              "w-full flex items-center gap-3 p-2 rounded-md text-left transition-colors",
                              tempVirtualConfig.personId === person.person_id
                                ? "bg-primary text-primary-foreground"
                                : "hover:bg-muted"
                            )}
                          >
                            <Avatar className="h-8 w-8 shrink-0">
                              <AvatarFallback className={cn(
                                "text-xs",
                                tempVirtualConfig.personId === person.person_id
                                  ? "bg-primary-foreground/20"
                                  : "bg-muted"
                              )}>
                                {(person.nickname || person.person_name || '?').charAt(0)}
                              </AvatarFallback>
                            </Avatar>
                            <div className="min-w-0 flex-1">
                              <div className="font-medium truncate">
                                {person.nickname || person.person_name}
                              </div>
                              <div className={cn(
                                "text-xs truncate",
                                tempVirtualConfig.personId === person.person_id
                                  ? "text-primary-foreground/70"
                                  : "text-muted-foreground"
                              )}>
                                ID: {person.user_id}
                                {person.is_known && " · 已认识"}
                              </div>
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </ScrollArea>
              </div>
            )}

            {/* 虚拟群名配置 */}
            {tempVirtualConfig.personId && (
              <div className="space-y-2">
                <Label>虚拟群名（可选）</Label>
                <Input
                  placeholder="WebUI虚拟群聊"
                  value={tempVirtualConfig.groupName}
                  onChange={(e) => setTempVirtualConfig(prev => ({
                    ...prev,
                    groupName: e.target.value
                  }))}
                />
                <p className="text-xs text-muted-foreground">
                  麦麦会认为这是一个名为此名称的群聊
                </p>
              </div>
            )}
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setShowVirtualConfig(false)}>
              取消
            </Button>
            <Button 
              onClick={createVirtualTab}
              disabled={!tempVirtualConfig.platform || !tempVirtualConfig.personId}
            >
              创建对话
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 标签页栏 */}
      <div className="shrink-0 border-b bg-muted/30">
        <div className="max-w-4xl mx-auto px-2 sm:px-4">
          <div className="flex items-center gap-1 overflow-x-auto py-1.5 scrollbar-thin">
            {tabs.map((tab) => (
              <div
                key={tab.id}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm whitespace-nowrap transition-colors cursor-pointer",
                  "hover:bg-muted",
                  activeTabId === tab.id
                    ? "bg-background shadow-sm border"
                    : "text-muted-foreground"
                )}
                onClick={() => switchTab(tab.id)}
              >
                {tab.type === 'webui' ? (
                  <MessageSquare className="h-3.5 w-3.5" />
                ) : (
                  <UserCircle2 className="h-3.5 w-3.5" />
                )}
                <span className="max-w-[100px] truncate">{tab.label}</span>
                {/* 连接状态指示器 */}
                <span className={cn(
                  "w-1.5 h-1.5 rounded-full",
                  tab.isConnected ? "bg-green-500" : "bg-muted-foreground/50"
                )} />
                {/* 关闭按钮（非默认标签页） */}
                {tab.id !== 'webui-default' && (
                  <span
                    onClick={(e) => closeTab(tab.id, e)}
                    className="ml-0.5 p-0.5 rounded hover:bg-muted-foreground/20 cursor-pointer"
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        closeTab(tab.id, e as any)
                      }
                    }}
                  >
                    <X className="h-3 w-3" />
                  </span>
                )}
              </div>
            ))}
            {/* 新建虚拟身份标签页按钮 */}
            <button
              onClick={openVirtualConfig}
              className="flex items-center gap-1 px-2 py-1.5 rounded-md text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
              title="新建虚拟身份对话"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* 头部信息栏 */}
      <div className="shrink-0 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="p-3 sm:p-4 max-w-4xl mx-auto">
          {/* 标题行 */}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 sm:gap-3 min-w-0">
              <Avatar className="h-8 w-8 sm:h-10 sm:w-10 shrink-0">
                <AvatarFallback className="bg-primary/10 text-primary">
                  <Bot className="h-4 w-4 sm:h-5 sm:w-5" />
                </AvatarFallback>
              </Avatar>
              <div className="min-w-0">
                <h1 className="text-base sm:text-lg font-semibold truncate">
                  {activeTab?.sessionInfo.bot_name || '麦麦'}
                </h1>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  {activeTab?.isConnected ? (
                    <>
                      <Wifi className="h-3 w-3 text-green-500" />
                      <span className="text-green-600 dark:text-green-400">已连接</span>
                    </>
                  ) : isConnecting ? (
                    <>
                      <Loader2 className="h-3 w-3 animate-spin" />
                      <span>连接中...</span>
                    </>
                  ) : (
                    <>
                      <WifiOff className="h-3 w-3 text-red-500" />
                      <span className="text-red-600 dark:text-red-400">未连接</span>
                    </>
                  )}
                </div>
              </div>
            </div>
            
            {/* 右侧操作按钮 */}
            <div className="flex items-center gap-1 shrink-0">
              {isLoadingHistory && (
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              )}
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={handleReconnect}
                disabled={isConnecting}
                title="重新连接"
              >
                <RefreshCw className={cn('h-4 w-4', isConnecting && 'animate-spin')} />
              </Button>
            </div>
          </div>
          
          {/* 用户身份（桌面端显示更多信息） */}
          <div className="hidden sm:flex items-center gap-2 mt-2 text-sm text-muted-foreground">
            {activeTab?.type === 'virtual' && activeTab.virtualConfig ? (
              <>
                <UserCircle2 className="h-3 w-3 text-primary" />
                <span>虚拟身份：</span>
                <span className="font-medium text-primary">{activeTab.virtualConfig.userName}</span>
                <span className="text-xs">({activeTab.virtualConfig.platform})</span>
                {activeTab.virtualConfig.groupName && (
                  <>
                    <span className="mx-1">·</span>
                    <span className="text-xs">群：{activeTab.virtualConfig.groupName}</span>
                  </>
                )}
              </>
            ) : (
              <>
                <User className="h-3 w-3" />
                <span>当前身份：</span>
                {isEditingName ? (
                  <div className="flex items-center gap-2">
                    <Input
                      value={tempUserName}
                      onChange={(e) => setTempUserName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') saveEditedName()
                        if (e.key === 'Escape') cancelEditingName()
                      }}
                      className="h-7 w-32"
                      placeholder="输入昵称"
                      autoFocus
                    />
                    <Button size="sm" variant="ghost" className="h-7 px-2" onClick={saveEditedName}>
                      保存
                    </Button>
                    <Button size="sm" variant="ghost" className="h-7 px-2" onClick={cancelEditingName}>
                      取消
                    </Button>
                  </div>
                ) : (
                  <div className="flex items-center gap-1">
                    <span className="font-medium text-foreground">{userName}</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 w-6 p-0"
                      onClick={startEditingName}
                      title="修改昵称"
                    >
                      <Edit2 className="h-3 w-3" />
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* 消息列表区域 */}
      <div className="flex-1 overflow-hidden">
        <ScrollArea className="h-full">
          <div className="p-3 sm:p-4 max-w-4xl mx-auto space-y-3 sm:space-y-4">
            {activeTab?.messages.length === 0 && !isLoadingHistory && (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Bot className="h-12 w-12 mb-4 opacity-50" />
                <p className="text-sm">开始与 {activeTab?.sessionInfo.bot_name || '麦麦'} 对话吧！</p>
              </div>
            )}
            
            {activeTab?.messages.map((message) => (
              <div
                key={message.id}
                className={cn(
                  'flex gap-2 sm:gap-3',
                  message.type === 'user' && 'flex-row-reverse',
                  message.type === 'system' && 'justify-center',
                  message.type === 'error' && 'justify-center'
                )}
              >
                {/* 系统消息 */}
                {message.type === 'system' && (
                  <div className="text-xs text-muted-foreground bg-muted/50 px-3 py-1 rounded-full max-w-[90%]">
                    {message.content}
                  </div>
                )}

                {/* 错误消息 */}
                {message.type === 'error' && (
                  <div className="text-xs text-red-600 dark:text-red-400 bg-red-100 dark:bg-red-900/30 px-3 py-1 rounded-full max-w-[90%]">
                    {message.content}
                  </div>
                )}

                {/* 思考中占位消息 */}
                {message.type === 'thinking' && (
                  <>
                    <Avatar className="h-7 w-7 sm:h-8 sm:w-8 shrink-0">
                      <AvatarFallback className="bg-primary/10 text-primary">
                        <Bot className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                      </AvatarFallback>
                    </Avatar>
                    <div className="flex flex-col gap-1 max-w-[75%] sm:max-w-[70%]">
                      <div className="flex items-center gap-2 text-[10px] sm:text-xs text-muted-foreground">
                        <span className="hidden sm:inline">{message.sender?.name || activeTab?.sessionInfo.bot_name}</span>
                      </div>
                      <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="flex gap-1">
                            <span className="w-2 h-2 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                            <span className="w-2 h-2 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                            <span className="w-2 h-2 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                          </div>
                          <span className="text-xs text-muted-foreground ml-1">思考中...</span>
                        </div>
                      </div>
                    </div>
                  </>
                )}

                {/* 用户/机器人消息 */}
                {(message.type === 'user' || message.type === 'bot') && (
                  <>
                    <Avatar className="h-7 w-7 sm:h-8 sm:w-8 shrink-0">
                      <AvatarFallback
                        className={cn(
                          'text-xs',
                          message.type === 'bot'
                            ? 'bg-primary/10 text-primary'
                            : 'bg-secondary text-secondary-foreground'
                        )}
                      >
                        {message.type === 'bot' ? (
                          <Bot className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                        ) : (
                          <User className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                        )}
                      </AvatarFallback>
                    </Avatar>
                    <div
                      className={cn(
                        'flex flex-col gap-1 max-w-[75%] sm:max-w-[70%]',
                        message.type === 'user' && 'items-end'
                      )}
                    >
                      <div className="flex items-center gap-2 text-[10px] sm:text-xs text-muted-foreground">
                        <span className="hidden sm:inline">{message.sender?.name || (message.type === 'bot' ? activeTab?.sessionInfo.bot_name : userName)}</span>
                        <span>{formatTime(message.timestamp)}</span>
                      </div>
                      <div
                        className={cn(
                          'rounded-2xl px-3 py-2 text-sm break-words',
                          message.type === 'bot'
                            ? 'bg-muted rounded-tl-sm'
                            : 'bg-primary text-primary-foreground rounded-tr-sm'
                        )}
                      >
                        <RenderMessageContent message={message} isBot={message.type === 'bot'} />
                      </div>
                    </div>
                  </>
                )}
              </div>
            ))}

            <div ref={messagesEndRef} />
          </div>
        </ScrollArea>
      </div>

      {/* 输入区域 - 固定在底部 */}
      <div className="shrink-0 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="p-3 sm:p-4 max-w-4xl mx-auto">
          <div className="flex gap-2">
            <Input
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={activeTab?.isConnected ? '输入消息...' : '等待连接...'}
              disabled={!activeTab?.isConnected}
              className="flex-1 h-10 sm:h-10"
            />
            <Button
              onClick={sendMessage}
              disabled={!activeTab?.isConnected || !inputValue.trim()}
              size="icon"
              className="h-10 w-10 shrink-0"
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
