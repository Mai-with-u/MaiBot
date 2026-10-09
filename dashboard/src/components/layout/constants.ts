import { createElement } from 'react'
import {
  Activity,
  Box,
  Database,
  FileText,
  HardDrive,
  Hash,
  Home,
  MessageCircle,
  MessageSquare,
  Puzzle,
  Settings,
  Smile,
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
const EmojiIcon = createStreamlineIcon('happy-face-remix', Smile)
const ExpressionIcon = createStreamlineIcon('chat-bubble-square-write-remix', MessageSquare)
const JargonIcon = createStreamlineIcon('sign-hashtag-solid', Hash)
const KnowledgeIcon = createStreamlineIcon('user-sticker-square-remix', Database)
const PluginConfigIcon = createStreamlineIcon('application-add-remix', Puzzle)
const AdapterManagementIcon = createStreamlineIcon('router-wifi-network-solid', Wifi)
const PluginMarketIcon = createStreamlineIcon('store-2-solid', Store)
const DataTransferIcon: MenuIcon = (props) => createElement(HardDrive, props)
const ReplyEffectsIcon: MenuIcon = (props) => createElement(Activity, props)

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
      { icon: LocalChatIcon, label: 'workspace.chat', path: '/chat' },
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
        icon: EmojiIcon,
        label: 'sidebar.menu.emojiManagement',
        path: '/resource/emoji',
        searchDescription: 'search.items.emojiDesc',
      },
      {
        icon: ExpressionIcon,
        label: 'sidebar.menu.expressionManagement',
        path: '/resource/expression',
        searchDescription: 'search.items.expressionDesc',
      },
      {
        icon: JargonIcon,
        label: 'sidebar.menu.slangManagement',
        path: '/resource/jargon',
        searchDescription: 'search.items.jargonDesc',
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
        icon: ReplyEffectsIcon,
        label: 'sidebar.menu.replyEffects',
        path: '/reply-effects',
        featureFlag: 'replyEffects',
      },
      {
        icon: DataTransferIcon,
        label: 'sidebar.menu.dataTransfer',
        path: '/data-transfer',
        searchDescription: 'search.items.dataTransferDesc',
      },
    ],
  },
]
