import { resolveApiPath } from '@/lib/api-base'
import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/user-emojis'

export interface UserEmojiItem {
  id: string
  content_type: string
  content_url: string
  created_at: number
}

interface UserEmojiListResponse {
  items: UserEmojiItem[]
  limit: number
}

interface UserEmojiCreateResponse {
  item: UserEmojiItem
}

export interface UserEmojiPayload {
  name: string
  mime_type: string
  base64: string
  data_url: string
}

export async function listUserEmojis(userId: string): Promise<UserEmojiListResponse> {
  return backendApi.get<UserEmojiListResponse>(API_BASE, {
    query: { user_id: userId },
    errorMessage: '获取用户表情包失败',
  })
}

export async function addUserEmoji(userId: string, file: File): Promise<UserEmojiItem> {
  const formData = new FormData()
  formData.append('user_id', userId)
  formData.append('file', file)
  const response = await backendApi.post<UserEmojiCreateResponse>(API_BASE, {
    body: formData,
    errorMessage: '添加用户表情包失败',
  })
  return response.item
}

export async function deleteUserEmoji(userId: string, emojiId: string): Promise<void> {
  await backendApi.delete(`${API_BASE}/${encodeURIComponent(emojiId)}`, {
    query: { user_id: userId },
    errorMessage: '删除用户表情包失败',
  })
}

export async function resolveUserEmojiUrl(item: UserEmojiItem): Promise<string> {
  return resolveApiPath(item.content_url)
}

export async function loadUserEmojiPayload(item: UserEmojiItem): Promise<UserEmojiPayload> {
  const blob = await backendApi.get<Blob>(item.content_url, {
    parse: 'blob',
    cache: 'force-cache',
    errorMessage: '读取用户表情包失败',
  })
  const dataUrl = await blobToDataUrl(blob)
  const base64 = dataUrl.split(',', 2)[1]
  if (!base64) {
    throw new Error('用户表情包内容无效')
  }
  return {
    name: `${item.id}.${extensionFromContentType(blob.type || item.content_type)}`,
    mime_type: blob.type || item.content_type,
    base64,
    data_url: dataUrl,
  }
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('读取用户表情包失败'))
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        reject(new Error('用户表情包内容无效'))
        return
      }
      resolve(reader.result)
    }
    reader.readAsDataURL(blob)
  })
}

function extensionFromContentType(contentType: string): string {
  const extensionMap: Record<string, string> = {
    'image/gif': 'gif',
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
  }
  return extensionMap[contentType] || 'png'
}
