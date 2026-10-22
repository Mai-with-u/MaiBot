import { useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'

// 旧设置页地址统一返回插件市场，设置由市场页弹窗承载。
export function PluginMirrorsPage({ embedded = false }: { embedded?: boolean }) {
  const navigate = useNavigate()
  useEffect(() => {
    void navigate({ to: embedded ? '/plugins/embed' : '/plugins', replace: true })
  }, [embedded, navigate])
  return null
}
