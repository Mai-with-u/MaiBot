import { skipToken, useQuery } from '@tanstack/react-query'

import { useHostRuntime } from '@/host/state'
import { BOT_CONFIG_QUERY_KEY, getBotConfigSnapshotCached } from '@/lib/config-api'
import {
  isNewerBotNicknameSample,
  isNewerBotNicknameVersion,
  localChatBotNicknameQueryKey,
  localChatInfoQueryKey,
  readBotNicknameVersion,
  resolveBotNickname,
  startBotNicknameRequest,
  type BotNicknameRequest,
  type BotNicknameSample,
  type BotNicknameState,
} from '@/lib/bot-nickname'
import { backendApi } from '@/lib/http'

export function useLocalChatInfo(botId: string | null) {
  return useQuery({
    queryKey: localChatInfoQueryKey(botId),
    queryFn: async () => {
      let request: BotNicknameRequest | undefined
      const info = await backendApi.get<{
        bot_name: string | null
        bot_nickname_version?: string | number | null
        bot_qq: string | null
      }>('/api/chat/info', {
        cache: 'no-store',
        onSend: () => { request = startBotNicknameRequest() },
      })
      return { ...info, request }
    },
    staleTime: 30_000,
  })
}

export function useBotNickname(): BotNicknameState {
  const { selected_bot_id } = useHostRuntime()
  const { data: configSnapshot, dataUpdatedAt: configUpdatedAt, isRefetchError } = useQuery({
    queryKey: BOT_CONFIG_QUERY_KEY,
    queryFn: getBotConfigSnapshotCached,
    staleTime: 30_000,
  })
  const { data: chatInfo } = useLocalChatInfo(selected_bot_id)
  const { data: sessionNickname } = useQuery<BotNicknameSample>({
    queryKey: localChatBotNicknameQueryKey(selected_bot_id),
    queryFn: skipToken,
  })

  const chatInfoNickname = chatInfo?.request && {
    nickname: chatInfo.bot_name?.trim() || null,
    request: chatInfo.request,
    version: readBotNicknameVersion(chatInfo.bot_nickname_version),
  }
  const useSessionNickname = sessionNickname !== undefined && isNewerBotNicknameSample(sessionNickname, chatInfoNickname)
  const runtimeNickname = useSessionNickname ? sessionNickname : chatInfoNickname
  const isConfigLoaded = configSnapshot !== undefined
  const configVersion = configSnapshot?.nicknameVersion
  // 旧后端的配置响应没有版本，刷新失败时按客户端记录的配置读取时间回退。
  const useRuntimeNickname = runtimeNickname !== undefined && (
    !isConfigLoaded ||
    (configVersion === undefined
      ? isRefetchError && runtimeNickname.request.startedAt > configUpdatedAt
      : runtimeNickname.version !== undefined && isNewerBotNicknameVersion(runtimeNickname.version, configVersion))
  )

  return {
    nickname: useRuntimeNickname ? runtimeNickname.nickname : resolveBotNickname(configSnapshot?.config, '') || null,
    isResolved: isConfigLoaded || useRuntimeNickname,
  }
}
