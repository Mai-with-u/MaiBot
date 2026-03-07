import { createContext, useContext, useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'

import { getAsset } from '@/lib/asset-store'

type AssetStoreContextType = {
  getAssetUrl: (assetId: string) => Promise<string | undefined>
}

const AssetStoreContext = createContext<AssetStoreContextType | null>(null)

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
    return () => {
      urlCache.current.forEach((url) => {
        URL.revokeObjectURL(url)
      })
      urlCache.current.clear()
    }
  }, [])

  return <AssetStoreContext value={value}>{children}</AssetStoreContext>
}

export function useAssetStore() {
  const context = useContext(AssetStoreContext)
  if (!context) {
    throw new Error('useAssetStore must be used within AssetStoreProvider')
  }
  return context
}
