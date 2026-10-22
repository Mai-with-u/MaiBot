import { Link } from '@tanstack/react-router'

import { PluginPagesSettings } from '@/components/plugin-pages-settings'
import { Button } from '@/components/ui/button'
import {
  extensionPath,
  refreshPluginWebUI,
  setPluginWebUIPreferences,
  usePluginWebUI,
} from '@/lib/plugin-webui'

export function PluginWebUIManagerPanel() {
  const registry = usePluginWebUI(true)
  return (
    <PluginPagesSettings
      {...registry}
      onRefresh={() => {
        void refreshPluginWebUI()
      }}
      onPreferencesChange={setPluginWebUIPreferences}
      renderPageLink={(extension, page) => (
        <Button asChild variant="outline" size="xs">
          <Link to={extensionPath(extension.plugin_id, page.id)}>{page.title}</Link>
        </Button>
      )}
    />
  )
}
