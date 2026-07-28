import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/mcp'

export interface MCPToolPreview {
  name: string
  title: string
  description: string
  read_only: boolean | null
  destructive: boolean | null
}

export interface MCPConnectionTestResponse {
  success: boolean
  error: string
  protocol_version: string
  tools: MCPToolPreview[]
}

export interface MCPServerStatus {
  name: string
  transport: string
  connected: boolean
  protocol_version: string
  tool_count: number
  error: string
}

export interface MCPStatusResponse {
  initialized: boolean
  server_count: number
  tool_count: number
  servers: MCPServerStatus[]
}

export function testMCPConnection(
  server: Record<string, unknown>
): Promise<MCPConnectionTestResponse> {
  return backendApi.post<MCPConnectionTestResponse>(`${API_BASE}/test`, {
    body: server,
    errorMessage: '测试 MCP 连接失败',
  })
}

export function getMCPStatus(): Promise<MCPStatusResponse> {
  return backendApi.get<MCPStatusResponse>(`${API_BASE}/status`, {
    cache: 'no-store',
    errorMessage: '获取 MCP 状态失败',
  })
}
