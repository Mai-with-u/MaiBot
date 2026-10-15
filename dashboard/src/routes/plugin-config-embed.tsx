import { EmbedPageShell } from '@/components/embed-page-shell'

import { PluginConfigPage } from './plugin-config'

export function PluginConfigEmbedPage() {
  return (
    <EmbedPageShell shellId="embed-plugin-config" title="插件扩展 - MaiBot Dashboard">
      <PluginConfigPage />
    </EmbedPageShell>
  )
}
