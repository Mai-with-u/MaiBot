import type { DashboardStyle } from '@/lib/theme/tokens'

import type { ApiProviderSetupConfig, BasicSetupConfig, BotBasicConfig, ModelSetupConfig, PersonalityConfig } from './types'

export const PERSONALITY_MODES = ['template', 'manual'] as const
export type PersonalityMode = (typeof PERSONALITY_MODES)[number]

export interface SetupDraft {
  stepId: string
  botBasic: BotBasicConfig
  personalityMode: PersonalityMode | null
  personalityDrafts: Partial<Record<PersonalityMode, PersonalityConfig>>
  personalityTemplate?: PersonalityConfig
  personalityPresetId: string
  provider: Pick<ApiProviderSetupConfig, 'provider_name' | 'base_url'>
  needsApiKey: boolean
  modelSetup: ModelSetupConfig
  basicSettings: BasicSetupConfig | null
  desktopPetDraft: boolean | null
  dashboardStyleDraft: DashboardStyle | null
}

export function readSetupDraft(key: string): SetupDraft | null {
  try {
    const raw = sessionStorage.getItem(key)
    if (!raw) return null
    const draft: SetupDraft | null = JSON.parse(raw)
    return typeof draft?.stepId === 'string' && draft.botBasic && draft.provider &&
      draft.modelSetup && draft.personalityDrafts ? draft : null
  } catch {
    return null
  }
}

export function writeSetupDraft(key: string, draft: SetupDraft | null): void {
  try {
    if (draft) sessionStorage.setItem(key, JSON.stringify(draft))
    else sessionStorage.removeItem(key)
  } catch {
    console.warn('无法更新设置向导的会话草稿')
  }
}
