/**
 * 适配器配置类型定义
 */

import { Package, Container } from 'lucide-react'

/**
 * 完整的适配器配置接口
 */
export interface AdapterConfig {
  inner: {
    version: string
  }
  nickname: {
    nickname: string
  }
  napcat_server: {
    host: string
    port: number
    token: string
    heartbeat_interval: number
  }
  maibot_server: {
    host: string
    port: number
  }
  chat: {
    group_list_type: 'whitelist' | 'blacklist'
    group_list: number[]
    private_list_type: 'whitelist' | 'blacklist'
    private_list: number[]
    ban_user_id: number[]
    ban_qq_bot: boolean
    enable_poke: boolean
  }
  voice: {
    use_tts: boolean
  }
  forward: {
    image_threshold: number
  }
  debug: {
    level: string
  }
}

/**
 * 默认配置
 */
export const DEFAULT_CONFIG: AdapterConfig = {
  inner: {
    version: '0.1.2',
  },
  nickname: {
    nickname: '',
  },
  napcat_server: {
    host: 'localhost',
    port: 8095,
    token: '',
    heartbeat_interval: 30,
  },
  maibot_server: {
    host: 'localhost',
    port: 8000,
  },
  chat: {
    group_list_type: 'whitelist',
    group_list: [],
    private_list_type: 'whitelist',
    private_list: [],
    ban_user_id: [],
    ban_qq_bot: false,
    enable_poke: true,
  },
  voice: {
    use_tts: false,
  },
  forward: {
    image_threshold: 30,
  },
  debug: {
    level: 'INFO',
  },
}

/**
 * 预设配置定义
 */
export const PRESETS = {
  oneclick: {
    name: '一键包',
    description: '使用一键包部署的适配器配置',
    path: '../MaiBot-Napcat-Adapter/config.toml',
    icon: Package,
  },
  docker: {
    name: 'Docker',
    description: 'Docker Compose 部署的适配器配置',
    path: '/MaiMBot/adapters-config/config.toml',
    icon: Container,
  },
} as const

export type PresetKey = keyof typeof PRESETS
