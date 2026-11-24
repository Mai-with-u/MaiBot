export const LOCAL_CHAT_BOT_NICKNAME_QUERY_KEY = ['local-chat-bot-nickname'] as const

export interface BotNicknameState {
  nickname: string | null
  isConfigLoaded: boolean
}

export function resolveBotNickname(
  config: Record<string, unknown> | null | undefined,
  fallback: string
): string {
  const botConfig = config?.bot
  if (typeof botConfig !== 'object' || botConfig === null || Array.isArray(botConfig)) {
    return fallback
  }

  const nickname = (botConfig as Record<string, unknown>).nickname
  return typeof nickname === 'string' && nickname.trim() ? nickname.trim() : fallback
}

export function getBotDisplayName(
  botNickname: BotNicknameState,
  knownNickname: string | null | undefined,
  fallback: string
): string {
  if (botNickname.isConfigLoaded) {
    return botNickname.nickname ?? fallback
  }

  return knownNickname?.trim() || fallback
}
