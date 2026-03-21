/**
 * 扩展包 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface Pack {
  id: string
  name: string
  description: string
  version: string
  author: string
  downloads: number
  rating: number
  installed: boolean
}

export async function getPackList(): Promise<{ success: boolean; data: Pack[] }> {
  const response = await fetchWithAuth('/api/webui/packs')
  return response.json()
}

export async function getPackDetail(id: string): Promise<{ success: boolean; data: Pack }> {
  const response = await fetchWithAuth(`/api/webui/packs/${id}`)
  return response.json()
}

export async function installPack(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/packs/${id}/install`, {
    method: 'POST',
  })
  return response.json()
}

export async function uninstallPack(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/packs/${id}/uninstall`, {
    method: 'POST',
  })
  return response.json()
}

export async function downloadPack(id: string): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/packs/${id}/download`, {
    method: 'POST',
  })
  return response.json()
}
