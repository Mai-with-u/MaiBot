let nicknameRequestOrder = 0

export interface BotNicknameRequest {
  startedAt: number
  order: number
}

export interface BotNicknameSample {
  nickname: string | null
  request: BotNicknameRequest
  version?: string
}

export function startBotNicknameRequest(): BotNicknameRequest {
  return { startedAt: Date.now(), order: ++nicknameRequestOrder }
}

export function readBotNicknameVersion(value: unknown): string | undefined {
  if (typeof value === 'string' && /^[1-9]\d*$/.test(value)) return value
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? String(value) : undefined
}

export function isNewerBotNicknameVersion(candidate: string, current: string): boolean {
  return BigInt(candidate) > BigInt(current)
}

export function isNewerBotNicknameSample(candidate: BotNicknameSample, current?: BotNicknameSample): boolean {
  if (current === undefined) return true

  const candidateVersion = candidate.version ?? '0'
  const currentVersion = current.version ?? '0'
  if (candidateVersion !== currentVersion) return isNewerBotNicknameVersion(candidateVersion, currentVersion)

  return candidate.request.order > current.request.order
}

export function localChatBotNicknameQueryKey(botId: string | null) {
  return ['local-chat-bot-nickname', botId] as const
}

export function localChatInfoQueryKey(botId: string | null) {
  return ['local-chat-info', botId] as const
}

export interface BotNicknameState {
  nickname: string | null
  isResolved: boolean
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
  if (botNickname.isResolved) {
    return botNickname.nickname ?? fallback
  }

  return knownNickname?.trim() || fallback
}
