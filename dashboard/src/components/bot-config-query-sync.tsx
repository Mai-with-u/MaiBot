import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { getHostRuntime } from '@/host/state'
import { localChatInfoQueryKey } from '@/lib/bot-nickname'
import {
  BOT_CONFIG_QUERY_KEY,
  BOT_CONFIG_UPDATED_EVENT,
  subscribeToBotConfigUpdatesFromOtherTabs,
} from '@/lib/config-api'

export function BotConfigQuerySync() {
  const queryClient = useQueryClient()

  useEffect(() => {
    const refreshBotConfigAndChatInfo = async () => {
      const queryKeys = [BOT_CONFIG_QUERY_KEY, localChatInfoQueryKey(getHostRuntime().selected_bot_id)]
      await Promise.all(queryKeys.map((queryKey) => queryClient.cancelQueries({ queryKey, exact: true })))
      await Promise.all(queryKeys.map((queryKey) => queryClient.invalidateQueries({ queryKey, exact: true })))
    }
    window.addEventListener(BOT_CONFIG_UPDATED_EVENT, refreshBotConfigAndChatInfo)
    const unsubscribe = subscribeToBotConfigUpdatesFromOtherTabs()
    return () => {
      unsubscribe()
      window.removeEventListener(BOT_CONFIG_UPDATED_EVENT, refreshBotConfigAndChatInfo)
    }
  }, [queryClient])

  return null
}
