import { backendApi } from '@/lib/http'

export interface AgentEvent {
  backend?: string
  index: number
  id: string
  time: string
  kind: string
  items: Array<{
    item_type: string
    parts?: Array<{ type: string; text?: string }>
    tool_call?: { func_name: string; args: unknown }
    output?: string
    success?: boolean
    tool_name?: string
    display_kind?: string
    summary_parts?: string[]
    text_parts?: string[]
  }>
}

export function createAgentApi(root: string) {
  return {
    events: (after: number, signal: AbortSignal) => backendApi.get<{
      events: AgentEvent[]; queued: AgentEvent[]; state: string; error: string; pending: boolean; backend: string; desktop_enabled: boolean;
    }>(`${root}/events`, { query: { after }, signal }),
    message: (text: string) => backendApi.post<{ id: string; backend: string }>(`${root}/message`, { body: { text } }),
    resume: (signal: AbortSignal) => backendApi.post<{ agent_state: string; error: string }>(`${root}/control`, {
      body: { operation: 'resume' }, signal,
    }),
  }
}

export type AgentApi = ReturnType<typeof createAgentApi>
export const agentApi = createAgentApi('/api/webui/agent')
