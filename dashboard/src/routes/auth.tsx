import { useState, useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Key, Lock, AlertCircle, Moon, Sun, HelpCircle, FileText, Terminal, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
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
import { WavesBackground } from '@/components/waves-background'
import { useAnimation } from '@/hooks/use-animation'
import { useTheme } from '@/components/use-theme'
import { checkAuthStatus } from '@/lib/fetch-with-auth'
import { cn } from '@/lib/utils'
import { APP_FULL_NAME } from '@/lib/version'

export function AuthPage() {
  const [token, setToken] = useState('')
  const [isValidating, setIsValidating] = useState(false)
  const [error, setError] = useState('')
  const [checkingAuth, setCheckingAuth] = useState(true)
  const navigate = useNavigate()
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
      setError('请输入 Access Token')
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
      
      const data = await response.json()
      console.log('Token 验证响应数据:', data)

      if (response.ok && data.valid) {
        console.log('Token 验证成功，准备跳转...')
        console.log('is_first_setup:', data.is_first_setup)
        
        // Token 验证成功，Cookie 已由后端设置
        // 等待一小段时间确保 Cookie 已设置
        await new Promise(resolve => setTimeout(resolve, 100))
        
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
        setError(data.message || 'Token 验证失败，请检查后重试')
      }
    } catch (err) {
      console.error('Token 验证错误:', err)
      setError('连接服务器失败，请检查网络连接')
    } finally {
      setIsValidating(false)
    }
  }

  // 正在检查认证状态时显示加载
  if (checkingAuth) {
    return (
      <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background p-4">
        {enableWavesBackground && <WavesBackground />}
        <div className="text-muted-foreground">正在检查登录状态...</div>
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
          title={actualTheme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
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
            <CardTitle className="text-2xl font-bold">欢迎使用 MaiBot</CardTitle>
            <CardDescription className="text-base">
              请输入您的 Access Token 以继续访问系统
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
                  placeholder="请输入您的 Access Token"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className={cn('pl-10', error && 'border-red-500 focus-visible:ring-red-500')}
                  disabled={isValidating}
                  autoFocus
                  autoComplete="off"
                />
              </div>
            </div>

            {/* 错误提示 */}
            {error && (
              <div className="flex items-center gap-2 rounded-md bg-red-50 p-3 text-sm text-red-600 dark:bg-red-950/50 dark:text-red-400">
                <AlertCircle className="h-4 w-4 flex-shrink-0" strokeWidth={2} fill="none" />
                <span>{error}</span>
              </div>
            )}

            {/* 提交按钮 */}
            <Button type="submit" className="w-full" disabled={isValidating}>
              {isValidating ? (
                <>
                  <div className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  验证中...
                </>
              ) : (
                '验证并进入'
              )}
            </Button>

            {/* 帮助文本 */}
            <Dialog>
              <DialogTrigger asChild>
                <button className="w-full text-center text-sm text-primary hover:text-primary/80 transition-colors underline-offset-4 hover:underline flex items-center justify-center gap-1">
                  <HelpCircle className="h-4 w-4" strokeWidth={2} fill="none" />
                  我没有 Token，我该去哪里获得 Token？
                </button>
              </DialogTrigger>
              <DialogContent className="sm:max-w-md">
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-2">
                    <Lock className="h-5 w-5 text-primary" strokeWidth={2} fill="none" />
                    如何获取 Access Token
                  </DialogTitle>
                  <DialogDescription>
                    Access Token 是访问 MaiBot WebUI 的唯一凭证，请按以下方式获取
                  </DialogDescription>
                </DialogHeader>

                <div className="space-y-4">
                  {/* 方式一：查看控制台 */}
                  <div className="rounded-lg border bg-muted/50 p-4 space-y-2">
                    <div className="flex items-start gap-3">
                      <Terminal className="h-5 w-5 text-primary flex-shrink-0 mt-0.5" strokeWidth={2} fill="none" />
                      <div className="flex-1 space-y-2">
                        <h4 className="font-semibold text-sm">方式一：查看启动日志</h4>
                        <p className="text-sm text-muted-foreground">
                          在 MaiBot 启动时，控制台会显示 WebUI Access Token。
                        </p>
                        <div className="rounded bg-background p-2 font-mono text-xs">
                          <p className="text-muted-foreground">🔑 WebUI Access Token: abc123...</p>
                          <p className="text-muted-foreground">💡 请使用此 Token 登录 WebUI</p>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 方式二：查看配置文件 */}
                  <div className="rounded-lg border bg-muted/50 p-4 space-y-2">
                    <div className="flex items-start gap-3">
                      <FileText className="h-5 w-5 text-primary flex-shrink-0 mt-0.5" strokeWidth={2} fill="none" />
                      <div className="flex-1 space-y-2">
                        <h4 className="font-semibold text-sm">方式二：查看配置文件</h4>
                        <p className="text-sm text-muted-foreground">
                          Token 保存在项目根目录的配置文件中：
                        </p>
                        <div className="rounded bg-background p-2 font-mono text-xs break-all">
                          <code className="text-primary">data/webui.json</code>
                        </div>
                        <p className="text-xs text-muted-foreground">
                          打开此文件，复制 <code className="px-1 py-0.5 bg-background rounded">access_token</code> 字段的值
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* 安全提示 */}
                  <div className="rounded-lg border border-yellow-200 dark:border-yellow-900 bg-yellow-50 dark:bg-yellow-950/30 p-3">
                    <div className="flex gap-2">
                      <AlertCircle className="h-4 w-4 text-yellow-600 dark:text-yellow-500 flex-shrink-0 mt-0.5" strokeWidth={2} fill="none" />
                      <div className="text-sm text-yellow-800 dark:text-yellow-300 space-y-1">
                        <p className="font-semibold">安全提示</p>
                        <ul className="list-disc list-inside space-y-0.5 text-xs">
                          <li>请妥善保管您的 Token，不要泄露给他人</li>
                          <li>如需重置 Token，请在登录后前往系统设置</li>
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
                  我觉得这个界面很卡怎么办？
                </button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle className="flex items-center gap-2">
                    <Zap className="h-5 w-5 text-primary" strokeWidth={2} fill="none" />
                    关闭背景动画
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    背景动画可能会在低性能设备上造成卡顿。关闭动画可以显著提升界面流畅度。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <div className="rounded-lg border bg-muted/50 p-4 space-y-2">
                  <p className="text-sm text-muted-foreground">
                    关闭动画后，背景将变为纯色，但不影响任何功能的使用。您可以随时在系统设置中重新开启动画。
                  </p>
                </div>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={() => setEnableWavesBackground(false)}
                  >
                    关闭动画
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
