import { createElement } from 'react'
import {
  Box,
  Database,
  FileText,
  HardDrive,
  Home,
  MessageCircle,
  MessageSquare,
  Network,
  Puzzle,
  Settings,
  Store,
  Wifi,
} from 'lucide-react'

import { createStreamlineIcon } from '@/components/ui/streamline-menu-icon'

import type { MenuIcon, MenuSection } from './types'

const HomeIcon = createStreamlineIcon('allergens-fish-remix', Home)
const LocalChatIcon = createStreamlineIcon('desktop-chat-remix', MessageCircle)
const BotConfigIcon = createStreamlineIcon('page-setting-remix', Settings)
const ModelIcon = createStreamlineIcon('module-remix', Box)
const PromptIcon = createStreamlineIcon('script-1-remix', FileText)
const LearningIcon = createStreamlineIcon('chat-bubble-square-write-remix', MessageSquare)
const KnowledgeIcon = createStreamlineIcon('user-sticker-square-remix', Database)
const PluginConfigIcon = createStreamlineIcon('application-add-remix', Puzzle)
const AdapterManagementIcon = createStreamlineIcon('router-wifi-network-solid', Wifi)
const PluginMarketIcon = createStreamlineIcon('store-2-solid', Store)
const DataTransferIcon: MenuIcon = (props) => createElement(HardDrive, props)
const DesktopPetIcon: MenuIcon = ({ size = 20, ...props }) =>
  createElement(
    'svg',
    {
      ...props,
      width: size,
      height: size,
      viewBox: '0 0 24 24',
      overflow: 'visible',
      fill: 'none',
      stroke: 'currentColor',
      strokeWidth: 1.7,
      'aria-hidden': true,
    },
    createElement('circle', { cx: 3.5, cy: 12, r: 4.9 }),
    createElement('circle', { cx: 3.5, cy: 12, r: 2 }),
    createElement('path', { d: 'M9.5 9h5l-2.5 6.5z', strokeLinejoin: 'round' }),
    createElement('circle', { cx: 20.5, cy: 12, r: 4.9 }),
    createElement('circle', { cx: 20.5, cy: 12, r: 2 })
  )

export const menuSections: MenuSection[] = [
  {
    title: 'sidebar.groups.overview',
    items: [
      {
        icon: HomeIcon,
        label: 'sidebar.menu.home',
        path: '/',
        searchDescription: 'search.items.homeDesc',
      },
      {
        icon: LocalChatIcon,
        label: 'workspace.chat',
        path: '/chat',
        searchDescription: 'search.items.chatDesc',
      },
    ],
  },
  {
    title: 'sidebar.groups.botConfig',
    items: [
      {
        icon: BotConfigIcon,
        label: 'sidebar.menu.botMainConfig',
        path: '/config/bot',
        searchDescription: 'search.items.botConfigDesc',
      },
      {
        icon: DesktopPetIcon,
        label: 'sidebar.menu.desktopPet',
        path: '/desktop-pet',
        featureFlag: 'desktopPet',
      },
      {
        icon: ModelIcon,
        label: 'sidebar.menu.modelManagement',
        path: '/config/model',
        searchDescription: 'search.items.modelDesc',
        tourId: 'sidebar-model-management',
      },
      {
        icon: AdapterManagementIcon,
        label: 'sidebar.menu.adapterManagement',
        path: '/adapter-management',
      },
    ],
  },
  {
    title: 'sidebar.groups.botResources',
    items: [
      {
        icon: LearningIcon,
        label: 'sidebar.menu.learning',
        path: '/resource/learning',
        searchDescription: 'search.items.learningDesc',
      },
      {
        icon: (props) => createElement(Network, props),
        label: 'sidebar.menu.mutualGroups',
        path: '/chat-management',
      },
      {
        icon: KnowledgeIcon,
        label: 'sidebar.menu.knowledgeBase',
        path: '/resource/knowledge-base',
      },
    ],
  },
  {
    title: 'sidebar.groups.extensionsMonitor',
    items: [
      { icon: PluginConfigIcon, label: 'sidebar.menu.pluginConfig', path: '/plugin-config' },
      {
        icon: PluginMarketIcon,
        label: 'sidebar.menu.pluginMarket',
        path: '/plugins',
        searchDescription: 'search.items.pluginsDesc',
      },
    ],
  },
  {
    title: 'sidebar.groups.advancedTools',
    items: [
      { icon: PromptIcon, label: 'sidebar.menu.promptManagement', path: '/config/prompts' },
      {
        icon: DataTransferIcon,
        label: 'sidebar.menu.dataTransfer',
        path: '/data-transfer',
        searchDescription: 'search.items.dataTransferDesc',
      },
    ],
  },
]
