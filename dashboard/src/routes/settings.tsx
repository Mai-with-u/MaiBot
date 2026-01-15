import { Palette, Info, Shield, Eye, EyeOff, Copy, RefreshCw, Check, CheckCircle2, XCircle, AlertTriangle, Settings, RotateCcw, Database, Download, Upload, Trash2, HardDrive } from 'lucide-react'
import { useTheme } from '@/components/use-theme'
import { useAnimation } from '@/hooks/use-animation'
import { useState, useMemo, useEffect, useRef } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { cn } from '@/lib/utils'
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useToast } from '@/hooks/use-toast'
import { validateToken } from '@/lib/token-validator'
import { APP_VERSION, APP_NAME } from '@/lib/version'
import {
  getSetting,
  setSetting,
  exportSettings,
  importSettings,
  resetAllSettings,
  clearLocalCache,
  getStorageUsage,
  formatBytes,
  DEFAULT_SETTINGS,
} from '@/lib/settings-manager'
import { Slider } from '@/components/ui/slider'
import { logWebSocket } from '@/lib/log-websocket'
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

export function SettingsPage() {
  return (
    <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">系统设置</h1>
          <p className="text-muted-foreground mt-1 sm:mt-2 text-sm sm:text-base">管理您的应用偏好设置</p>
        </div>
      </div>

      {/* 标签页 */}
      <Tabs defaultValue="appearance" className="w-full">
        <TabsList className="grid w-full grid-cols-2 sm:grid-cols-4 gap-0.5 sm:gap-1 h-auto p-1">
          <TabsTrigger value="appearance" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <Palette className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>外观</span>
          </TabsTrigger>
          <TabsTrigger value="security" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <Shield className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>安全</span>
          </TabsTrigger>
          <TabsTrigger value="other" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <Settings className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>其他</span>
          </TabsTrigger>
          <TabsTrigger value="about" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <Info className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>关于</span>
          </TabsTrigger>
        </TabsList>

        <ScrollArea className="h-[calc(100vh-240px)] sm:h-[calc(100vh-280px)] mt-4 sm:mt-6">
          <TabsContent value="appearance" className="mt-0">
            <AppearanceTab />
          </TabsContent>

          <TabsContent value="security" className="mt-0">
            <SecurityTab />
          </TabsContent>

          <TabsContent value="other" className="mt-0">
            <OtherTab />
          </TabsContent>

          <TabsContent value="about" className="mt-0">
            <AboutTab />
          </TabsContent>
        </ScrollArea>
      </Tabs>
    </div>
  )
}

// 应用主题色的辅助函数
function applyAccentColor(color: string) {
  const root = document.documentElement
  
  // 预设颜色配置
  const colors = {
    // 单色
    blue: { 
      hsl: '221.2 83.2% 53.3%', 
      darkHsl: '217.2 91.2% 59.8%',
      gradient: null
    },
    purple: { 
      hsl: '271 91% 65%', 
      darkHsl: '270 95% 75%',
      gradient: null
    },
    green: { 
      hsl: '142 71% 45%', 
      darkHsl: '142 76% 36%',
      gradient: null
    },
    orange: { 
      hsl: '25 95% 53%', 
      darkHsl: '20 90% 48%',
      gradient: null
    },
    pink: { 
      hsl: '330 81% 60%', 
      darkHsl: '330 85% 70%',
      gradient: null
    },
    red: { 
      hsl: '0 84% 60%', 
      darkHsl: '0 90% 70%',
      gradient: null
    },
    
    // 渐变色
    'gradient-sunset': { 
      hsl: '15 95% 60%', 
      darkHsl: '15 95% 65%',
      gradient: 'linear-gradient(135deg, hsl(25 95% 53%) 0%, hsl(330 81% 60%) 100%)'
    },
    'gradient-ocean': { 
      hsl: '200 90% 55%', 
      darkHsl: '200 90% 60%',
      gradient: 'linear-gradient(135deg, hsl(221.2 83.2% 53.3%) 0%, hsl(189 94% 43%) 100%)'
    },
    'gradient-forest': { 
      hsl: '150 70% 45%', 
      darkHsl: '150 75% 40%',
      gradient: 'linear-gradient(135deg, hsl(142 71% 45%) 0%, hsl(158 64% 52%) 100%)'
    },
    'gradient-aurora': { 
      hsl: '310 85% 65%', 
      darkHsl: '310 90% 70%',
      gradient: 'linear-gradient(135deg, hsl(271 91% 65%) 0%, hsl(330 81% 60%) 100%)'
    },
    'gradient-fire': { 
      hsl: '15 95% 55%', 
      darkHsl: '15 95% 60%',
      gradient: 'linear-gradient(135deg, hsl(0 84% 60%) 0%, hsl(25 95% 53%) 100%)'
    },
    'gradient-twilight': { 
      hsl: '250 90% 60%', 
      darkHsl: '250 95% 65%',
      gradient: 'linear-gradient(135deg, hsl(239 84% 67%) 0%, hsl(271 91% 65%) 100%)'
    },
  }

  const selectedColor = colors[color as keyof typeof colors]
  if (selectedColor) {
    // 设置主色
    root.style.setProperty('--primary', selectedColor.hsl)
    
    // 设置渐变（如果有）
    if (selectedColor.gradient) {
      root.style.setProperty('--primary-gradient', selectedColor.gradient)
      root.classList.add('has-gradient')
    } else {
      root.style.removeProperty('--primary-gradient')
      root.classList.remove('has-gradient')
    }
  } else if (color.startsWith('#')) {
    // 自定义颜色 - 将 HEX 转换为 HSL
    const hexToHsl = (hex: string) => {
      // 移除 # 号
      hex = hex.replace('#', '')
      
      // 转换为 RGB
      const r = parseInt(hex.substring(0, 2), 16) / 255
      const g = parseInt(hex.substring(2, 4), 16) / 255
      const b = parseInt(hex.substring(4, 6), 16) / 255
      
      const max = Math.max(r, g, b)
      const min = Math.min(r, g, b)
      let h = 0
      let s = 0
      const l = (max + min) / 2
      
      if (max !== min) {
        const d = max - min
        s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
        
        switch (max) {
          case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6; break
          case g: h = ((b - r) / d + 2) / 6; break
          case b: h = ((r - g) / d + 4) / 6; break
        }
      }
      
      return `${Math.round(h * 360)} ${Math.round(s * 100)}% ${Math.round(l * 100)}%`
    }
    
    root.style.setProperty('--primary', hexToHsl(color))
    root.style.removeProperty('--primary-gradient')
    root.classList.remove('has-gradient')
  }
}

