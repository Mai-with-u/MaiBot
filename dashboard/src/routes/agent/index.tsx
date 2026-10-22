import { AgentWorkspace } from '@/components/agent/AgentWorkspace'
import { ComputerDesktop } from '@/components/agent/ComputerDesktop'
import { agentApi } from '@/lib/agent-api'

export function AgentPage() {
  return <AgentWorkspace api={agentApi} titleKey="sidebar.menu.agent" desktopView={<ComputerDesktop />} />
}
