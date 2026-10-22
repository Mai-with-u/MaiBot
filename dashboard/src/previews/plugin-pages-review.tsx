import { useState } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'

import { PluginPagesSettings, type PluginPagePreferences } from '@/components/plugin-pages-settings'
import { Button } from '@/components/ui/button'
import type { WebUIExtension } from '@/lib/plugin-webui'
import zh from '@/i18n/locales/zh.json'

const i18n = createInstance()
void i18n.init({
  lng: 'zh',
  resources: { zh: { translation: zh } },
  interpolation: { escapeValue: false },
})
const samples: WebUIExtension[] = [
  {
    id: 'maibot.mai-recog-self',
    title: '照片上的人是你吗？',
    pages: ['图片标注', '训练任务', '模型版本', '识别测试', '运行环境'],
  },
  { id: 'sengokucola.pixiv-image-plugin', title: 'Pixiv 图库', pages: ['Pixiv图库'] },
  { id: 'preview.maiwork', title: 'MaiWork', pages: ['工作台'] },
].map((sample) => ({
  schema_version: 2,
  plugin_id: sample.id,
  workspace_title: sample.title,
  pages: sample.pages.map((title, index) => ({
    mode: 'declarative',
    id: `page-${index}`,
    title,
    description: '',
    placement: 'workspace',
    icon: 'puzzle',
    queries: {},
    actions: {},
    content: [],
  })),
}))
const initialPreferences: PluginPagePreferences = {
  order: samples.map((item) => item.plugin_id),
  hidden: [],
}

/** 复用正式设置页；排序和显示仅在预览内存中变化，不影响真实插件。 */
export function PluginPagesReview() {
  const [preferences, setPreferences] = useState(initialPreferences)
  const [notice, setNotice] = useState('示例数据：可以调整顺序、切换显示，刷新恢复设计稿顺序。')
  return (
    <I18nextProvider i18n={i18n}>
      <main className="p-8">
        <PluginPagesSettings
          extensions={samples}
          preferences={preferences}
          onPreferencesChange={setPreferences}
          onRefresh={() => {
            setPreferences(initialPreferences)
            setNotice('已恢复示例数据。')
          }}
          renderPageLink={(_, page) => (
            <Button
              variant="outline"
              size="xs"
              onClick={() => setNotice(`预览：${page.title}。正式页面中会打开对应的插件页面。`)}
            >
              {page.title}
            </Button>
          )}
        />
        <p role="status" className="text-muted-foreground mt-6 text-xs">
          {notice}
        </p>
      </main>
    </I18nextProvider>
  )
}
