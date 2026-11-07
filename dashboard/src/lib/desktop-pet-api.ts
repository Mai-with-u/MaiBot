import { backendApi } from '@/lib/http'

const root = '/api/webui/desktop-pet'

export interface DesktopPetTodo {
  id: string
  text: string
  due_at: number | null
}

/** 首页桌宠卡片用的状态：桌宠服务的 status() 加上未完成待办。 */
export interface DesktopPetStatus {
  enabled: boolean
  supported: boolean
  running: boolean
  mode: 'local' | 'remote'
  ready: boolean
  visible: boolean
  error: string
  skin_id: string
  thinking: boolean
  /** 最近一次显示的气泡文字，没有时为空 */
  bubble: string
  todos: { pending: number; items: DesktopPetTodo[] }
}

export function getDesktopPetStatus(signal?: AbortSignal) {
  return backendApi.get<DesktopPetStatus>(`${root}/status`, { signal })
}

export function setDesktopPetVisible(visible: boolean) {
  return backendApi.post<DesktopPetStatus>(`${root}/visibility`, { body: { visible } })
}

export function sendDesktopPetMessage(text: string) {
  return backendApi.post<{ message_id: string }>(`${root}/chat`, { body: { text } })
}

export interface DesktopPetSkinFrame {
  file: string
  rect: [number, number, number, number]
  duration_ms: number
}

export interface DesktopPetSkin {
  id: string
  name: string
  animations: Record<
    string,
    { sequence?: DesktopPetSkinFrame[]; duration_ms?: number; motion?: string }
  >
  /** 标准状态（idle / thinking / talking / dragging）对应的动画名 */
  states?: Record<string, string>
  resources?: Record<string, string>
}

export function getDesktopPetSkin(skinId: string, signal?: AbortSignal) {
  return backendApi.get<DesktopPetSkin>(`${root}/skins/${encodeURIComponent(skinId)}`, { signal })
}

/** 卡片在首页的显示状态，由服务状态推出来 */
export type DesktopPetCardState = 'idle' | 'thinking' | 'hidden' | 'starting' | 'waiting' | 'error'

export function desktopPetCardState(status: DesktopPetStatus): DesktopPetCardState {
  if (status.error || !status.supported) return 'error'
  if (status.mode === 'remote' && !status.running) return 'waiting'
  if (!status.ready) return 'starting'
  if (status.thinking) return 'thinking'
  return status.visible ? 'idle' : 'hidden'
}
