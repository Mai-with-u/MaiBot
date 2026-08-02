import { createContext, useContext } from 'react'

export type AssetStoreContextType = {
  getAssetUrl: (assetId: string) => Promise<string | undefined>
}

export const AssetStoreContext = createContext<AssetStoreContextType | null>(null)

export function useAssetStore(): AssetStoreContextType {
  const context = useContext(AssetStoreContext)
  if (!context) {
    throw new Error('useAssetStore must be used within AssetStoreProvider')
  }
  return context
}
