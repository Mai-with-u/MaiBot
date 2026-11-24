import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import {
  BOT_CONFIG_QUERY_KEY,
  BOT_CONFIG_UPDATED_EVENT,
  subscribeToBotConfigUpdatesFromOtherTabs,
} from '@/lib/config-api'

export function BotConfigQuerySync() {
  const queryClient = useQueryClient()

  useEffect(() => {
    const refreshBotConfig = async () => {
      await queryClient.cancelQueries({ queryKey: BOT_CONFIG_QUERY_KEY, exact: true })
      await queryClient.invalidateQueries({ queryKey: BOT_CONFIG_QUERY_KEY, exact: true })
    }
    window.addEventListener(BOT_CONFIG_UPDATED_EVENT, refreshBotConfig)
    const unsubscribe = subscribeToBotConfigUpdatesFromOtherTabs()
    return () => {
      unsubscribe()
      window.removeEventListener(BOT_CONFIG_UPDATED_EVENT, refreshBotConfig)
    }
  }, [queryClient])

  return null
}
