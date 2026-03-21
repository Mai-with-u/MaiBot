/**
 * 系统 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface SystemStatus {
  running: boolean
  uptime: number
  version: string
  cpuUsage: number
  memoryUsage: number
}

export interface MaiBotStatus {
  enabled: boolean
  running: boolean
  lastMessage?: string
}

export async function getSystemStatus(): Promise<{ success: boolean; data: SystemStatus }> {
  const response = await fetchWithAuth('/api/webui/system/status')
  return response.json()
}

export async function getMaiBotStatus(): Promise<{ success: boolean; data: MaiBotStatus }> {
  const response = await fetchWithAuth('/api/webui/maibot/status')
  return response.json()
}

export async function restartSystem(): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/system/restart', {
    method: 'POST',
  })
  return response.json()
}

export async function shutdownSystem(): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/system/shutdown', {
    method: 'POST',
  })
  return response.json()
}
