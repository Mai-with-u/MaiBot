import { useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'

import { type AuthStatus, getAuthStatus } from '@/lib/auth'
import { authApi } from '@/lib/http'

const AUTH_STATUS_CACHE_MS = 30_000
let cachedAuthStatus: (AuthStatus & { checkedAt: number }) | null = null
let authStatusPromise: Promise<AuthStatus> | null = null

function readCachedAuthStatus(): AuthStatus | undefined {
  if (!cachedAuthStatus) {
    return undefined
  }
  if (Date.now() - cachedAuthStatus.checkedAt > AUTH_STATUS_CACHE_MS) {
    cachedAuthStatus = null
    return undefined
  }
  return cachedAuthStatus
}

async function resolveEntryRedirect(): Promise<'auth' | 'setup' | null> {
  authStatusPromise ??= getAuthStatus().then((status) => {
    cachedAuthStatus = { ...status, checkedAt: Date.now() }
    return status
  }).finally(() => {
    authStatusPromise = null
  })

  const status = await authStatusPromise
  if (!status.authenticated) {
    return 'auth'
  }
  if (status.requires_custom_token) {
    return 'setup'
  }
  return null
}

export function useAuthGuard() {
  const navigate = useNavigate()
  const [checking, setChecking] = useState(() => {
    const cached = readCachedAuthStatus()
    return cached?.authenticated !== true || cached.requires_custom_token === true
  })

  useEffect(() => {
    let cancelled = false
    const cached = readCachedAuthStatus()
    if (cached?.authenticated === true && cached.requires_custom_token !== true) {
      setChecking(false)
      return () => {
        cancelled = true
      }
    }
    
    const verifyAuth = async () => {
      try {
        const redirectTarget = await resolveEntryRedirect()
        if (cancelled) {
          return
        }
        if (redirectTarget === 'auth') {
          navigate({ to: '/auth' })
        } else if (redirectTarget === 'setup') {
          navigate({ to: '/setup' })
        }
      } catch {
        // 发生错误时也跳转到登录页
        if (!cancelled) {
          navigate({ to: '/auth' })
        }
      } finally {
        if (!cancelled) {
          setChecking(false)
        }
      }
    }
    
    verifyAuth()
    
    return () => {
      cancelled = true
    }
  }, [navigate])
  
  return { checking }
}

/**
 * 检查是否已认证（异步）
 */
export async function checkAuth(): Promise<boolean> {
  return (await getAuthStatus()).authenticated
}

/**
 * 检查是否需要首次配置
 */
export async function checkFirstSetup(): Promise<boolean> {
  try {
    const data = await authApi.get<{ is_first_setup: boolean }>('/api/webui/setup/status')
    return data.is_first_setup
  } catch (error) {
    console.error('检查首次配置状态失败:', error)
    return false
  }
}
