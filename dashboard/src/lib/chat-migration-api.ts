import { backendApi } from '@/lib/http'

export interface MigrationConflict {
  id: string
  kind: string
  label: string
  source: unknown
  target: unknown
  field_labels?: Record<string, string>
  choices: string[]
}
export interface MigrationJob {
  id: string
  source: string
  target: string
  status: string
  error: string
  created_at: string
  conflicts: MigrationConflict[]
  automatic_config?: MigrationConflict[]
  counts: { key: string; label: string; source: number; target: number; pending: number }[]
  memory: {
    source: number
    target: number
    source_images?: number
    target_images?: number
    source_facts?: number
    target_facts?: number
    source_episodes?: number
    target_episodes?: number
  }
  identities: Record<
    string,
    {
      group_name?: string
      user_nickname?: string
      group_id?: string
      user_id?: string
      platform: string
    }
  >
  result?: Record<string, number>
}
const base = '/api/webui/chat-migrations'
export const previewMigration = (source: string, target: string) =>
  backendApi.post<MigrationJob>(`${base}/preview`, { body: { source, target } })
export const getMigration = (id: string) => backendApi.get<MigrationJob>(`${base}/${id}`)
export const listMigrations = () => backendApi.get<{ items: MigrationJob[] }>(base)
export const executeMigration = (
  id: string,
  decisions: Record<string, string>,
  confirmMemory: boolean
) =>
  backendApi.post(`${base}/${id}/execute`, { body: { decisions, confirm_memory: confirmMemory } })
export const recoverMigration = (id: string) => backendApi.post(`${base}/${id}/recover`)
