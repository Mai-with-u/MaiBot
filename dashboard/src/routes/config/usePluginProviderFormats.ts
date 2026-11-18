import { useEffect, useState } from 'react'
import { fetchModelClientTypes, type ModelClientType } from '@/lib/config-api'

type RegisteredPlugin = ModelClientType & {
  owner_plugin_id: string
  formats: NonNullable<ModelClientType['formats']>
}

export function usePluginProviderFormats() {
  const [plugins, setPlugins] = useState<RegisteredPlugin[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    fetchModelClientTypes().then((types) => {
      if (active) setPlugins(types.filter((item): item is RegisteredPlugin => Boolean(item.owner_plugin_id && item.formats)))
    }).catch((cause: Error) => {
      if (active) setError(cause.message)
    })
    return () => { active = false }
  }, [])
  return { plugins, error }
}
