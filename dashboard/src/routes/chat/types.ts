// 虚拟标签页持久化存储 key
export const VIRTUAL_TABS_STORAGE_KEY = 'maibot_webui_virtual_tabs'

// 保存的虚拟标签页配置
export interface SavedVirtualTab {
  id: string
  label: string
  virtualConfig: VirtualIdentityConfig
  createdAt: number
}

// 平台信息类型
export interface PlatformInfo {
  platform: string
  count: number
}

// 用户信息类型（从后端获取的人物信息）
export interface PersonInfo {
  person_id: string
  user_id: string
  person_name: string
  nickname: string | null
  platform: string
  is_known: boolean
}

// 虚拟身份配置
export interface VirtualIdentityConfig {
  platform: string
  personId: string
  userId: string
  userName: string
  groupName: string
  groupId: string // 虚拟群 ID，用于持久化历史记录
}

export interface ChatRuntimeStatus {
  kind: 'thinking' | 'typing' | 'acting' | 'error'
  stage?: string
  detail?: string
  retry?: {
    attempt: number
    maxAttempts: number
  }
  updatedAt: number
}

// 聊天标签页
export interface ChatTab {
  id: string
  type: 'webui' | 'virtual'
  label: string
  virtualConfig?: VirtualIdentityConfig
  messages: ChatMessage[]
  isConnected: boolean
  isTyping: boolean
  runtimeStatus?: ChatRuntimeStatus | null
  sessionInfo: {
    session_id?: string
    user_id?: string
    user_name?: string
    bot_name?: string
    bot_qq?: string
    group_id?: string
    platform?: string
    virtual_mode?: boolean
  }
}

// 消息段类型
export interface MessageSegment {
  type:
    | 'text'
    | 'image'
    | 'emoji'
    | 'face'
    | 'voice'
    | 'video'
    | 'music'
    | 'file'
    | 'reply'
    | 'at'
    | 'forward'
    | 'unknown'
  data: string | number | object
  original_type?: string
}

// @某人 消息段的负载
export interface AtSegmentData {
  target_user_id?: string | null
  target_user_nickname?: string | null
  target_user_cardname?: string | null
}

// 回复消息段的负载
export interface ReplySegmentData {
  target_message_id?: string | null
  target_message_content?: string | null
  target_message_sender_id?: string | null
  target_message_sender_nickname?: string | null
  target_message_sender_cardname?: string | null
}

// 消息类型
export interface ChatMessage {
  id: string
  type: 'user' | 'bot' | 'system' | 'error'
  content: string
  timestamp: number
  message_type?: 'text' | 'rich' // 消息格式类型
  segments?: MessageSegment[] // 富文本消息段
  sender?: {
    name: string
    user_id?: string
    is_bot?: boolean
  }
}

// WebSocket 消息类型
export interface ChatImageAttachment {
  id: string
  name: string
  mime_type: string
  base64: string
  data_url: string
}

export interface ChatIncomingImage {
  name?: string
  mime_type?: string
  mimeType?: string
  base64?: string
  data_url?: string
  dataUrl?: string
}

export interface WsMessage {
  type: string
  content?: string
  message_id?: string
  timestamp?: number
  is_typing?: boolean
  session_id?: string
  user_id?: string
  user_name?: string
  bot_name?: string
  bot_qq?: string
  platform?: string
  virtual_mode?: boolean
  sender?: {
    name: string
    user_id?: string
    is_bot?: boolean
  }
  images?: ChatIncomingImage[]
  // 历史消息列表（用于 type: 'history'）
  messages?: Array<{
    id?: string
    content: string
    timestamp: number
    sender_name?: string
    sender_id?: string
    is_bot?: boolean
    message_type?: string
    segments?: MessageSegment[] | null
  }>
  group_id?: string
  // 富文本消息
  message_type?: string
  segments?: MessageSegment[]
}
