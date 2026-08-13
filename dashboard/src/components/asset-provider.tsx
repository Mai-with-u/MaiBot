import { useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'

import { getAsset } from '@/lib/asset-store'
import { AssetStoreContext } from '@/lib/asset-store-context'

type AssetStoreProviderProps = {
  children: ReactNode
}

export function AssetStoreProvider({ children }: AssetStoreProviderProps) {
  const urlCache = useRef<Map<string, string>>(new Map())

  const getAssetUrl = async (assetId: string): Promise<string | undefined> => {
    // Check cache first
    const cached = urlCache.current.get(assetId)
    if (cached) {
      return cached
    }

    // Fetch from IndexedDB
    const record = await getAsset(assetId)
    if (!record) {
      return undefined
    }

    // Create blob URL and cache it
    const url = URL.createObjectURL(record.blob)
    urlCache.current.set(assetId, url)
    return url
  }

  const value = useMemo(
    () => ({
      getAssetUrl,
    }),
    [],
  )

  // Cleanup: revoke all blob URLs on unmount
  useEffect(() => {
    const cache = urlCache.current
    return () => {
      cache.forEach((url) => {
        URL.revokeObjectURL(url)
      })
      cache.clear()
    }
  }, [])

  return <AssetStoreContext value={value}>{children}</AssetStoreContext>
}
