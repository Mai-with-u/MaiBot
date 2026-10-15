import { useEffect, useMemo, useState } from 'react'

import { resolveApiPath } from '@/lib/api-base'
import { backendApi } from '@/lib/http'
import { isElectron } from '@/lib/runtime'
import { getSetting } from '@/lib/settings-manager'

export type AvatarTargetType = 'user' | 'group'

interface AvatarRoute {
  accountId?: string | null
  scope?: string | null
}

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
  version?: string | number | null,
  route?: AvatarRoute
): string | null {
  const normalizedPlatform = String(platform || '')
    .trim()
    .toLowerCase()
  const normalizedTargetId = String(targetId || '').trim()
  if (!normalizedPlatform || !normalizedTargetId) return null
  const idParam = targetType === 'group' ? 'group_id' : 'user_id'
  const versionQuery =
    version === undefined || version === null ? '' : `&v=${encodeURIComponent(version)}`
  const routeQuery =
    (route?.accountId ? `&account_id=${encodeURIComponent(route.accountId)}` : '') +
    (route?.scope ? `&scope=${encodeURIComponent(route.scope)}` : '')
  return `/api/webui/avatar?platform=${encodeURIComponent(normalizedPlatform)}&${idParam}=${encodeURIComponent(normalizedTargetId)}${versionQuery}${routeQuery}`
}

export function useResolvedAvatarUrl(
  platform?: string | null,
  targetId?: string | null,
  targetType: AvatarTargetType = 'user',
  version?: string | number | null,
  route?: AvatarRoute
): string | undefined {
  const avatarFetchEnabled = useAvatarFetchEnabled()
  const accountId = route?.accountId
  const scope = route?.scope
  const avatarPath = useMemo(
    () => buildWebuiAvatarPath(platform, targetId, targetType, version, { accountId, scope }),
    [platform, targetId, targetType, version, accountId, scope]
  )
  const canResolve = Boolean(avatarFetchEnabled && avatarPath)
  // 浏览器里接口与页面同源，路径本身就是地址，首帧即可给出，已缓存的头像不会先闪一下默认头像；
  // 只有 Electron 需要向主进程异步查询后端地址。
  const needsAsyncResolve = canResolve && isElectron()
  const [resolved, setResolved] = useState<{ path: string; url: string }>()

  useEffect(() => {
    if (!needsAsyncResolve || !avatarPath) {
      return
    }

    let ignore = false
    resolveApiPath(avatarPath).then((resolvedPath) => {
      if (!ignore) setResolved({ path: avatarPath, url: resolvedPath })
    })

    return () => {
      ignore = true
    }
  }, [avatarPath, needsAsyncResolve])

  if (!canResolve || !avatarPath) return undefined
  if (!needsAsyncResolve) return avatarPath
  // 目标切换后旧地址不再适用，等新地址解析完成。
  return resolved?.path === avatarPath ? resolved.url : undefined
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
