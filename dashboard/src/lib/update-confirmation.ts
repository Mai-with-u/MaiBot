import { ApiError } from '@/lib/http'

export interface UpdateConflict {
  message: string
  files: string[]
  diverged: boolean
  target_commit?: string
}

export type UpdateConfirmation = UpdateConflict

let current: UpdateConfirmation | null = null
let finish: ((accepted: boolean) => void) | null = null
const listeners = new Set<() => void>()

export const getUpdateConfirmation = () => current
export function subscribeUpdateConfirmation(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function resolveUpdateConfirmation(accepted: boolean) {
  finish?.(accepted)
}

export function confirmCodeUpdate(request: UpdateConfirmation, signal?: AbortSignal): Promise<boolean> {
  if (signal?.aborted || current) return Promise.resolve(false)
  return new Promise((resolve) => {
    const abort = () => complete(false)
    const complete = (accepted: boolean) => {
      signal?.removeEventListener('abort', abort)
      current = null
      finish = null
      listeners.forEach((listener) => listener())
      resolve(accepted)
    }
    finish = complete
    current = request
    signal?.addEventListener('abort', abort, { once: true })
    listeners.forEach((listener) => listener())
  })
}

export function getUpdateConflict(error: unknown): UpdateConflict | null {
  if (!(error instanceof ApiError) || error.status !== 409 || typeof error.detail !== 'object' || !error.detail) return null
  const envelope = error.detail as { detail?: unknown }
  if (typeof envelope.detail !== 'object' || !envelope.detail) return null
  const detail = envelope.detail as Record<string, unknown>
  if (detail.code !== 'local_changes' || typeof detail.message !== 'string' || !Array.isArray(detail.files)
    || !detail.files.every((file) => typeof file === 'string') || typeof detail.diverged !== 'boolean') return null
  if (typeof detail.target_commit !== 'string') return null
  return { message: detail.message, files: detail.files as string[], diverged: detail.diverged, target_commit: detail.target_commit }
}
