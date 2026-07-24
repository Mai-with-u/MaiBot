import { useEffect, useMemo, useState } from 'react'

import { resolveApiPath } from '@/lib/api-base'
import { backendApi } from '@/lib/http'
import { getSetting } from '@/lib/settings-manager'

export type AvatarTargetType = 'user' | 'group'

export function isAvatarFetchEnabled(): boolean {
  return getSetting('enableAvatarFetch')
}

export function useAvatarFetchEnabled(): boolean {
  const [enabled, setEnabled] = useState(() => isAvatarFetchEnabled())

  useEffect(() => {
    const syncEnabled = () => setEnabled(isAvatarFetchEnabled())
    const handleSettingsChange = (event: Event) => {
      const detail = (event as CustomEvent<{ key?: string }>).detail
      if (!detail?.key || detail.key === 'enableAvatarFetch') {
        syncEnabled()
      }
    }

    window.addEventListener('maibot-settings-change', handleSettingsChange)
    window.addEventListener('maibot-settings-reset', syncEnabled)
    window.addEventListener('storage', syncEnabled)
    return () => {
      window.removeEventListener('maibot-settings-change', handleSettingsChange)
      window.removeEventListener('maibot-settings-reset', syncEnabled)
      window.removeEventListener('storage', syncEnabled)
    }
  }, [])

  return enabled
}

export function buildWebuiAvatarPath(
  platform?: string | null,
  targetId?: string | null,
  targetType: AvatarTargetType = 'user',
  version?: string | number | null
): string | null {
  const normalizedPlatform = String(platform || '')
    .trim()
    .toLowerCase()
  const normalizedTargetId = String(targetId || '').trim()
  if (!normalizedPlatform || !normalizedTargetId) return null
  const idParam = targetType === 'group' ? 'group_id' : 'user_id'
  const versionQuery =
    version === undefined || version === null ? '' : `&v=${encodeURIComponent(version)}`
  return `/api/webui/avatar?platform=${encodeURIComponent(normalizedPlatform)}&${idParam}=${encodeURIComponent(normalizedTargetId)}${versionQuery}`
}

export function useResolvedAvatarUrl(
  platform?: string | null,
  targetId?: string | null,
  targetType: AvatarTargetType = 'user',
  version?: string | number | null
): string | undefined {
  const avatarFetchEnabled = useAvatarFetchEnabled()
  const avatarPath = useMemo(
    () => buildWebuiAvatarPath(platform, targetId, targetType, version),
    [platform, targetId, targetType, version]
  )
  const [avatarUrl, setAvatarUrl] = useState<string | undefined>()

  useEffect(() => {
    let ignore = false
    if (!avatarFetchEnabled || !avatarPath) {
      setAvatarUrl(undefined)
      return
    }

    resolveApiPath(avatarPath).then((resolvedPath) => {
      if (!ignore) setAvatarUrl(resolvedPath)
    })

    return () => {
      ignore = true
    }
  }, [avatarFetchEnabled, avatarPath])

  return avatarUrl
}

interface WebuiUserAvatarUploadResponse {
  success: boolean
  avatar_url: string
}

export async function uploadWebuiUserAvatar(
  userId: string,
  file: File
): Promise<WebuiUserAvatarUploadResponse> {
  const formData = new FormData()
  formData.append('user_id', userId)
  formData.append('file', file)
  return backendApi.put<WebuiUserAvatarUploadResponse>('/api/webui/avatar/webui-user', {
    body: formData,
    errorMessage: '保存用户头像失败',
  })
}
