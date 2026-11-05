import { createContext, useContext, useEffect, useState } from 'react'

import { useMediaQuery } from '@/hooks/use-media-query'

import { getSetting, type Settings } from './settings-manager'

export type LayoutVersion = Settings['layoutVersion']

/** 新布局只接管桌面端；手机和窄屏继续使用经典布局的底部标签栏。 */
const OBSERVATION_LAYOUT_MEDIA_QUERY = '(min-width: 1024px)'

/** 读取 WebUI 设置里的布局版本，并跟随设置页的修改实时切换。 */
export function useLayoutVersionSetting(): LayoutVersion {
  const [version, setVersion] = useState<LayoutVersion>(() => getSetting('layoutVersion'))

  useEffect(() => {
    const refresh = () => setVersion(getSetting('layoutVersion'))
    window.addEventListener('maibot-settings-change', refresh)
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener('maibot-settings-change', refresh)
      window.removeEventListener('storage', refresh)
    }
  }, [])

  return version
}

/** 当前实际生效的外壳：设置为新布局且处于桌面宽度时才是 observation。 */
export function useEffectiveLayoutVersion(): LayoutVersion {
  const setting = useLayoutVersionSetting()
  const isDesktop = useMediaQuery(OBSERVATION_LAYOUT_MEDIA_QUERY)
  return setting === 'observation' && isDesktop ? 'observation' : 'classic'
}

/** 外壳把自己的版本告诉页面，页面据此隐藏外壳已经提供的部件（如聊天页自带的会话列表）。 */
export const LayoutVersionContext = createContext<LayoutVersion>('classic')

export function useLayoutVersion(): LayoutVersion {
  return useContext(LayoutVersionContext)
}
