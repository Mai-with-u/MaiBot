import { BarChart3, BrainCircuit, ScrollText, Settings } from 'lucide-react'

import type { MenuSection } from './types'

/**
 * 侧栏菜单之外、手机「管理」页额外列出的分组：
 * 日志、推理过程与详细统计在桌面端位于顶栏的日志工作区，手机上没有这个切换器；
 * WebUI 设置在桌面端位于顶栏右侧。
 */
export const MOBILE_EXTRA_MANAGE_SECTIONS: MenuSection[] = [
  {
    title: 'manage.groups.runtime',
    items: [
      {
        icon: (props) => <ScrollText strokeWidth={1.7} {...props} />,
        label: 'sidebar.menu.logViewer',
        path: '/logs',
      },
      {
        icon: (props) => <BrainCircuit strokeWidth={1.7} {...props} />,
        label: 'sidebar.menu.reasoningProcess',
        path: '/reasoning-process',
      },
      {
        icon: (props) => <BarChart3 strokeWidth={1.7} {...props} />,
        label: 'sidebar.menu.statistics',
        path: '/statistics',
      },
    ],
  },
  {
    title: 'sidebar.groups.system',
    items: [
      {
        icon: (props) => <Settings strokeWidth={1.7} {...props} />,
        label: 'sidebar.menu.settings',
        path: '/settings',
      },
    ],
  },
]
