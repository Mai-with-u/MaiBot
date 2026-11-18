import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useState } from 'react'

import { PluginReviewItem } from '@/components/plugin-review-item'
import { Button } from '@/components/ui/button'
import { getPluginFeedback } from '@/lib/plugin-feedback'

/** 单独加载公开评论与回复，服务故障不阻塞点赞、评分或安装。 */
export function PluginFeedback({ pluginId }: { pluginId: string }) {
  const [offset, setOffset] = useState(0)
  const feedback = useQuery({
    queryKey: ['plugin-feedback', pluginId, offset],
    queryFn: ({ signal }) => getPluginFeedback(pluginId, offset, signal),
    staleTime: 30000,
    refetchInterval: 60000,
    retry: false,
  })

  return (
    <section className="space-y-3" aria-label="评论与开发者回复">
      <div className="flex items-center justify-between gap-2">
        <h4 data-plugin-stats-heading="true" className="text-sm font-semibold">最近评论</h4>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          aria-label={feedback.isFetching ? '正在刷新评论' : '刷新评论'}
          title="刷新评论"
          disabled={feedback.isFetching}
          onClick={() => void feedback.refetch()}
        >
          <RefreshCw aria-hidden="true" className={`h-4 w-4 ${feedback.isFetching ? 'motion-safe:animate-spin' : ''}`} />
        </Button>
      </div>
      {feedback.isPending && <p className="text-sm text-muted-foreground">正在读取评论…</p>}
      {feedback.isError && <p role="alert" className="text-sm text-destructive">{feedback.error.message}，可点击刷新重试。</p>}
      {!feedback.isError && feedback.data && (
        <>
          {feedback.data.items.length === 0 && <p className="text-sm text-muted-foreground">暂无评论。</p>}
          {feedback.data.items.map((item) => <PluginReviewItem key={item.id} item={item} />)}
          <div className="flex items-center gap-2">
            {offset > 0 && <Button variant="outline" size="sm" onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</Button>}
            {feedback.data.next_offset !== null && (
              <Button variant="outline" size="sm" onClick={() => {
                const next = feedback.data?.next_offset
                if (next != null) setOffset(next)
              }}>下一页</Button>
            )}
          </div>
        </>
      )}
    </section>
  )
}
