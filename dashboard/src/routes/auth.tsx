import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import {
  AlertCircle,
  FileText,
  HelpCircle,
  Key,
  Lock,
  Moon,
  Sun,
  Terminal,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useTheme } from '@/components/use-theme'

import { checkAuthStatus, getSetupStatus } from '@/lib/auth'
import { authApi } from '@/lib/http'
import { cn } from '@/lib/utils'
import { APP_FULL_NAME } from '@/lib/version'

function createGearPath(teeth: number, outerRadius: number, rootRadius: number) {
  const toothAngle = (Math.PI * 2) / teeth
  const points = Array.from({ length: teeth }, (_, index) => {
    const centerAngle = index * toothAngle - Math.PI / 2
    const rootHalfWidth = toothAngle * 0.5
    const topHalfWidth = toothAngle * 0.22

    return [
      { angle: centerAngle - rootHalfWidth, radius: rootRadius },
      { angle: centerAngle - topHalfWidth, radius: outerRadius },
      { angle: centerAngle + topHalfWidth, radius: outerRadius },
      { angle: centerAngle + rootHalfWidth, radius: rootRadius },
    ]
  }).flat().map(({ angle, radius }) => ({
    x: 50 + Math.cos(angle) * radius,
    y: 50 + Math.sin(angle) * radius,
  }))

  return points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ') + ' Z'
}

const AUTH_GEAR_PATH = createGearPath(28, 48, 38)

function AuthRetroGears() {
  return (
    <div className="auth-retro-gears" aria-hidden="true">
      <svg className="auth-retro-gear auth-retro-gear--primary" viewBox="0 0 100 100" focusable="false">
        <path className="auth-retro-gear__body" d={AUTH_GEAR_PATH} />
        <circle className="auth-retro-gear__ring" cx="50" cy="50" r="31" />
        <circle className="auth-retro-gear__ring auth-retro-gear__ring--inner" cx="50" cy="50" r="13" />
        <circle className="auth-retro-gear__cutout" cx="50" cy="50" r="8" />
      </svg>
      <svg className="auth-retro-gear auth-retro-gear--secondary" viewBox="0 0 100 100" focusable="false">
        <path className="auth-retro-gear__body" d={AUTH_GEAR_PATH} />
        <circle className="auth-retro-gear__ring" cx="50" cy="50" r="29" />
        <circle className="auth-retro-gear__ring auth-retro-gear__ring--inner" cx="50" cy="50" r="12" />
        <circle className="auth-retro-gear__cutout" cx="50" cy="50" r="7" />
      </svg>
    </div>
  )
}