// 外观设置标签页
function AppearanceTab() {
  const { theme, setTheme } = useTheme()
  const { enableAnimations, setEnableAnimations, enableWavesBackground, setEnableWavesBackground } = useAnimation()
  const [accentColor, setAccentColor] = useState(() => {
    return localStorage.getItem('accent-color') || 'blue'
  })

  // 页面加载时应用保存的主题色
  useEffect(() => {
    const savedColor = localStorage.getItem('accent-color') || 'blue'
    applyAccentColor(savedColor)
  }, [])

  const handleAccentColorChange = (color: string) => {
    setAccentColor(color)
    localStorage.setItem('accent-color', color)
    applyAccentColor(color)
  }

  return (
    <div className="space-y-6 sm:space-y-8">
      {/* 主题模式 */}
      <div>
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">主题模式</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
          <ThemeOption
            value="light"
            current={theme}
            onChange={setTheme}
            label="浅色"
            description="始终使用浅色主题"
          />
          <ThemeOption
            value="dark"
            current={theme}
            onChange={setTheme}
            label="深色"
            description="始终使用深色主题"
          />
          <ThemeOption
            value="system"
            current={theme}
            onChange={setTheme}
            label="跟随系统"
            description="根据系统设置自动切换"
          />
        </div>
      </div>

      {/* 主题色 */}
      <div>
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">主题色</h3>
        
        {/* 单色预设 */}
        <div className="space-y-3 sm:space-y-4">
          <div>
            <h4 className="text-xs sm:text-sm font-medium mb-2 sm:mb-3">单色</h4>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 sm:gap-3">
              <ColorPresetOption
                value="blue"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="蓝色"
                colorClass="bg-blue-500"
              />
              <ColorPresetOption
                value="purple"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="紫色"
                colorClass="bg-purple-500"
              />
              <ColorPresetOption
                value="green"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="绿色"
                colorClass="bg-green-500"
              />
              <ColorPresetOption
                value="orange"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="橙色"
                colorClass="bg-orange-500"
              />
              <ColorPresetOption
                value="pink"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="粉色"
                colorClass="bg-pink-500"
              />
              <ColorPresetOption
                value="red"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="红色"
                colorClass="bg-red-500"
              />
            </div>
          </div>

          {/* 渐变色预设 */}
          <div>
            <h4 className="text-xs sm:text-sm font-medium mb-2 sm:mb-3">渐变色</h4>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 sm:gap-3">
              <ColorPresetOption
                value="gradient-sunset"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="日落"
                colorClass="bg-gradient-to-r from-orange-500 to-pink-500"
              />
              <ColorPresetOption
                value="gradient-ocean"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="海洋"
                colorClass="bg-gradient-to-r from-blue-500 to-cyan-500"
              />
              <ColorPresetOption
                value="gradient-forest"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="森林"
                colorClass="bg-gradient-to-r from-green-500 to-emerald-500"
              />
              <ColorPresetOption
                value="gradient-aurora"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="极光"
                colorClass="bg-gradient-to-r from-purple-500 to-pink-500"
              />
              <ColorPresetOption
                value="gradient-fire"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="烈焰"
                colorClass="bg-gradient-to-r from-red-500 to-orange-500"
              />
              <ColorPresetOption
                value="gradient-twilight"
                current={accentColor}
                onChange={handleAccentColorChange}
                label="暮光"
                colorClass="bg-gradient-to-r from-indigo-500 to-purple-500"
              />
            </div>
          </div>

          {/* 自定义颜色选择器 */}
          <div>
            <h4 className="text-xs sm:text-sm font-medium mb-2 sm:mb-3">自定义颜色</h4>
            <div className="flex flex-col sm:flex-row gap-3 sm:gap-4">
              <div className="flex-1">
                <input
                  type="color"
                  value={accentColor.startsWith('#') ? accentColor : '#3b82f6'}
                  onChange={(e) => handleAccentColorChange(e.target.value)}
                  className="h-10 sm:h-12 w-full rounded-lg border-2 border-border cursor-pointer"
                  title="选择自定义颜色"
                />
              </div>
              <div className="flex-1">
                <Input
                  type="text"
                  value={accentColor}
                  onChange={(e) => handleAccentColorChange(e.target.value)}
                  placeholder="#3b82f6"
                  className="font-mono text-sm"
                />
              </div>
            </div>
            <p className="text-[10px] sm:text-xs text-muted-foreground mt-2">
              点击色块选择颜色，或手动输入 HEX 颜色代码
            </p>
          </div>
        </div>
      </div>

      {/* 动效设置 */}
      <div>
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">动画效果</h3>
        <div className="space-y-2 sm:space-y-3">
          {/* 全局动画开关 */}
          <div className="rounded-lg border bg-card p-3 sm:p-4">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5 flex-1">
                <Label htmlFor="animations" className="text-base font-medium cursor-pointer">
                  启用动画效果
                </Label>
                <p className="text-sm text-muted-foreground">
                  关闭后将禁用所有过渡动画和特效，提升性能
                </p>
              </div>
              <Switch
                id="animations"
                checked={enableAnimations}
                onCheckedChange={setEnableAnimations}
              />
            </div>
          </div>

          {/* 波浪背景开关 */}
          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5 flex-1">
                <Label htmlFor="waves-background" className="text-base font-medium cursor-pointer">
                  登录页波浪背景
                </Label>
                <p className="text-sm text-muted-foreground">
                  关闭后登录页将使用纯色背景，适合低性能设备
                </p>
              </div>
              <Switch
                id="waves-background"
                checked={enableWavesBackground}
                onCheckedChange={setEnableWavesBackground}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// 安全设置标签页
function SecurityTab() {
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
        title: '无法复制',
        description: 'Token 存储在安全 Cookie 中，请重新生成以获取新 Token',
        variant: 'destructive',
      })
      return
    }
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      toast({
        title: '复制成功',
        description: 'Token 已复制到剪贴板',
      })
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast({
        title: '复制失败',
        description: '请手动复制 Token',
        variant: 'destructive',
      })
    }
  }

  // 更新 token
  const handleUpdateToken = async () => {
    if (!newToken.trim()) {
      toast({
        title: '输入错误',
        description: '请输入新的 Token',
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
        title: '格式错误',
        description: `Token 不符合要求: ${failedRules}`,
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
          title: '更新成功',
          description: 'Access Token 已更新，即将跳转到登录页',
        })

        // 延迟跳转到登录页
        setTimeout(() => {
          navigate({ to: '/auth' })
        }, 1500)
      } else {
        toast({
          title: '更新失败',
          description: data.message || '无法更新 Token',
          variant: 'destructive',
        })
      }
    } catch (err) {
      console.error('更新 Token 错误:', err)
      toast({
        title: '更新失败',
        description: '连接服务器失败',
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
          title: '生成成功',
          description: '新的 Access Token 已生成，请及时保存',
        })
      } else {
        toast({
          title: '生成失败',
          description: data.message || '无法生成新 Token',
          variant: 'destructive',
        })
      }
    } catch (err) {
      console.error('生成 Token 错误:', err)
      toast({
        title: '生成失败',
        description: '连接服务器失败',
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
        title: '复制成功',
        description: 'Token 已复制到剪贴板',
      })
    } catch {
      toast({
        title: '复制失败',
        description: '请手动复制 Token',
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
              新的 Access Token
            </DialogTitle>
            <DialogDescription>
              这是您的新 Token，请立即保存。关闭此窗口后将跳转到登录页面。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Token 显示区域 */}
            <div className="rounded-lg border-2 border-primary/20 bg-primary/5 p-4">
              <Label className="text-xs text-muted-foreground mb-2 block">
                您的新 Token (64位安全令牌)
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
                  <p className="font-semibold">重要提示</p>
                  <ul className="list-disc list-inside space-y-0.5 text-xs">
                    <li>此 Token 仅显示一次，关闭后无法再查看</li>
                    <li>请立即复制并保存到安全的位置</li>
                    <li>关闭窗口后将自动跳转到登录页面</li>
                    <li>请使用新 Token 重新登录系统</li>
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
                  已复制
                </>
              ) : (
                <>
                  <Copy className="h-4 w-4" />
                  复制 Token
                </>
              )}
            </Button>
            <Button onClick={handleCloseDialog}>
              我已保存，关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 当前 Token */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">当前 Access Token</h3>
        <div className="space-y-3 sm:space-y-4">
          <div className="space-y-2">
            <Label htmlFor="current-token" className="text-sm">您的访问令牌</Label>
            <div className="flex flex-col sm:flex-row gap-2">
              <div className="relative flex-1">
                <Input
                  id="current-token"
                  type={showCurrentToken ? 'text' : 'password'}
                  value={currentToken || '••••••••••••••••••••••••••••••••'}
                  readOnly
                  className="pr-10 font-mono text-sm"
                  placeholder="Token 存储在安全 Cookie 中"
                />
                <button
                  onClick={() => {
                    if (currentToken) {
                      setShowCurrentToken(!showCurrentToken)
                    } else {
                      toast({
                        title: '无法查看',
                        description: 'Token 存储在安全 Cookie 中，如需新 Token 请点击"重新生成"',
                      })
                    }
                  }}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 hover:bg-accent rounded"
                  title={showCurrentToken ? '隐藏' : '显示'}
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
                  title="复制到剪贴板"
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
                      <span className="hidden sm:inline">重新生成</span>
                      <span className="sm:hidden">生成</span>
                    </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>确认重新生成 Token</AlertDialogTitle>
                    <AlertDialogDescription>
                      这将生成一个新的 64 位安全令牌，并使当前 Token 立即失效。
                      您需要使用新 Token 重新登录系统。此操作不可撤销，确定要继续吗？
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>取消</AlertDialogCancel>
                    <AlertDialogAction onClick={executeRegenerateToken}>
                      确认生成
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
              </div>
            </div>
            <p className="text-[10px] sm:text-xs text-muted-foreground">
              请妥善保管您的 Access Token，不要泄露给他人
            </p>
          </div>
        </div>
      </div>

      {/* 更新 Token */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">自定义 Access Token</h3>
        <div className="space-y-3 sm:space-y-4">
          <div className="space-y-2">
            <Label htmlFor="new-token" className="text-sm">新的访问令牌</Label>
            <div className="relative">
              <Input
                id="new-token"
                type={showNewToken ? 'text' : 'password'}
                value={newToken}
                onChange={(e) => setNewToken(e.target.value)}
                className="pr-10 font-mono text-sm"
                placeholder="输入自定义 Token"
              />
              <button
                onClick={() => setShowNewToken(!showNewToken)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 hover:bg-accent rounded"
                title={showNewToken ? '隐藏' : '显示'}
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
                <p className="text-sm font-medium text-foreground">Token 安全要求:</p>
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
                      <span className="font-medium">Token 格式正确，可以使用</span>
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
            {isUpdating ? '更新中...' : '更新自定义 Token'}
          </Button>
        </div>
      </div>

      {/* 安全提示 */}
      <div className="rounded-lg border border-yellow-200 dark:border-yellow-900 bg-yellow-50 dark:bg-yellow-950/30 p-3 sm:p-4">
        <h4 className="text-sm sm:text-base font-semibold text-yellow-900 dark:text-yellow-200 mb-2">安全提示</h4>
        <ul className="text-xs sm:text-sm text-yellow-800 dark:text-yellow-300 space-y-1 list-disc list-inside">
          <li>重新生成 Token 会创建系统随机生成的 64 位安全令牌</li>
          <li>自定义 Token 必须满足所有安全要求才能使用</li>
          <li>更新 Token 后，旧的 Token 将立即失效</li>
          <li>请在安全的环境下查看和复制 Token</li>
          <li>如果怀疑 Token 泄露，请立即重新生成或更新</li>
          <li>建议使用系统生成的 Token 以获得最高安全性</li>
        </ul>
      </div>
    </div>
  )
}

