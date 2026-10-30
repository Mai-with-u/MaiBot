export interface PersonalityProfile {
  name: string
  personality: string
  behavior_style: string
  reply_style: string
  group_chat_prompt: string
  private_chat_prompts: string
  multiple_reply_style: string[]
  multiple_probability: number
}

export const PERSONALITY_IMPORT_LIMIT = 65536
export const PERSONALITY_IMPORT_EVENT = 'maibot:personality-import'
const PENDING_KEY = 'maibot-personality-import-v1'

export const PERSONALITY_IMPORT_EXAMPLE = JSON.stringify({
  format: 'maibot.personality',
  version: 1,
  profile: {
    name: '温柔的麦麦',
    personality: '温柔、好奇，喜欢和朋友分享日常。',
    behavior_style: '自然参与聊天，认真倾听，不强行找话题。',
    reply_style: '简短自然，语气温和。',
    group_chat_prompt: '留意群友之间的互动，适时参与，不必逐条回复。',
    private_chat_prompts: '关注对方的想法，保持自然连贯的一对一交流。',
    multiple_reply_style: ['偶尔用一句俏皮话回复'],
    multiple_probability: 0.1,
  },
}, null, 2)

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 外部文本只允许声明人设字段，不接受配置、脚本或远程资源。 */
export function parsePersonalityImport(text: string): PersonalityProfile {
  if (text.length > PERSONALITY_IMPORT_LIMIT) throw new Error('导入内容不能超过 65536 个字符')
  const source = text.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i, '$1')
  let data: unknown
  try { data = JSON.parse(source) } catch { throw new Error('内容不是有效 JSON，请按示例格式粘贴') }
  if (!object(data) || data.format !== 'maibot.personality' || data.version !== 1) {
    throw new Error('仅支持 format 为 maibot.personality、version 为 1 的人设')
  }
  if (Object.keys(data).some((key) => !['format', 'version', 'profile'].includes(key))) {
    throw new Error('顶层只允许 format、version 和 profile 字段')
  }
  const profile = data.profile
  if (!object(profile)) throw new Error('profile 必须是人设对象')
  const fields = ['name', 'personality', 'behavior_style', 'reply_style', 'group_chat_prompt', 'private_chat_prompts', 'multiple_reply_style', 'multiple_probability']
  if (Object.keys(profile).some((key) => !fields.includes(key))) throw new Error('profile 中包含不支持的字段')
  for (const key of ['name', 'personality', 'behavior_style', 'reply_style']) {
    if (typeof profile[key] !== 'string') throw new Error(`${key} 必须是字符串`)
  }
  const name = (profile.name as string).trim()
  for (const key of ['group_chat_prompt', 'private_chat_prompts']) {
    if (typeof profile[key] !== 'string') throw new Error(`${key} 必须是字符串`)
  }
  if (!name) throw new Error('人设名称不能为空')
  const styles = profile.multiple_reply_style === undefined ? [] : profile.multiple_reply_style
  if (!Array.isArray(styles) || !styles.every((style) => typeof style === 'string')) {
    throw new Error('multiple_reply_style 必须是字符串数组')
  }
  const probability = profile.multiple_probability === undefined ? 0 : profile.multiple_probability
  if (typeof probability !== 'number' || !Number.isFinite(probability) || probability < 0 || probability > 1) {
    throw new Error('multiple_probability 必须是 0 到 1 之间的数字')
  }
  return {
    name,
    personality: profile.personality as string,
    behavior_style: profile.behavior_style as string,
    reply_style: profile.reply_style as string,
    group_chat_prompt: profile.group_chat_prompt as string,
    private_chat_prompts: profile.private_chat_prompts as string,
    multiple_reply_style: styles,
    multiple_probability: probability,
  }
}

/** 在鉴权跳转前保留文本；多留一个字符，让超限内容在预览中明确报错。 */
export function rememberPersonalityImport(hash: string): boolean {
  const text = new URLSearchParams(hash.replace(/^#/, '')).get('personality-import')
  if (text === null) return false
  sessionStorage.setItem(PENDING_KEY, text.slice(0, PERSONALITY_IMPORT_LIMIT + 1))
  window.dispatchEvent(new Event(PERSONALITY_IMPORT_EVENT))
  return true
}

export function readPersonalityImport(): string | null {
  return sessionStorage.getItem(PENDING_KEY)
}

export function clearPersonalityImport(): void {
  sessionStorage.removeItem(PENDING_KEY)
}