export function AuthPage() {
  const [token, setToken] = useState('')
  const [isValidating, setIsValidating] = useState(false)
  const [error, setError] = useState('')
  const [checkingAuth, setCheckingAuth] = useState(true)
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { theme, setTheme, themeConfig } = useTheme()
  const showRetroGears = themeConfig.dashboardStyle === 'future-retro'
  // 避免 React StrictMode 下重复触发 URL token 自动登录。
  const urlTokenHandledRef = useRef(false)

  // 从 URL 中提取 token（支持 query 与 hash 两种位置）。
  // 允许 ?token=xxx 、&token=xxx（query）以及 #/foo?token=xxx、#token=xxx（hash）。
  const extractUrlToken = useCallback((): string => {
    if (typeof window === 'undefined') return ''
    const fromQuery = new URLSearchParams(window.location.search).get('token')
    if (fromQuery && fromQuery.trim()) return fromQuery.trim()
    // hash 中可能形如 "#token=xxx" 或 "#/path?token=xxx"
    const rawHash = window.location.hash.replace(/^#/, '')
    if (!rawHash) return ''
    const queryIdx = rawHash.indexOf('?')
    const hashQuery = queryIdx >= 0 ? rawHash.slice(queryIdx + 1) : rawHash
    const fromHash = new URLSearchParams(hashQuery).get('token')
    return fromHash && fromHash.trim() ? fromHash.trim() : ''
  }, [])

  // 从当前 URL 中移除 token 参数，避免令牌被书签/Referer/浏览器历史泄露。
  const stripTokenFromUrl = useCallback(() => {
    if (typeof window === 'undefined') return
    try {
      const url = new URL(window.location.href)
      let changed = false
      if (url.searchParams.has('token')) {
        url.searchParams.delete('token')
        changed = true
      }
      const rawHash = url.hash.replace(/^#/, '')
      if (rawHash) {
        const queryIdx = rawHash.indexOf('?')
        if (queryIdx >= 0) {
          const path = rawHash.slice(0, queryIdx)
          const hashParams = new URLSearchParams(rawHash.slice(queryIdx + 1))
          if (hashParams.has('token')) {
            hashParams.delete('token')
            const next = hashParams.toString()
            url.hash = next ? `#${path}?${next}` : `#${path}`
            changed = true
          }
        } else if (/^token=/.test(rawHash)) {
          url.hash = ''
          changed = true
        }
      }
      if (changed) {
        window.history.replaceState(null, '', url.toString())
      }
    } catch {
      // 忽略 URL 解析异常
    }
  }, [])

  // 获取实际应用的主题（处理 system 情况）
  const getActualTheme = () => {
    if (theme === 'system') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    }
    return theme
  }

  const actualTheme = getActualTheme()

  // 主题切换（无动画）
  const toggleTheme = () => {
    const newTheme = actualTheme === 'dark' ? 'light' : 'dark'
    setTheme(newTheme)
  }

  const verifyToken = useCallback(
    async (rawToken: string): Promise<boolean> => {
      const trimmed = rawToken.trim()
      setError('')

      if (!trimmed) {
        setError(t('auth.tokenRequired'))
        return false
      }

      setIsValidating(true)
      try {
        // 向后端发送请求验证 token（后端会设置 HttpOnly Cookie）。
        // 走 authApi：401（token 错误）透传后端信息，不触发整页跳转。
        const data = await authApi.post<{
          valid: boolean
          is_first_setup?: boolean
          requires_custom_token?: boolean
          message?: string
        }>('/api/webui/auth/verify', {
          body: { token: trimmed },
          errorMessage: t('auth.verifyFailed'),
        })

        if (data.valid) {
          // Token 验证成功，Cookie 已由后端设置
          // 等待一小段时间确保 Cookie 已设置
          await new Promise((resolve) => setTimeout(resolve, 100))

          // 再次检查认证状态
          await checkAuthStatus()

          // 直接使用验证响应中的 is_first_setup 字段，避免额外请求
          if (data.requires_custom_token || data.is_first_setup) {
            navigate({ to: '/setup' })
          } else {
            navigate({ to: '/' })
          }
          return true
        }

        console.error('Token 验证失败:', data.message)
        setError(data.message || t('auth.verifyFailed'))
        return false
      } catch (err) {
        console.error('Token 验证错误:', err)
        setError(err instanceof Error ? err.message : t('auth.connFailed'))
        return false
      } finally {
        setIsValidating(false)
      }
    },
    [navigate, t]
  )

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    await verifyToken(token)
  }

  // 如果已经认证，直接跳转到首页；否则尝试用 URL 中的 token 自动登录。
  useEffect(() => {
    const verifyAuth = async () => {
      try {
        const isAuth = await checkAuthStatus()
        if (isAuth) {
          // 已登录场景下，URL 中残留的 token 也清掉，避免外泄。
          stripTokenFromUrl()
          const setupStatus = await getSetupStatus()
          navigate({
            to: setupStatus?.requires_custom_token || setupStatus?.is_first_setup ? '/setup' : '/',
          })
          return
        }

        // 未登录：检查 URL 是否带 token，带了就自动尝试登录。
        const urlToken = extractUrlToken()
        if (urlToken && !urlTokenHandledRef.current) {
          urlTokenHandledRef.current = true
          // 立即从 URL 中剥离 token，防止刷新/复制链接时再次暴露。
          stripTokenFromUrl()
          setToken(urlToken)
          // 异步触发验证；失败时错误信息会显示在表单上，token 也会保留在输入框中以便用户修正。
          void verifyToken(urlToken)
        }
      } catch {
        // 忽略错误，保持在登录页
      } finally {
        setCheckingAuth(false)
      }
    }
    verifyAuth()
  }, [navigate, extractUrlToken, stripTokenFromUrl, verifyToken])

  // 正在检查认证状态时显示加载
  if (checkingAuth) {
    return (
      <div data-auth-page="true" className="relative isolate flex min-h-screen items-center justify-center overflow-hidden bg-background p-4">
        {showRetroGears && <AuthRetroGears />}
        <div className="text-muted-foreground">{t('auth.checkingAuth')}</div>
      </div>
    )
  }

  return (
    <div data-auth-page="true" className="relative isolate flex min-h-screen items-center justify-center overflow-hidden bg-background p-4">
      {showRetroGears && <AuthRetroGears />}

      {/* 认证卡片 - 磨砂玻璃效果 */}
      <Card className="relative z-10 w-full max-w-md shadow-2xl backdrop-blur-xl bg-card/80 border-border/50">
        {/* 主题切换按钮 */}
        <button
          type="button"
          data-auth-theme-toggle="true"
          onClick={toggleTheme}
          className="absolute right-4 top-4 rounded-lg p-2 hover:bg-accent transition-colors z-10 text-foreground"
          title={actualTheme === 'dark' ? t('auth.switchToLight') : t('auth.switchToDark')}
        >
          {actualTheme === 'dark' ? (
            <Sun className="h-5 w-5" strokeWidth={2.5} fill="none" />
          ) : (
            <Moon className="h-5 w-5" strokeWidth={2.5} fill="none" />
          )}
        </button>

        <CardHeader className="space-y-4 text-center">
          {/* Logo/Icon */}
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
            <Lock className="h-8 w-8 text-primary" strokeWidth={2} fill="none" />
          </div>

          <div className="space-y-2">
            <CardTitle className="text-2xl font-bold">{t('auth.welcome')}</CardTitle>
            <CardDescription className="text-base">
              {t('auth.accessDesc')}
            </CardDescription>
          </div>
        </CardHeader>

        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Token 输入框 */}
            <div className="space-y-2">
              <Label htmlFor="token" className="text-sm font-medium">
                Access Token
              </Label>
              <div className="relative">
                <Key className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" strokeWidth={2} fill="none" />
                <Input
                  id="token"
                  type="password"
                  placeholder={t('auth.tokenPlaceholder')}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className={cn('pl-10', error && 'border-red-500 focus-visible:ring-red-500')}
                  disabled={isValidating}
                  autoFocus
                  autoComplete="off"
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? 'token-error' : undefined}
                />
              </div>
            </div>

            {/* 错误提示 */}
            {error && (
              <div id="token-error" role="alert" className="flex items-center gap-2 rounded-md bg-red-50 p-3 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400">
                <AlertCircle className="h-4 w-4 flex-shrink-0" strokeWidth={2} fill="none" />
                <span>{error}</span>
              </div>
            )}

            {/* 提交按钮 */}
            <Button type="submit" className="w-full" disabled={isValidating}>
              {isValidating ? (
                <>
                  <div className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  {t('auth.verifyingLabel')}
                </>
              ) : (
                t('auth.verifyEnter')
              )}
            </Button>

            {/* 帮助文本 */}
            <Dialog>
              <DialogTrigger asChild>
                <button className="w-full text-center text-sm text-primary hover:text-primary/80 transition-colors underline-offset-4 hover:underline flex items-center justify-center gap-1">
                  <HelpCircle className="h-4 w-4" strokeWidth={2} fill="none" />
                  {t('auth.helpLink')}
                </button>
              </DialogTrigger>
              <DialogContent className="sm:max-w-md">
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-2">
                    <Lock className="h-5 w-5 text-primary" strokeWidth={2} fill="none" />
                    {t('auth.helpTitle')}
                  </DialogTitle>
                  <DialogDescription>
                    {t('auth.helpDesc')}
                  </DialogDescription>
                </DialogHeader>

                <div className="space-y-4">
                  {/* 方式一：查看控制台 */}
                  <div className="rounded-lg border bg-muted/50 p-4 space-y-2">
                    <div className="flex items-start gap-3">
                      <Terminal className="h-5 w-5 text-primary flex-shrink-0 mt-0.5" strokeWidth={2} fill="none" />
                      <div className="flex-1 space-y-2">
                        <h4 className="font-semibold text-sm">{t('auth.method1Title')}</h4>
                        <p className="text-sm text-muted-foreground">
                          {t('auth.method1Desc')}
                        </p>
                        <div className="rounded bg-background p-2 font-mono text-xs">
                          <p className="text-muted-foreground">{t('auth.method1Example1')}</p>
                          <p className="text-muted-foreground">{t('auth.method1Example2')}</p>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 方式二：查看配置文件 */}
                  <div className="rounded-lg border bg-muted/50 p-4 space-y-2">
                    <div className="flex items-start gap-3">
                      <FileText className="h-5 w-5 text-primary flex-shrink-0 mt-0.5" strokeWidth={2} fill="none" />
                      <div className="flex-1 space-y-2">
                        <h4 className="font-semibold text-sm">{t('auth.method2Title')}</h4>
                        <p className="text-sm text-muted-foreground">
                          {t('auth.method2Desc')}
                        </p>
                        <div className="rounded bg-background p-2 font-mono text-xs break-all">
                          <code className="text-primary">data/webui.json</code>
                        </div>
                        <p className="text-xs text-muted-foreground">
                          {t('auth.method2FileHint')} <code className="px-1 py-0.5 bg-background rounded">access_token</code>
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* 安全提示 */}
                  <div className="rounded-lg border border-yellow-200 dark:border-yellow-900 bg-yellow-50 dark:bg-yellow-950/30 p-3">
                    <div className="flex gap-2">
                      <AlertCircle className="h-4 w-4 text-yellow-600 dark:text-yellow-500 flex-shrink-0 mt-0.5" strokeWidth={2} fill="none" />
                      <div className="text-sm text-yellow-800 dark:text-yellow-300 space-y-1">
                        <p className="font-semibold">{t('auth.securityTipTitle')}</p>
                        <ul className="list-disc list-inside space-y-0.5 text-xs">
                          <li>{t('auth.securityTip1')}</li>
                          <li>{t('auth.securityTip2')}</li>
                        </ul>
                      </div>
                    </div>
                  </div>
                </div>
              </DialogContent>
            </Dialog>

          </form>
        </CardContent>
      </Card>

      {/* 页脚信息 */}
      <div className="absolute bottom-4 left-0 right-0 z-10 text-center text-xs text-muted-foreground">
        <p>{APP_FULL_NAME}</p>
      </div>
    </div>
  )
}
