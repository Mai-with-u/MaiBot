import { skipToken, useQuery } from '@tanstack/react-query'

import { BOT_CONFIG_QUERY_KEY, getBotConfigCached } from '@/lib/config-api'
import {
  LOCAL_CHAT_BOT_NICKNAME_QUERY_KEY,
  resolveBotNickname,
  type BotNicknameState,
} from '@/lib/bot-nickname'

export function useBotNickname(): BotNicknameState {
  const { data: botConfig, dataUpdatedAt: configUpdatedAt, isRefetchError } = useQuery({
    queryKey: BOT_CONFIG_QUERY_KEY,
    queryFn: getBotConfigCached,
    staleTime: 30_000,
  })
  const { data: sessionNickname, dataUpdatedAt: sessionUpdatedAt } = useQuery<string | null>({
    queryKey: LOCAL_CHAT_BOT_NICKNAME_QUERY_KEY,
    queryFn: skipToken,
  })

  const configNickname = resolveBotNickname(botConfig, '') || null
  // 配置刷新失败时，只使用上次配置读取之后收到的会话昵称。
  const useSessionNickname =
    isRefetchError && sessionNickname !== undefined && sessionUpdatedAt > configUpdatedAt

  return {
    nickname: useSessionNickname ? sessionNickname : configNickname,
    isConfigLoaded: botConfig !== undefined,
  }
}
