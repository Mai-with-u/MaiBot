import { useState } from 'react'
import {
  ArrowRight,
  Bot,
  CheckCircle2,
  Loader2,
  XCircle,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { isElectron } from '@/lib/runtime'

interface BackendSetupWizardProps {
  open: boolean
}

type TestStatus = 'idle' | 'loading' | 'success' | 'error'

/**
 * First-launch backend setup wizard for Electron environment.
 * Full-screen modal that guides users to configure their first backend connection.
 * Cannot be dismissed until configuration is complete.
 */
export function BackendSetupWizard({ open }: BackendSetupWizardProps) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [testStatus, setTestStatus] = useState<TestStatus>('idle')
  const [testError, setTestError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Validation errors
  const [nameError, setNameError] = useState('')
  const [urlError, setUrlError] = useState('')

  // Only render in Electron environment
  if (!isElectron()) {
    return null
  }

  if (!open) {
    return null
  }

  const validateName = (value: string): boolean => {
    if (!value.trim()) {
      setNameError('后端名称不能为空')
      return false
    }
    setNameError('')
    return true
  }

  const validateUrl = (value: string): boolean => {
    if (!value.trim()) {
      setUrlError('后端地址不能为空')
      return false
    }
    if (!/^https?:\/\/.+/.test(value)) {
      setUrlError('地址必须以 http:// 或 https:// 开头')
      return false
    }
    if (value.endsWith('/')) {
      setUrlError('地址末尾不能包含 /')
      return false
    }
    setUrlError('')
    return true
  }

  const handleTestConnection = async () => {
    if (!validateUrl(url)) return

    setTestStatus('loading')
    setTestError('')

    try {
      const response = await fetch(`${url}/api/webui/system/health`, {
        method: 'GET',
        signal: AbortSignal.timeout(10000),
      })
      if (response.ok) {
        setTestStatus('success')
      } else {
        setTestStatus('error')
        setTestError(`服务器返回状态码 ${response.status}`)
      }
    } catch (err) {
      setTestStatus('error')
      if (err instanceof DOMException && err.name === 'TimeoutError') {
        setTestError('连接超时，请检查地址是否正确')
      } else if (err instanceof TypeError) {
        setTestError('无法连接到服务器，请检查地址和网络')
      } else {
        setTestError(err instanceof Error ? err.message : '未知错误')
      }
    }
  }

  const handleFinish = async () => {
    const isNameValid = validateName(name)
    const isUrlValid = validateUrl(url)
    if (!isNameValid || !isUrlValid) return

    setIsSubmitting(true)
    try {
      const newBackend = await window.electronAPI!.addBackend({
        name: name.trim(),
        url: url.trim(),
        isDefault: true,
      })
      await window.electronAPI!.setActiveBackend(newBackend.id)
      await window.electronAPI!.markFirstLaunchComplete()
      window.location.reload()
    } catch (err) {
      setIsSubmitting(false)
      setTestStatus('error')
      setTestError(
        err instanceof Error ? err.message : '保存配置失败，请重试'
      )
    }
  }

  const isFormValid = name.trim() !== '' && /^https?:\/\/.+/.test(url) && !url.endsWith('/')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background">
      {/* Background decoration */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute left-1/4 top-1/4 h-64 w-64 md:h-96 md:w-96 rounded-full bg-primary/5 blur-3xl" />
        <div className="absolute right-1/4 bottom-1/4 h-64 w-64 md:h-96 md:w-96 rounded-full bg-secondary/5 blur-3xl" />
      </div>

      <Card className="relative z-10 max-w-md w-full mx-4 shadow-lg">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10">
            <Bot className="h-6 w-6 text-primary" />
          </div>
          <CardTitle className="text-2xl">欢迎使用 MaiBot</CardTitle>
          <CardDescription>
            配置您的第一个后端连接以开始使用
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          {/* Backend name field */}
          <div className="space-y-2">
            <Label htmlFor="backend-name">
              后端名称 <span className="text-destructive">*</span>
            </Label>
            <Input
              id="backend-name"
              placeholder="例如：本地服务器"
              value={name}
              onChange={(e) => {
                setName(e.target.value)
                if (nameError) validateName(e.target.value)
              }}
              onBlur={() => validateName(name)}
            />
            {nameError && (
              <p className="text-sm text-destructive">{nameError}</p>
            )}
          </div>

          {/* Backend URL field */}
          <div className="space-y-2">
            <Label htmlFor="backend-url">
              后端地址 <span className="text-destructive">*</span>
            </Label>
            <Input
              id="backend-url"
              placeholder="例如：http://192.168.1.100:8001"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value)
                if (urlError) validateUrl(e.target.value)
                // Reset test status when URL changes
                if (testStatus !== 'idle') {
                  setTestStatus('idle')
                  setTestError('')
                }
              }}
              onBlur={() => validateUrl(url)}
            />
            {urlError && (
              <p className="text-sm text-destructive">{urlError}</p>
            )}
          </div>

          {/* Test connection */}
          <div className="space-y-2">
            <Button
              type="button"
              variant="outline"
              onClick={handleTestConnection}
              disabled={testStatus === 'loading' || !url.trim()}
              className="w-full"
            >
              {testStatus === 'loading' ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  测试连接中...
                </>
              ) : (
                '测试连接'
              )}
            </Button>

            {testStatus === 'success' && (
              <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
                <CheckCircle2 className="h-4 w-4" />
                连接成功
              </div>
            )}

            {testStatus === 'error' && (
              <div className="flex items-start gap-2 text-sm text-destructive">
                <XCircle className="h-4 w-4 mt-0.5 shrink-0" />
                <span>{testError || '无法连接'}</span>
              </div>
            )}
          </div>

          {/* Submit button */}
          <Button
            onClick={handleFinish}
            disabled={!isFormValid || isSubmitting}
            className="w-full"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                配置中...
              </>
            ) : (
              <>
                开始使用
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
