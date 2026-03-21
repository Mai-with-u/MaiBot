/**
 * 配置 API
 */

import { fetchWithAuth } from './fetch-with-auth'

export interface ModelListItem {
  id: string
  name: string
  provider: string
  enabled: boolean
}

export interface ModelConfig {
  provider: string
  model: string
  apiKey: string
  baseUrl?: string
  temperature?: number
  maxTokens?: number
}

export interface BotConfig {
  name: string
  description: string
  personality: string
  modelConfig?: ModelConfig
}

export async function getBotConfig(): Promise<{ success: boolean; config: BotConfig }> {
  const response = await fetchWithAuth('/api/webui/config/bot')
  return response.json()
}

export async function updateBotConfig(config: Partial<BotConfig>): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/config/bot', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  return response.json()
}

export async function getBotConfigRaw(): Promise<{ success: boolean; config: Record<string, unknown> }> {
  const response = await fetchWithAuth('/api/webui/config/bot/raw')
  return response.json()
}

export async function updateBotConfigRaw(config: Record<string, unknown>): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/config/bot/raw', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  return response.json()
}

export async function updateBotConfigSection(section: string, data: unknown): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/config/bot/section/${section}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return response.json()
}

export async function getModelConfig(): Promise<{ success: boolean; config: ModelConfig }> {
  const response = await fetchWithAuth('/api/webui/config/model')
  return response.json()
}

export async function updateModelConfig(config: Partial<ModelConfig>): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth('/api/webui/config/model', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  return response.json()
}

export async function updateModelConfigSection(section: string, data: unknown): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(`/api/webui/config/model/section/${section}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return response.json()
}

export async function fetchProviderModels(providerId: string): Promise<{ success: boolean; models: ModelListItem[] }> {
  const response = await fetchWithAuth(`/api/webui/models/${providerId}`)
  return response.json()
}

export interface TestConnectionResult {
  success: boolean
  message: string
  latency?: number
}

export async function testProviderConnection(providerId: string, config: Record<string, unknown>): Promise<TestConnectionResult> {
  const response = await fetchWithAuth(`/api/webui/providers/${providerId}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  return response.json()
}
