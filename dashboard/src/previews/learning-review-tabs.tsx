import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

/** 预览页切换仅修改页面参数，保留当前主题、明暗模式和嵌入状态。 */
export function LearningReviewTabs({ page }: { page: 'expression' | 'jargon' }) {
  return (
    <Tabs
      value={page}
      onValueChange={(value) => {
        const url = new URL(window.location.href)
        url.searchParams.set('page', value)
        window.location.assign(url)
      }}
    >
      <TabsList aria-label="学习类型">
        <TabsTrigger value="expression" size="sm">
          表达方式
        </TabsTrigger>
        <TabsTrigger value="jargon" size="sm">
          黑话
        </TabsTrigger>
      </TabsList>
    </Tabs>
  )
}
