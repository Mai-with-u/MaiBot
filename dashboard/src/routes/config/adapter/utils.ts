/**
 * 适配器配置 TOML 处理工具
 * 使用 smol-toml 库进行可靠的 TOML 解析和生成
 */

import { parse, stringify } from 'smol-toml'
import type { AdapterConfig } from './types'
import { DEFAULT_CONFIG } from './types'

/**
 * 解析 TOML 内容为配置对象
 * @param content TOML 格式的字符串
 * @returns 解析后的配置对象
 * @throws 如果 TOML 格式无效
 */
export function parseTOML(content: string): AdapterConfig {
  try {
    const parsed = parse(content) as unknown as AdapterConfig
    
    // 合并默认配置，确保所有必需字段都存在
    return {
      inner: { ...DEFAULT_CONFIG.inner, ...parsed.inner },
      nickname: { ...DEFAULT_CONFIG.nickname, ...parsed.nickname },
      napcat_server: { ...DEFAULT_CONFIG.napcat_server, ...parsed.napcat_server },
      maibot_server: { ...DEFAULT_CONFIG.maibot_server, ...parsed.maibot_server },
      chat: { ...DEFAULT_CONFIG.chat, ...parsed.chat },
      voice: { ...DEFAULT_CONFIG.voice, ...parsed.voice },
      forward: { ...DEFAULT_CONFIG.forward, ...parsed.forward },
      debug: { ...DEFAULT_CONFIG.debug, ...parsed.debug },
    }
  } catch (error) {
    console.error('TOML 解析失败:', error)
    throw new Error(`无法解析 TOML 文件: ${error instanceof Error ? error.message : '未知错误'}`)
  }
}

/**
 * 将配置对象转换为 TOML 格式字符串
 * @param config 配置对象
 * @returns TOML 格式的字符串
 */
export function generateTOML(config: AdapterConfig): string {
  try {
    // 填充默认值的辅助函数
    const fillDefaults = <T>(value: T, defaultValue: T): T => {
      if (value === '' || value === null || value === undefined) {
        return defaultValue
      }
      return value
    }

    // 创建填充了默认值的配置副本
    const filledConfig: AdapterConfig = {
      inner: {
        version: fillDefaults(config.inner.version, DEFAULT_CONFIG.inner.version),
      },
      nickname: {
        nickname: fillDefaults(config.nickname.nickname, DEFAULT_CONFIG.nickname.nickname),
      },
      napcat_server: {
        host: fillDefaults(config.napcat_server.host, DEFAULT_CONFIG.napcat_server.host),
        port: fillDefaults(config.napcat_server.port || 0, DEFAULT_CONFIG.napcat_server.port),
        token: fillDefaults(config.napcat_server.token, DEFAULT_CONFIG.napcat_server.token),
        heartbeat_interval: fillDefaults(
          config.napcat_server.heartbeat_interval || 0,
          DEFAULT_CONFIG.napcat_server.heartbeat_interval
        ),
      },
      maibot_server: {
        host: fillDefaults(config.maibot_server.host, DEFAULT_CONFIG.maibot_server.host),
        port: fillDefaults(config.maibot_server.port || 0, DEFAULT_CONFIG.maibot_server.port),
      },
      chat: {
        group_list_type: fillDefaults(config.chat.group_list_type, DEFAULT_CONFIG.chat.group_list_type),
        group_list: config.chat.group_list || [],
        private_list_type: fillDefaults(config.chat.private_list_type, DEFAULT_CONFIG.chat.private_list_type),
        private_list: config.chat.private_list || [],
        ban_user_id: config.chat.ban_user_id || [],
        ban_qq_bot: config.chat.ban_qq_bot ?? DEFAULT_CONFIG.chat.ban_qq_bot,
        enable_poke: config.chat.enable_poke ?? DEFAULT_CONFIG.chat.enable_poke,
      },
      voice: {
        use_tts: config.voice.use_tts ?? DEFAULT_CONFIG.voice.use_tts,
      },
      forward: {
        image_threshold: fillDefaults(
          config.forward.image_threshold || 0,
          DEFAULT_CONFIG.forward.image_threshold
        ),
      },
      debug: {
        level: fillDefaults(config.debug.level, DEFAULT_CONFIG.debug.level),
      },
    }

    // 使用 smol-toml 生成基础 TOML
    let toml = stringify(filledConfig)

    // 添加注释（smol-toml 不支持注释，需要手动添加）
    toml = addComments(toml)

    return toml
  } catch (error) {
    console.error('TOML 生成失败:', error)
    throw new Error(`无法生成 TOML 文件: ${error instanceof Error ? error.message : '未知错误'}`)
  }
}

/**
 * 为生成的 TOML 添加注释
 * @param toml 基础 TOML 字符串
 * @returns 添加了注释的 TOML 字符串
 */
