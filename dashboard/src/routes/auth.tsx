import { useEffect, useState } from 'react'
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
  Zap,
} from 'lucide-react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
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
import { WavesBackground } from '@/components/waves-background'
import { useTheme } from '@/components/use-theme'

import { useAnimation } from '@/hooks/use-animation'

import { parseResponse } from '@/lib/api-helpers'
import { checkAuthStatus } from '@/lib/fetch-with-auth'
import { cn } from '@/lib/utils'
import { APP_FULL_NAME } from '@/lib/version'


export function AuthPage() {
  const [token, setToken] = useState('')
  const [isValidating, setIsValidating] = useState(false)
  const [error, setError] = useState('')
  const [checkingAuth, setCheckingAuth] = useState(true)
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { enableWavesBackground, setEnableWavesBackground } = useAnimation()
  const { theme, setTheme } = useTheme()

  // 如果已经认证，直接跳转到首页
  useEffect(() => {
    const verifyAuth = async () => {
      try {
        const isAuth = await checkAuthStatus()
        if (isAuth) {
          navigate({ to: '/' })
        }
      } catch {
        // 忽略错误，保持在登录页
      } finally {
        setCheckingAuth(false)
      }
    }
    verifyAuth()
  }, [navigate])

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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!token.trim()) {
      setError(t('auth.tokenRequired'))
      return
    }

    setIsValidating(true)

    console.log('开始验证 token...')

    try {
      // 向后端发送请求验证 token（后端会设置 HttpOnly Cookie）
      const response = await fetch('/api/webui/auth/verify', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include', // 确保接收并存储 Cookie
        body: JSON.stringify({ token: token.trim() }),
      })

      console.log('Token 验证响应状态:', response.status)

      const result = await parseResponse<{
        valid: boolean
        is_first_setup?: boolean
        message?: string
      }>(response)

      if (!result.success) {
        console.error('Token 验证失败:', result.error)
        setError(result.error)
        return
      }

      const data = result.data
      console.log('Token 验证响应数据:', data)

      if (data.valid) {
        console.log('Token 验证成功，准备跳转...')
        console.log('is_first_setup:', data.is_first_setup)

        // Token 验证成功，Cookie 已由后端设置
        // 等待一小段时间确保 Cookie 已设置
        await new Promise((resolve) => setTimeout(resolve, 100))

        // 再次检查认证状态
        const authCheck = await checkAuthStatus()
        console.log('跳转前认证状态检查:', authCheck)

        // 直接使用验证响应中的 is_first_setup 字段，避免额外请求
        if (data.is_first_setup) {
          console.log('跳转到首次配置页面')
          // 需要首次配置，跳转到配置向导
          navigate({ to: '/setup' })
        } else {
          console.log('跳转到首页')
          // 不需要配置或配置已完成，跳转到首页
          navigate({ to: '/' })
        }
      } else {
        console.error('Token 验证失败:', data.message)
        setError(data.message || t('auth.verifyFailed'))
      }
    } catch (err) {
      console.error('Token 验证错误:', err)
      setError(
        err instanceof Error ? err.message : t('auth.connFailed')
      )
    } finally {
      setIsValidating(false)
    }
  }

  // 正在检查认证状态时显示加载
  if (checkingAuth) {
    return (
      <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background p-4">
        {enableWavesBackground && <WavesBackground />}
        <div className="text-muted-foreground">{t('auth.checkingAuth')}</div>
      </div>
    )
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background p-4">
      {/* 波浪背景 - 独立控制 */}
      {enableWavesBackground && <WavesBackground />}

      {/* 认证卡片 - 磨砂玻璃效果 */}
      <Card className="relative z-10 w-full max-w-md shadow-2xl backdrop-blur-xl bg-card/80 border-border/50">
        {/* 主题切换按钮 */}
        <button
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

            {/* 性能优化选项 */}
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <button className="w-full text-center text-sm text-muted-foreground hover:text-foreground transition-colors underline-offset-4 hover:underline flex items-center justify-center gap-1">
                  <Zap className="h-4 w-4" strokeWidth={2} fill="none" />
                  {t('auth.slowLink')}
                </button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle className="flex items-center gap-2">
                    <Zap className="h-5 w-5 text-primary" strokeWidth={2} fill="none" />
                    {t('auth.disableAnimTitle')}
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    {t('auth.disableAnimDesc')}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <div className="rounded-lg border bg-muted/50 p-4 space-y-2">
                  <p className="text-sm text-muted-foreground">
                    {t('auth.disableAnimDetail')}
                  </p>
                </div>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={() => setEnableWavesBackground(false)}
                  >
                    {t('auth.disableAnimBtn')}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </form>
        </CardContent>
      </Card>

      {/* 页脚信息 */}
      <div className="absolute bottom-4 left-0 right-0 text-center text-xs text-muted-foreground">
        <p>{APP_FULL_NAME}</p>
      </div>
    </div>
  )
}
