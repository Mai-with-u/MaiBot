/**
 * 表情包 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface Emoji {
  id: string
  name: string
  url: string
  tags: string[]
  created_at: string
}

export async function getEmojiList(): Promise<{ success: boolean; data: Emoji[] }> {
  const response = await fetchWithAuth('/api/webui/emojis')
  return response.json()
}

export async function getEmojiDetail(id: string): Promise<{ success: boolean; data: Emoji }> {
  const response = await fetchWithAuth(`/api/webui/emojis/${id}`)
  return response.json()
}

export async function uploadEmoji(file: File, name: string, tags: string[]): Promise<{ success: boolean; data?: Emoji; message?: string }> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('name', name)
  formData.append('tags', JSON.stringify(tags))

  const response = await fetchWithAuth('/api/webui/emojis', {
    method: 'POST',
    body: formData,
  })
  return response.json()
}

export async function deleteEmoji(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/emojis/${id}`, {
    method: 'DELETE',
  })
  return response.json()
}

export async function searchEmojis(query: string): Promise<{ success: boolean; data: Emoji[] }> {
  const response = await fetchWithAuth(`/api/webui/emojis/search?q=${encodeURIComponent(query)}`)
  return response.json()
}
