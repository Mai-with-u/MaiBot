import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Copy,
  Eye,
  EyeOff,
  RefreshCw,
  XCircle,
} from 'lucide-react'
import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import { useNavigate } from '@tanstack/react-router'
import { cn } from '@/lib/utils'
import { useToast } from '@/hooks/use-toast'
import { validateToken } from '@/lib/token-validator'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
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

export function SecurityTab() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [currentToken, setCurrentToken] = useState('')
  const [newToken, setNewToken] = useState('')
  const [showCurrentToken, setShowCurrentToken] = useState(false)
  const [showNewToken, setShowNewToken] = useState(false)
  const [isUpdating, setIsUpdating] = useState(false)
  const [isRegenerating, setIsRegenerating] = useState(false)
  const [copied, setCopied] = useState(false)
  const [showTokenDialog, setShowTokenDialog] = useState(false)
  const [generatedToken, setGeneratedToken] = useState('')
  const [tokenCopied, setTokenCopied] = useState(false)
  const { toast } = useToast()

  // 实时验证新 Token
  const tokenValidation = useMemo(() => validateToken(newToken), [newToken])

  // 复制 token 到剪贴板
  const copyToClipboard = async (text: string) => {
    if (!currentToken) {
      toast({
        title: t('settings.security.cannotCopy'),
        description: t('settings.security.cannotCopyDesc'),
        variant: 'destructive',
      })
      return
    }
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      toast({
        title: t('settings.security.copySuccess'),
        description: t('settings.security.copySuccessDesc'),
      })
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast({
        title: t('settings.security.copyFailed'),
        description: t('settings.security.copyFailedDesc'),
        variant: 'destructive',
      })
    }
  }

  // 更新 token
  const handleUpdateToken = async () => {
    if (!newToken.trim()) {
      toast({
        title: t('settings.security.inputError'),
        description: t('settings.security.inputErrorDesc'),
        variant: 'destructive',
      })
      return
    }

    // 验证 Token 格式
    if (!tokenValidation.isValid) {
      const failedRules = tokenValidation.rules
        .filter((rule) => !rule.passed)
        .map((rule) => rule.label)
        .join(', ')
      
      toast({
        title: t('settings.security.formatError'),
        description: t('settings.security.formatErrorDesc', { failedRules }),
        variant: 'destructive',
      })
      return
    }

    setIsUpdating(true)

    try {
      const response = await fetch('/api/webui/auth/update', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include', // 使用 Cookie 认证
        body: JSON.stringify({ new_token: newToken.trim() }),
      })

      const data = await response.json()

      if (response.ok && data.success) {
        // 清空输入框
        setNewToken('')
        
        // 更新当前显示的 Token
        setCurrentToken(newToken.trim())
        
        toast({
          title: t('settings.security.updateSuccess'),
          description: t('settings.security.updateSuccessDesc'),
        })

        // 延迟跳转到登录页
        setTimeout(() => {
          navigate({ to: '/auth' })
        }, 1500)
      } else {
        toast({
          title: t('settings.security.updateFailed'),
          description: data.message || t('settings.security.updateFailedDesc'),
          variant: 'destructive',
        })
      }
    } catch (err) {
      console.error('更新 Token 错误:', err)
      toast({
        title: t('settings.security.updateFailed'),
        description: t('settings.security.updateFailedConn'),
        variant: 'destructive',
      })
    } finally {
      setIsUpdating(false)
    }
  }

  // 重新生成 token (实际执行函数)
  const executeRegenerateToken = async () => {
    setIsRegenerating(true)

    try {
      const response = await fetch('/api/webui/auth/regenerate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include', // 使用 Cookie 认证
      })

      const data = await response.json()

      if (response.ok && data.success) {
        // 更新当前显示的 Token
        setCurrentToken(data.token)
        
        // 显示弹窗展示新 Token
        setGeneratedToken(data.token)
        setShowTokenDialog(true)
        setTokenCopied(false)
        
        toast({
          title: t('settings.security.generateSuccess'),
          description: t('settings.security.generateSuccessDesc'),
        })
      } else {
        toast({
          title: t('settings.security.generateFailed'),
          description: data.message || t('settings.security.generateFailedDesc'),
          variant: 'destructive',
        })
      }
    } catch (err) {
      console.error('生成 Token 错误:', err)
      toast({
        title: t('settings.security.generateFailed'),
        description: t('settings.security.generateFailedConn'),
        variant: 'destructive',
      })
    } finally {
      setIsRegenerating(false)
    }
  }

  // 复制生成的 Token
  const copyGeneratedToken = async () => {
    try {
      await navigator.clipboard.writeText(generatedToken)
      setTokenCopied(true)
      toast({
        title: t('settings.security.copySuccess'),
        description: t('settings.security.copySuccessDesc'),
      })
    } catch {
      toast({
        title: t('settings.security.copyFailed'),
        description: t('settings.security.copyFailedDesc'),
        variant: 'destructive',
      })
    }
  }

  // 关闭弹窗
  const handleCloseDialog = () => {
    setShowTokenDialog(false)
    // 延迟清空 token，避免用户看到内容消失
    setTimeout(() => {
      setGeneratedToken('')
      setTokenCopied(false)
    }, 300)
    
    // 跳转到登录页
    setTimeout(() => {
      navigate({ to: '/auth' })
    }, 500)
  }

  // 处理对话框状态变化（包括点击外部、ESC 等关闭方式）
  const handleDialogOpenChange = (open: boolean) => {
    if (!open) {
      handleCloseDialog()
    }
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Token 生成成功弹窗 */}
      <Dialog open={showTokenDialog} onOpenChange={handleDialogOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-yellow-500" />
              {t('settings.security.dialogTitle')}
            </DialogTitle>
            <DialogDescription>
              {t('settings.security.dialogDesc')}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Token 显示区域 */}
            <div className="rounded-lg border-2 border-primary/20 bg-primary/5 p-4">
              <Label className="text-xs text-muted-foreground mb-2 block">
                {t('settings.security.dialogTokenLabel')}
              </Label>
              <div className="font-mono text-sm break-all select-all bg-background p-3 rounded border">
                {generatedToken}
              </div>
            </div>

            {/* 警告提示 */}
            <div className="rounded-lg border border-yellow-200 dark:border-yellow-900 bg-yellow-50 dark:bg-yellow-950/30 p-3">
              <div className="flex gap-2">
                <AlertTriangle className="h-4 w-4 text-yellow-600 dark:text-yellow-500 flex-shrink-0 mt-0.5" />
                <div className="text-sm text-yellow-800 dark:text-yellow-300 space-y-1">
                  <p className="font-semibold">{t('settings.security.important')}</p>
                  <ul className="list-disc list-inside space-y-0.5 text-xs">
                    <li>{t('settings.security.tip1')}</li>
                    <li>{t('settings.security.tip2')}</li>
                    <li>{t('settings.security.tip3')}</li>
                    <li>{t('settings.security.tip4')}</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              variant="outline"
              onClick={copyGeneratedToken}
              className="gap-2"
            >
              {tokenCopied ? (
                <>
                  <Check className="h-4 w-4 text-green-500" />
                  {t('settings.security.copied')}
                </>
              ) : (
                <>
                  <Copy className="h-4 w-4" />
                  {t('settings.security.copyToken')}
                </>
              )}
            </Button>
            <Button onClick={handleCloseDialog}>
              {t('settings.security.savedClose')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 当前 Token */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">{t('settings.security.currentToken')}</h3>
        <div className="space-y-3 sm:space-y-4">
          <div className="space-y-2">
            <Label htmlFor="current-token" className="text-sm">{t('settings.security.yourToken')}</Label>
            <div className="flex flex-col sm:flex-row gap-2">
              <div className="relative flex-1">
                <Input
                  id="current-token"
                  type={showCurrentToken ? 'text' : 'password'}
                  value={currentToken || '••••••••••••••••••••••••••••••••'}
                  readOnly
                  className="pr-10 font-mono text-sm"
                  placeholder={t('settings.security.tokenStorePlaceholder')}
                />
                <button
                  onClick={() => {
                    if (currentToken) {
                      setShowCurrentToken(!showCurrentToken)
                    } else {
                      toast({
                        title: t('settings.security.cannotView'),
                        description: t('settings.security.cannotViewDesc'),
                      })
                    }
                  }}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 hover:bg-accent rounded"
                  title={showCurrentToken ? t('settings.security.hide') : t('settings.security.show')}
                >
                  {showCurrentToken ? (
                    <EyeOff className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <Eye className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>
              </div>
              <div className="flex gap-2 w-full sm:w-auto">
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => copyToClipboard(currentToken)}
                  title={t('settings.security.copyTip')}
                  className="flex-shrink-0"
                  disabled={!currentToken}
                >
                  {copied ? (
                    <Check className="h-4 w-4 text-green-500" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </Button>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="outline"
                      disabled={isRegenerating}
                      className="gap-2 flex-1 sm:flex-none"
                    >
                      <RefreshCw className={cn('h-4 w-4', isRegenerating && 'animate-spin')} />
                      <span className="hidden sm:inline">{t('settings.security.regenerate')}</span>
                      <span className="sm:hidden">{t('settings.security.regenerateShort')}</span>
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>{t('settings.security.confirmRegenerate')}</AlertDialogTitle>
                      <AlertDialogDescription>
                        {t('settings.security.confirmRegenerateFullDesc')}
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>{t('settings.security.cancel')}</AlertDialogCancel>
                      <AlertDialogAction onClick={executeRegenerateToken}>
                        {t('settings.security.confirmGenerate')}
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            </div>
            <p className="text-[10px] sm:text-xs text-muted-foreground">
              {t('settings.security.safekeepTip')}
            </p>
          </div>
        </div>
      </div>

      {/* 更新 Token */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">{t('settings.security.customToken')}</h3>
        <div className="space-y-3 sm:space-y-4">
          <div className="space-y-2">
            <Label htmlFor="new-token" className="text-sm">{t('settings.security.newTokenLabel')}</Label>
            <div className="relative">
              <Input
                id="new-token"
                type={showNewToken ? 'text' : 'password'}
                value={newToken}
                onChange={(e) => setNewToken(e.target.value)}
                className="pr-10 font-mono text-sm"
                placeholder={t('settings.security.customTokenPlaceholder')}
              />
              <button
                onClick={() => setShowNewToken(!showNewToken)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 hover:bg-accent rounded"
                title={showNewToken ? t('settings.security.hide') : t('settings.security.show')}
              >
                {showNewToken ? (
                  <EyeOff className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <Eye className="h-4 w-4 text-muted-foreground" />
                )}
              </button>
            </div>
            
            {/* Token 验证规则显示 */}
            {newToken && (
              <div className="mt-3 space-y-2 p-3 rounded-lg bg-muted/50">
                <p className="text-sm font-medium text-foreground">{t('settings.security.tokenReqTitle')}</p>
                <div className="space-y-1.5">
                  {tokenValidation.rules.map((rule) => (
                    <div key={rule.id} className="flex items-center gap-2 text-sm">
                      {rule.passed ? (
                        <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0" />
                      ) : (
                        <XCircle className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                      )}
                      <span className={cn(
                        rule.passed ? 'text-green-600 dark:text-green-400' : 'text-muted-foreground'
                      )}>
                        {rule.label}
                      </span>
                    </div>
                  ))}
                </div>
                {tokenValidation.isValid && (
                  <div className="mt-2 pt-2 border-t border-border">
                    <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
                      <Check className="h-4 w-4" />
                      <span className="font-medium">{t('settings.security.tokenValid')}</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
          <Button 
            onClick={handleUpdateToken} 
            disabled={isUpdating || !tokenValidation.isValid || !newToken} 
            className="w-full sm:w-auto"
          >
            {isUpdating ? t('settings.security.updating') : t('settings.security.updateBtn')}
          </Button>
        </div>
      </div>

      {/* 安全提示 */}
      <div className="rounded-lg border border-yellow-200 dark:border-yellow-900 bg-yellow-50 dark:bg-yellow-950/30 p-3 sm:p-4">
        <h4 className="text-sm sm:text-base font-semibold text-yellow-900 dark:text-yellow-200 mb-2">{t('settings.security.securityTip')}</h4>
        <ul className="text-xs sm:text-sm text-yellow-800 dark:text-yellow-300 space-y-1 list-disc list-inside">
          <li>{t('settings.security.securityTip1')}</li>
          <li>{t('settings.security.securityTip2')}</li>
          <li>{t('settings.security.securityTip3')}</li>
          <li>{t('settings.security.securityTip4')}</li>
          <li>{t('settings.security.securityTip5')}</li>
          <li>{t('settings.security.securityTip6')}</li>
        </ul>
      </div>
    </div>
  )
}
