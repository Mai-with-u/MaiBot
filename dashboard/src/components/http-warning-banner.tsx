import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, X } from 'lucide-react'
import { Button } from '@/components/ui/button'

/**
 * HTTP 警告横幅组件
 * 当用户通过 HTTP 访问时显示安全警告
 */
export function HttpWarningBanner() {
  const { t } = useTranslation()
  // 直接计算初始状态，避免 effect 中调用 setState
  const isHttp = window.location.protocol === 'http:'
  const hostname = window.location.hostname.toLowerCase()
  const isLocalhost = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
  const dismissed = sessionStorage.getItem('http-warning-dismissed') === 'true'
  
  // 本地访问（localhost/127.0.0.1）不显示警告
  const [isVisible, setIsVisible] = useState(isHttp && !isLocalhost && !dismissed)
  const [isDismissed, setIsDismissed] = useState(false)

  const handleDismiss = () => {
    setIsDismissed(true)
    setIsVisible(false)
    sessionStorage.setItem('http-warning-dismissed', 'true')
  }

  if (!isVisible || isDismissed) {
    return null
  }

  return (
    <div className="relative bg-amber-500/10 border-b border-amber-500/20 backdrop-blur-sm">
      <div className="container mx-auto px-4 py-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 flex-1">
            <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-500 flex-shrink-0" />
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
                <span className="font-semibold">{t('httpWarning.title')}</span>
                {t('httpWarning.message')}
              </p>
              <p className="text-xs text-amber-800 dark:text-amber-200 mt-1">
                {t('httpWarning.description')}
              </p>
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleDismiss}
            className="h-8 w-8 text-amber-700 hover:text-amber-900 dark:text-amber-400 dark:hover:text-amber-200 flex-shrink-0"
            aria-label={t('httpWarning.dismiss')}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