function addComments(toml: string): string {
  const lines = toml.split('\n')
  const result: string[] = []

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    // [inner] section
    if (line === '[inner]') {
      result.push(line)
      continue
    }
    if (line.startsWith('version = ')) {
      result.push(`${line} # 版本号`)
      result.push('# 请勿修改版本号，除非你知道自己在做什么')
      continue
    }

    // [nickname] section
    if (line === '[nickname]') {
      result.push('[nickname] # 现在没用')
      continue
    }

    // [napcat_server] section
    if (line === '[napcat_server]') {
      result.push('[napcat_server] # Napcat连接的ws服务设置')
      continue
    }
    if (line.startsWith('host = ') && result[result.length - 1]?.includes('[napcat_server]')) {
      result.push(`${line}      # Napcat设定的主机地址`)
      continue
    }
    if (line.startsWith('port = ') && lines[i - 1]?.includes('host')) {
      result.push(`${line}             # Napcat设定的端口`)
      continue
    }
    if (line.startsWith('token = ')) {
      result.push(`${line}              # Napcat设定的访问令牌，若无则留空`)
      continue
    }
    if (line.startsWith('heartbeat_interval = ')) {
      result.push(`${line} # 与Napcat设置的心跳相同（按秒计）`)
      continue
    }

    // [maibot_server] section
    if (line === '[maibot_server]') {
      result.push('[maibot_server] # 连接麦麦的ws服务设置')
      continue
    }
    if (line.startsWith('host = ') && result[result.length - 1]?.includes('[maibot_server]')) {
      result.push(`${line} # 麦麦在.env文件中设置的主机地址，即HOST字段`)
      continue
    }
    if (line.startsWith('port = ') && result[result.length - 1]?.includes('麦麦在.env')) {
      result.push(`${line}        # 麦麦在.env文件中设置的端口，即PORT字段`)
      continue
    }

    // [chat] section
    if (line === '[chat]') {
      result.push('[chat] # 黑白名单功能')
      continue
    }
    if (line.startsWith('group_list_type = ')) {
      result.push(`${line} # 群组名单类型，可选为：whitelist, blacklist`)
      continue
    }
    if (line.startsWith('group_list = ')) {
      result.push(`${line}               # 群组名单`)
      result.push('# 当group_list_type为whitelist时，只有群组名单中的群组可以聊天')
      result.push('# 当group_list_type为blacklist时，群组名单中的任何群组无法聊天')
      continue
    }
    if (line.startsWith('private_list_type = ')) {
      result.push(`${line} # 私聊名单类型，可选为：whitelist, blacklist`)
      continue
    }
    if (line.startsWith('private_list = ')) {
      result.push(`${line}               # 私聊名单`)
      result.push('# 当private_list_type为whitelist时，只有私聊名单中的用户可以聊天')
      result.push('# 当private_list_type为blacklist时，私聊名单中的任何用户无法聊天')
      continue
    }
    if (line.startsWith('ban_user_id = ')) {
      result.push(`${line}   # 全局禁止名单（全局禁止名单中的用户无法进行任何聊天）`)
      continue
    }
    if (line.startsWith('ban_qq_bot = ')) {
      result.push(`${line} # 是否屏蔽QQ官方机器人`)
      continue
    }
    if (line.startsWith('enable_poke = ')) {
      result.push(`${line} # 是否启用戳一戳功能`)
      continue
    }

    // [voice] section
    if (line === '[voice]') {
      result.push('[voice] # 发送语音设置')
      continue
    }
    if (line.startsWith('use_tts = ')) {
      result.push(`${line} # 是否使用tts语音（请确保你配置了tts并有对应的adapter）`)
      continue
    }

    // [forward] section
    if (line === '[forward]') {
      result.push('[forward] # 转发消息处理设置')
      continue
    }
    if (line.startsWith('image_threshold = ')) {
      result.push(`${line} # 图片数量阈值：转发消息中图片数量超过此值时使用占位符(避免麦麦VLM处理卡死)`)
      continue
    }

    // [debug] section
    if (line.startsWith('level = ') && result[result.length - 1] === '[debug]') {
      result.push(`${line} # 日志等级（DEBUG, INFO, WARNING, ERROR, CRITICAL）`)
      continue
    }

    result.push(line)
  }

  return result.join('\n')
}

/**
 * 验证配置路径格式
 * @param path 文件路径
 * @returns 验证结果
 */
export function validatePath(path: string): { valid: boolean; error: string } {
  if (!path.trim()) {
    return { valid: false, error: '路径不能为空' }
  }

  if (!path.toLowerCase().endsWith('.toml')) {
    return { valid: false, error: '文件必须是 .toml 格式' }
  }

  // 支持相对路径和绝对路径
  // Windows 绝对路径: C:\path\to\file.toml 或 \\server\share\file.toml
  const windowsPathRegex = /^([a-zA-Z]:\\|\\\\[^\\]+\\[^\\]+\\).+\.toml$/i
  // Linux/Unix 绝对路径: /path/to/file.toml 或 ~/path/to/file.toml
  const unixPathRegex = /^(\/|~\/).+\.toml$/i
  // 相对路径: ./path/to/file.toml 或 ../path/to/file.toml 或 path/to/file.toml
  const relativePathRegex = /^(\.{1,2}[\\/]|[^:\\/]).+\.toml$/i

  const isWindows = windowsPathRegex.test(path)
  const isUnix = unixPathRegex.test(path)
  const isRelative = relativePathRegex.test(path)

  if (!isWindows && !isUnix && !isRelative) {
    return {
      valid: false,
      error: '路径格式错误',
    }
  }

  // 检查路径中是否包含非法字符
  // eslint-disable-next-line no-control-regex
  const illegalChars = /[<>"|?*\x00-\x1F]/
  if (illegalChars.test(path)) {
    return { valid: false, error: '路径包含非法字符' }
  }

  return { valid: true, error: '' }
}
