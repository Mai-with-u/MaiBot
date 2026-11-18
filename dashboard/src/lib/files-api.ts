import { backendApi } from '@/lib/http'

const root = '/api/webui/files'
export interface FileChat { session_id: string; name: string }
export interface StoredFile {
  file_ref: string; name: string; size: number; mime_type: string; state: string; origin: string
  error: string; created_at: number; chats?: FileChat[]
}
export interface FileList {
  items: StoredFile[]
  usage: { used_bytes: number; reserved_bytes: number; max_bytes: number; max_file_bytes: number }
}
export interface FileText { text: string; offset: number; next_offset: number; total_characters: number }
export const listFiles = (session_id?: string, offset = 0) => backendApi.get<FileList>(root, { query: { session_id, offset } })
export const listFileChats = () => backendApi.get<FileChat[]>(`${root}/chats`)
export const uploadFile = (file: File, session_id: string, chat = false) => backendApi.post<StoredFile>(
  `${root}/${chat ? 'chat-uploads' : 'uploads'}`, { query: { session_id, name: file.name }, body: file,
    headers: { 'Content-Type': file.type || 'application/octet-stream' } })
export const readFileText = (ref: string, offset = 0, encoding = 'utf-8-sig') => backendApi.get<FileText>(
  `${root}/${ref}/text`, { query: { offset, encoding } })
export const removeFile = (ref: string, cache_only: boolean) => backendApi.delete(`${root}/${ref}`, { query: { cache_only } })
export const sendFile = (ref: string, session_id: string) => backendApi.post<{ status: string }>(`${root}/${ref}/send`, { body: { session_id } })
export async function downloadFile(file: Pick<StoredFile, 'file_ref' | 'name'>) {
  const result = await backendApi.post<{ url: string }>(`${root}/${file.file_ref}/download`)
  const blob = await backendApi.get<Blob>(result.url, { parse: 'blob' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = file.name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}
