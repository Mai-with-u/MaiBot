import { backendApi } from '@/lib/http'

export type SharingCapability = 'expression' | 'jargon' | 'memory'
export interface SharingRule {
  platform: string
  item_id: string
  rule_type: 'group' | 'private' | 'all'
}
export interface SharingGroupInput {
  name: string
  color: string
  note: string
  enabled: boolean
  revision?: number
  member_ids: string[]
  rules: SharingRule[]
  capabilities: Partial<Record<SharingCapability, boolean>>
}
export interface SharingGroup extends SharingGroupInput {
  id: string
  revision: number
  resolved_ids: string[]
  created_at: number
  updated_at: number
}
const base = '/api/webui/sharing-groups'
export const getSharingGroups = () =>
  backendApi.get<{ data: SharingGroup[]; capabilities: Record<string, string> }>(base)
export const saveSharingGroup = (data: SharingGroupInput, id?: string) =>
  id
    ? backendApi.put<SharingGroup>(`${base}/${encodeURIComponent(id)}`, { body: data })
    : backendApi.post<SharingGroup>(base, { body: data })
export const deleteSharingGroup = (group: SharingGroup) =>
  backendApi.delete(`${base}/${encodeURIComponent(group.id)}`, {
    query: { revision: group.revision },
  })
