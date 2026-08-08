import { createElement } from 'react'
import {
  Activity,
  Box,
  Brain,
  Database,
  FileText,
  HardDrive,
  Hash,
  Home,
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
const MonitorIcon = createStreamlineIcon('desktop-chat-remix', Activity)
const ChatManagementIcon = createStreamlineIcon('chat-two-bubbles-oval-remix', MessageSquare)
const BotConfigIcon = createStreamlineIcon('page-setting-remix', Settings)
const ModelIcon = createStreamlineIcon('module-remix', Box)
const PromptIcon = createStreamlineIcon('script-1-remix', FileText)
const EmojiIcon = createStreamlineIcon('happy-face-remix', Smile)
const ExpressionIcon = createStreamlineIcon('chat-bubble-square-write-remix', MessageSquare)
const JargonIcon = createStreamlineIcon('sign-hashtag-solid', Hash)
const BehaviorIcon = createStreamlineIcon('cyborg-solid', Brain)
const KnowledgeIcon = createStreamlineIcon('user-sticker-square-remix', Database)
const PluginConfigIcon = createStreamlineIcon('application-add-remix', Puzzle)
const AdapterManagementIcon = createStreamlineIcon('router-wifi-network-solid', Wifi)
const PluginMarketIcon = createStreamlineIcon('store-2-solid', Store)
const McpIcon = createStreamlineIcon('router-wifi-network-solid', Wifi)
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
      { icon: MonitorIcon, label: 'sidebar.menu.maisakaMonitor', path: '/planner-monitor' },
      { icon: ChatManagementIcon, label: 'sidebar.menu.chatManagement', path: '/chat-management' },
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
      { icon: PromptIcon, label: 'sidebar.menu.promptManagement', path: '/config/prompts' },
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
        icon: BehaviorIcon,
        label: 'sidebar.menu.behaviorLearning',
        path: '/resource/behavior',
        searchDescription: 'search.items.behaviorLearningDesc',
        featureFlag: 'behaviorLearning',
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
      {
        icon: AdapterManagementIcon,
        label: 'sidebar.menu.adapterManagement',
        path: '/adapter-management',
      },
      { icon: PluginConfigIcon, label: 'sidebar.menu.pluginConfig', path: '/plugin-config' },
      {
        icon: PluginMarketIcon,
        label: 'sidebar.menu.pluginMarket',
        path: '/plugins',
        searchDescription: 'search.items.pluginsDesc',
      },
      {
        icon: DataTransferIcon,
        label: 'sidebar.menu.dataTransfer',
        path: '/data-transfer',
        searchDescription: 'search.items.dataTransferDesc',
      },
      { icon: McpIcon, label: 'sidebar.menu.mcpSettings', path: '/mcp-settings' },
      {
        icon: ReplyEffectsIcon,
        label: 'sidebar.menu.replyEffects',
        path: '/reply-effects',
      },
    ],
  },
]