// 其他设置标签页
function OtherTab() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [isResetting, setIsResetting] = useState(false)
  const [shouldThrowError, setShouldThrowError] = useState(false)
  
  // 性能与存储设置状态
  const [logCacheSize, setLogCacheSize] = useState(() => getSetting('logCacheSize'))
  const [wsReconnectInterval, setWsReconnectInterval] = useState(() => getSetting('wsReconnectInterval'))
  const [wsMaxReconnectAttempts, setWsMaxReconnectAttempts] = useState(() => getSetting('wsMaxReconnectAttempts'))
  const [dataSyncInterval, setDataSyncInterval] = useState(() => getSetting('dataSyncInterval'))
  const [storageUsage, setStorageUsage] = useState(() => getStorageUsage())
  
  // 导入/导出状态
  const [isExporting, setIsExporting] = useState(false)
  const [isImporting, setIsImporting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 手动触发 React 错误
  if (shouldThrowError) {
    throw new Error('这是一个手动触发的测试错误，用于验证错误边界组件是否正常工作。')
  }

  // 刷新存储使用情况
  const refreshStorageUsage = () => {
    setStorageUsage(getStorageUsage())
  }

  // 处理日志缓存大小变更
  const handleLogCacheSizeChange = (value: number[]) => {
    const size = value[0]
    setLogCacheSize(size)
    setSetting('logCacheSize', size)
  }

  // 处理 WebSocket 重连间隔变更
  const handleWsReconnectIntervalChange = (value: number[]) => {
    const interval = value[0]
    setWsReconnectInterval(interval)
    setSetting('wsReconnectInterval', interval)
  }

  // 处理 WebSocket 最大重连次数变更
  const handleWsMaxReconnectAttemptsChange = (value: number[]) => {
    const attempts = value[0]
    setWsMaxReconnectAttempts(attempts)
    setSetting('wsMaxReconnectAttempts', attempts)
  }

  // 处理数据同步间隔变更
  const handleDataSyncIntervalChange = (value: number[]) => {
    const interval = value[0]
    setDataSyncInterval(interval)
    setSetting('dataSyncInterval', interval)
  }

  // 清除日志缓存
  const handleClearLogCache = () => {
    logWebSocket.clearLogs()
    toast({
      title: '日志已清除',
      description: '日志缓存已清空',
    })
  }

  // 清除本地缓存
  const handleClearLocalCache = () => {
    const result = clearLocalCache()
    refreshStorageUsage()
    toast({
      title: '缓存已清除',
      description: `已清除 ${result.clearedKeys.length} 项缓存数据`,
    })
  }

  // 导出设置
  const handleExportSettings = () => {
    setIsExporting(true)
    try {
      const settings = exportSettings()
      const dataStr = JSON.stringify(settings, null, 2)
      const blob = new Blob([dataStr], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `maibot-webui-settings-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      toast({
        title: '导出成功',
        description: '设置已导出为 JSON 文件',
      })
    } catch (error) {
      console.error('导出设置失败:', error)
      toast({
        title: '导出失败',
        description: '无法导出设置',
        variant: 'destructive',
      })
    } finally {
      setIsExporting(false)
    }
  }

  // 导入设置
  const handleImportSettings = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    setIsImporting(true)
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const content = e.target?.result as string
        const settings = JSON.parse(content)
        const result = importSettings(settings)
        
        if (result.success) {
          // 刷新页面状态
          setLogCacheSize(getSetting('logCacheSize'))
          setWsReconnectInterval(getSetting('wsReconnectInterval'))
          setWsMaxReconnectAttempts(getSetting('wsMaxReconnectAttempts'))
          setDataSyncInterval(getSetting('dataSyncInterval'))
          refreshStorageUsage()
          
          toast({
            title: '导入成功',
            description: `成功导入 ${result.imported.length} 项设置${result.skipped.length > 0 ? `，跳过 ${result.skipped.length} 项` : ''}`,
          })
          
          // 提示用户刷新页面以应用所有更改
          if (result.imported.includes('theme') || result.imported.includes('accentColor')) {
            toast({
              title: '提示',
              description: '部分设置需要刷新页面才能完全生效',
            })
          }
        } else {
          toast({
            title: '导入失败',
            description: '没有有效的设置项可导入',
            variant: 'destructive',
          })
        }
      } catch (error) {
        console.error('导入设置失败:', error)
        toast({
          title: '导入失败',
          description: '文件格式无效',
          variant: 'destructive',
        })
      } finally {
        setIsImporting(false)
        // 清空 input，允许重复选择同一文件
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
      }
    }
    reader.readAsText(file)
  }

  // 重置所有设置
  const handleResetAllSettings = () => {
    resetAllSettings()
    // 刷新页面状态
    setLogCacheSize(DEFAULT_SETTINGS.logCacheSize)
    setWsReconnectInterval(DEFAULT_SETTINGS.wsReconnectInterval)
    setWsMaxReconnectAttempts(DEFAULT_SETTINGS.wsMaxReconnectAttempts)
    setDataSyncInterval(DEFAULT_SETTINGS.dataSyncInterval)
    refreshStorageUsage()
    toast({
      title: '已重置',
      description: '所有设置已恢复为默认值，刷新页面以应用更改',
    })
  }

  const handleResetSetup = async () => {
    setIsResetting(true)

    try {
      // 调用后端API重置首次配置状态
      const response = await fetchWithAuth('/api/webui/setup/reset', {
        method: 'POST',
      })

      const data = await response.json()

      if (response.ok && data.success) {
        toast({
          title: '重置成功',
          description: '即将进入初次配置向导',
        })

        // 延迟跳转到配置向导
        setTimeout(() => {
          navigate({ to: '/setup' })
        }, 1000)
      } else {
        toast({
          title: '重置失败',
          description: data.message || '无法重置配置状态',
          variant: 'destructive',
        })
      }
    } catch (error) {
      console.error('重置配置状态错误:', error)
      toast({
        title: '重置失败',
        description: '连接服务器失败',
        variant: 'destructive',
      })
    } finally {
      setIsResetting(false)
    }
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* 性能与存储 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4 flex items-center gap-2">
          <Database className="h-5 w-5" />
          性能与存储
        </h3>
        <div className="space-y-4 sm:space-y-5">
          {/* 存储使用情况 */}
          <div className="rounded-lg bg-muted/50 p-3 sm:p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium flex items-center gap-2">
                <HardDrive className="h-4 w-4" />
                本地存储使用
              </span>
              <Button variant="ghost" size="sm" onClick={refreshStorageUsage} className="h-7 px-2">
                <RefreshCw className="h-3 w-3" />
              </Button>
            </div>
            <div className="text-2xl font-bold text-primary">{formatBytes(storageUsage.used)}</div>
            <p className="text-xs text-muted-foreground mt-1">{storageUsage.items} 个存储项</p>
          </div>

          {/* 日志缓存大小 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">日志缓存大小</Label>
              <span className="text-sm text-muted-foreground">{logCacheSize} 条</span>
            </div>
            <Slider
              value={[logCacheSize]}
              onValueChange={handleLogCacheSizeChange}
              min={100}
              max={5000}
              step={100}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              控制日志查看器最多缓存的日志条数，较大的值会占用更多内存
            </p>
          </div>

          {/* 数据刷新间隔 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">首页数据刷新间隔</Label>
              <span className="text-sm text-muted-foreground">{dataSyncInterval} 秒</span>
            </div>
            <Slider
              value={[dataSyncInterval]}
              onValueChange={handleDataSyncIntervalChange}
              min={10}
              max={120}
              step={5}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              控制首页统计数据的自动刷新间隔
            </p>
          </div>

          {/* WebSocket 重连间隔 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">WebSocket 重连间隔</Label>
              <span className="text-sm text-muted-foreground">{wsReconnectInterval / 1000} 秒</span>
            </div>
            <Slider
              value={[wsReconnectInterval]}
              onValueChange={handleWsReconnectIntervalChange}
              min={1000}
              max={10000}
              step={500}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              日志 WebSocket 连接断开后的重连基础间隔
            </p>
          </div>

          {/* WebSocket 最大重连次数 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">WebSocket 最大重连次数</Label>
              <span className="text-sm text-muted-foreground">{wsMaxReconnectAttempts} 次</span>
            </div>
            <Slider
              value={[wsMaxReconnectAttempts]}
              onValueChange={handleWsMaxReconnectAttemptsChange}
              min={3}
              max={30}
              step={1}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              连接失败后的最大重连尝试次数
            </p>
          </div>

          {/* 清理按钮 */}
          <div className="flex flex-wrap gap-2 pt-2">
            <Button variant="outline" size="sm" onClick={handleClearLogCache} className="gap-2">
              <Trash2 className="h-4 w-4" />
              清除日志缓存
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2">
                  <Trash2 className="h-4 w-4" />
                  清除本地缓存
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>确认清除本地缓存</AlertDialogTitle>
                  <AlertDialogDescription>
                    这将清除所有本地缓存的设置和数据（不包括登录凭证）。
                    您可能需要重新配置部分偏好设置。确定要继续吗？
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction onClick={handleClearLocalCache}>
                    确认清除
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      </div>

      {/* 导入/导出设置 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4 flex items-center gap-2">
          <Download className="h-5 w-5" />
          导入/导出设置
        </h3>
        <div className="space-y-4">
          <p className="text-xs sm:text-sm text-muted-foreground">
            导出当前的界面设置以便备份，或从之前导出的文件中恢复设置。
          </p>
          
          <div className="flex flex-wrap gap-2">
            <Button 
              variant="outline" 
              onClick={handleExportSettings} 
              disabled={isExporting}
              className="gap-2"
            >
              <Download className="h-4 w-4" />
              {isExporting ? '导出中...' : '导出设置'}
            </Button>
            
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleImportSettings}
              className="hidden"
            />
            <Button 
              variant="outline" 
              onClick={() => fileInputRef.current?.click()}
              disabled={isImporting}
              className="gap-2"
            >
              <Upload className="h-4 w-4" />
              {isImporting ? '导入中...' : '导入设置'}
            </Button>
          </div>

          {/* 重置所有设置 */}
          <div className="pt-2 border-t">
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2 text-destructive hover:text-destructive">
                  <RotateCcw className="h-4 w-4" />
                  重置所有设置为默认值
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>确认重置所有设置</AlertDialogTitle>
                  <AlertDialogDescription>
                    这将把所有界面设置恢复为默认值，包括主题、颜色、动画等偏好设置。
                    此操作不会影响您的登录状态。确定要继续吗？
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction onClick={handleResetAllSettings}>
                    确认重置
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      </div>

      {/* 配置向导 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">配置向导</h3>
        <div className="space-y-3 sm:space-y-4">
          <div className="space-y-2">
            <p className="text-xs sm:text-sm text-muted-foreground">
              重新进行初次配置向导，可以帮助您重新设置系统的基础配置。
            </p>
          </div>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline" disabled={isResetting} className="gap-2">
                <RotateCcw className={cn('h-4 w-4', isResetting && 'animate-spin')} />
                重新进行初次配置
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认重新配置</AlertDialogTitle>
                <AlertDialogDescription>
                  这将带您重新进入初次配置向导。您可以重新设置系统的基础配置项。确定要继续吗？
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction onClick={handleResetSetup}>
                  确认重置
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      {/* 开发者工具 */}
      <div className="rounded-lg border border-dashed border-yellow-500/50 bg-yellow-500/5 p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4 flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-yellow-500" />
          开发者工具
        </h3>
        <div className="space-y-3 sm:space-y-4">
          <div className="space-y-2">
            <p className="text-xs sm:text-sm text-muted-foreground">
              以下功能仅供开发调试使用，可能会导致页面崩溃或异常。
            </p>
          </div>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="destructive" className="gap-2">
                <AlertTriangle className="h-4 w-4" />
                触发测试错误
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认触发错误</AlertDialogTitle>
                <AlertDialogDescription>
                  这将手动触发一个 React 错误，用于测试错误边界组件的显示效果。
                  页面将显示错误界面，您可以通过刷新页面或点击返回首页来恢复。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction 
                  onClick={() => setShouldThrowError(true)}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                >
                  确认触发
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>
    </div>
  )
}

// 关于标签页
function AboutTab() {
  return (
    <div className="space-y-4 sm:space-y-6">
      {/* GitHub 开源地址 */}
      <div className="rounded-lg border-2 border-primary/30 bg-gradient-to-r from-primary/5 to-primary/10 p-4 sm:p-6">
        <div className="flex items-start gap-3 sm:gap-4">
          <div className="flex-shrink-0 rounded-lg bg-primary/10 p-2 sm:p-3">
            <svg
              className="h-6 w-6 sm:h-8 sm:w-8 text-primary"
              fill="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path
                fillRule="evenodd"
                d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                clipRule="evenodd"
              />
            </svg>
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-lg sm:text-xl font-bold text-foreground mb-2">
              开源项目
            </h3>
            <p className="text-sm sm:text-base text-muted-foreground mb-3">
              本项目在 GitHub 开源，欢迎 Star ⭐ 支持！
            </p>
            <a
              href="https://github.com/Mai-with-u/MaiBot-Dashboard"
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                "inline-flex items-center gap-2 px-4 py-2 rounded-lg",
                "bg-primary text-primary-foreground font-medium text-sm",
                "hover:bg-primary/90 transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              )}
            >
              <svg
                className="h-4 w-4"
                fill="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path
                  fillRule="evenodd"
                  d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                  clipRule="evenodd"
                />
              </svg>
              前往 GitHub
              <svg
                className="h-4 w-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                />
              </svg>
            </a>
          </div>
        </div>
      </div>

      {/* 应用信息 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">关于 {APP_NAME}</h3>
        <div className="space-y-2 text-xs sm:text-sm text-muted-foreground">
          <p>版本: {APP_VERSION}</p>
          <p>麦麦（MaiBot）的现代化 Web 管理界面</p>
        </div>
      </div>

      {/* 作者信息 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">作者</h3>
        <div className="space-y-3">
          <div className="space-y-1">
            <p className="text-sm font-medium">MaiBot 核心</p>
            <p className="text-xs sm:text-sm text-muted-foreground">Mai-with-u</p>
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium">WebUI</p>
            <p className="text-xs sm:text-sm text-muted-foreground">Mai-with-u <a href="https://github.com/DrSmoothl" target="_blank" rel="noopener noreferrer" className="text-primary underline">@MotricSeven</a></p>
          </div>
        </div>
      </div>

      {/* 技术栈 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">技术栈</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs sm:text-sm text-muted-foreground">
          <div className="space-y-1.5">
            <p className="font-medium text-foreground">前端框架</p>
            <ul className="space-y-0.5 list-disc list-inside">
              <li>React 19.2.0</li>
              <li>TypeScript 5.7.2</li>
              <li>Vite 6.0.7</li>
              <li>TanStack Router 1.94.2</li>
            </ul>
          </div>
          <div className="space-y-1.5">
            <p className="font-medium text-foreground">UI 组件</p>
            <ul className="space-y-0.5 list-disc list-inside">
              <li>shadcn/ui</li>
              <li>Radix UI</li>
              <li>Tailwind CSS 3.4.17</li>
              <li>Lucide Icons</li>
            </ul>
          </div>
          <div className="space-y-1.5">
            <p className="font-medium text-foreground">后端</p>
            <ul className="space-y-0.5 list-disc list-inside">
              <li>Python 3.12+</li>
              <li>FastAPI</li>
              <li>Uvicorn</li>
              <li>WebSocket</li>
            </ul>
          </div>
          <div className="space-y-1.5">
            <p className="font-medium text-foreground">构建工具</p>
            <ul className="space-y-0.5 list-disc list-inside">
              <li>Bun / npm</li>
              <li>ESLint 9.17.0</li>
              <li>PostCSS</li>
            </ul>
          </div>
        </div>
      </div>

      {/* 开源感谢 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">开源库感谢</h3>
        <p className="text-xs sm:text-sm text-muted-foreground mb-3">
          本项目使用了以下优秀的开源库，感谢他们的贡献：
        </p>
        <ScrollArea className="h-[300px] sm:h-[400px]">
          <div className="space-y-4 pr-4">
            {/* UI 框架 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">UI 框架与组件</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="React" description="用户界面构建库" license="MIT" />
                <LibraryItem name="shadcn/ui" description="优雅的 React 组件库" license="MIT" />
                <LibraryItem name="Radix UI" description="无样式的可访问组件库" license="MIT" />
                <LibraryItem name="Tailwind CSS" description="实用优先的 CSS 框架" license="MIT" />
                <LibraryItem name="Lucide React" description="精美的图标库" license="ISC" />
              </div>
            </div>

            {/* 路由与状态 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">路由与状态管理</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="TanStack Router" description="类型安全的路由库" license="MIT" />
                <LibraryItem name="Zustand" description="轻量级状态管理" license="MIT" />
              </div>
            </div>

            {/* 表单与验证 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">表单处理</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="React Hook Form" description="高性能表单库" license="MIT" />
                <LibraryItem name="Zod" description="TypeScript 优先的 schema 验证" license="MIT" />
              </div>
            </div>

            {/* 工具库 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">工具库</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="clsx" description="条件 className 构建工具" license="MIT" />
                <LibraryItem name="tailwind-merge" description="Tailwind 类名合并工具" license="MIT" />
                <LibraryItem name="class-variance-authority" description="组件变体管理" license="Apache-2.0" />
                <LibraryItem name="date-fns" description="现代化日期处理库" license="MIT" />
              </div>
            </div>

            {/* 动画 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">动画效果</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="Framer Motion" description="React 动画库" license="MIT" />
                <LibraryItem name="vaul" description="抽屉组件动画" license="MIT" />
              </div>
            </div>

            {/* 后端相关 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">后端框架</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="FastAPI" description="现代化 Python Web 框架" license="MIT" />
                <LibraryItem name="Uvicorn" description="ASGI 服务器" license="BSD-3-Clause" />
                <LibraryItem name="Pydantic" description="数据验证库" license="MIT" />
                <LibraryItem name="python-multipart" description="文件上传支持" license="Apache-2.0" />
              </div>
            </div>

            {/* 开发工具 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">开发工具</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="TypeScript" description="JavaScript 的超集" license="Apache-2.0" />
                <LibraryItem name="Vite" description="下一代前端构建工具" license="MIT" />
                <LibraryItem name="ESLint" description="JavaScript 代码检查工具" license="MIT" />
                <LibraryItem name="PostCSS" description="CSS 转换工具" license="MIT" />
              </div>
            </div>
          </div>
        </ScrollArea>
      </div>

      {/* 许可证 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">开源许可</h3>
        <div className="space-y-3">
          <div className="rounded-lg bg-primary/5 border border-primary/20 p-3 sm:p-4">
            <div className="flex items-start gap-2 sm:gap-3">
              <div className="flex-shrink-0 mt-0.5">
                <div className="rounded-md bg-primary/10 px-2 py-1">
                  <span className="text-xs sm:text-sm font-bold text-primary">GPLv3</span>
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm sm:text-base font-semibold text-foreground mb-1">
                  MaiBot WebUI
                </p>
                <p className="text-xs sm:text-sm text-muted-foreground">
                  本项目采用 GNU General Public License v3.0 开源许可证。
                  您可以自由地使用、修改和分发本软件，但必须保持相同的开源许可。
                </p>
              </div>
            </div>
          </div>
          <p className="text-xs sm:text-sm text-muted-foreground">
            本项目依赖的所有开源库均遵循各自的开源许可证（MIT、Apache-2.0、BSD 等）。
            感谢所有开源贡献者的无私奉献。
          </p>
        </div>
      </div>
    </div>
  )
}

// 库信息组件
type LibraryItemProps = {
  name: string
  description: string
  license: string
}

function LibraryItem({ name, description, license }: LibraryItemProps) {
  return (
    <div className="flex items-start justify-between gap-2 rounded-lg border bg-muted/30 p-2.5 sm:p-3">
      <div className="flex-1 min-w-0">
        <p className="font-medium text-foreground truncate">{name}</p>
        <p className="text-muted-foreground text-xs mt-0.5">{description}</p>
      </div>
      <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary flex-shrink-0">
        {license}
      </span>
    </div>
  )
}

type ThemeOptionProps = {
  value: 'light' | 'dark' | 'system'
  current: 'light' | 'dark' | 'system'
  onChange: (theme: 'light' | 'dark' | 'system') => void
  label: string
  description: string
}

function ThemeOption({ value, current, onChange, label, description }: ThemeOptionProps) {
  const isSelected = current === value

  return (
    <button
      onClick={() => onChange(value)}
      className={cn(
        'relative rounded-lg border-2 p-3 sm:p-4 text-left transition-all',
        'hover:border-primary/50 hover:bg-accent/50',
        isSelected ? 'border-primary bg-accent' : 'border-border'
      )}
    >
      {/* 选中指示器 */}
      {isSelected && (
        <div className="absolute top-2 right-2 sm:top-3 sm:right-3 h-2 w-2 rounded-full bg-primary" />
      )}

      <div className="space-y-1">
        <div className="text-sm sm:text-base font-medium">{label}</div>
        <div className="text-[10px] sm:text-xs text-muted-foreground">{description}</div>
      </div>

      {/* 主题预览 */}
      <div className="mt-2 sm:mt-3 flex gap-1">
        {value === 'light' && (
          <>
            <div className="h-2 w-2 rounded-full bg-slate-200" />
            <div className="h-2 w-2 rounded-full bg-slate-300" />
            <div className="h-2 w-2 rounded-full bg-slate-400" />
          </>
        )}
        {value === 'dark' && (
          <>
            <div className="h-2 w-2 rounded-full bg-slate-700" />
            <div className="h-2 w-2 rounded-full bg-slate-800" />
            <div className="h-2 w-2 rounded-full bg-slate-900" />
          </>
        )}
        {value === 'system' && (
          <>
            <div className="h-2 w-2 rounded-full bg-gradient-to-r from-slate-200 to-slate-700" />
            <div className="h-2 w-2 rounded-full bg-gradient-to-r from-slate-300 to-slate-800" />
            <div className="h-2 w-2 rounded-full bg-gradient-to-r from-slate-400 to-slate-900" />
          </>
        )}
      </div>
    </button>
  )
}

type ColorPresetOptionProps = {
  value: string
  current: string
  onChange: (color: string) => void
  label: string
  colorClass: string
}

function ColorPresetOption({ value, current, onChange, label, colorClass }: ColorPresetOptionProps) {
  const isSelected = current === value

  return (
    <button
      onClick={() => onChange(value)}
      className={cn(
        'relative rounded-lg border-2 p-2 sm:p-3 text-left transition-all',
        'hover:border-primary/50 hover:bg-accent/50',
        isSelected ? 'border-primary bg-accent' : 'border-border'
      )}
    >
      {/* 选中指示器 */}
      {isSelected && (
        <div className="absolute top-1.5 right-1.5 sm:top-2 sm:right-2 h-1.5 w-1.5 sm:h-2 sm:w-2 rounded-full bg-primary" />
      )}

      <div className="flex flex-col items-center gap-1.5 sm:gap-2">
        <div className={cn('h-8 w-8 sm:h-10 sm:w-10 rounded-full', colorClass)} />
        <div className="text-[10px] sm:text-xs font-medium text-center">{label}</div>
      </div>
    </button>
  )
}
