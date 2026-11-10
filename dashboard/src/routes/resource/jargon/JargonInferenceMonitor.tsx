import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { getJargonInferenceJobs } from '@/lib/jargon-inference-api'

/** Keep list/statistics current while inference dialogs are closed. */
export function JargonInferenceMonitor() {
  const queryClient = useQueryClient()
  const previous = useRef<string | null>(null)
  const wasActive = useRef(false)
  const jobs = useQuery({
    queryKey: ['jargon-inference', 'monitor'],
    queryFn: () => getJargonInferenceJobs({ page_size: 1 }),
    refetchInterval: 3000,
  })
  const revision = jobs.data
    ? JSON.stringify([
        jobs.data.items.map(({ id, status, completed, updated_at }) => [
          id,
          status,
          completed,
          updated_at,
        ]),
      ])
    : null
  useEffect(() => {
    if (revision === null) return
    const isActive = ['queued', 'running', 'cancelling'].includes(jobs.data?.items[0]?.status ?? '')
    const changed = revision !== previous.current
    previous.current = revision
    if (!changed && !isActive && !wasActive.current) return
    wasActive.current = isActive
    void queryClient.invalidateQueries({ queryKey: ['jargon'] })
  }, [revision, jobs.dataUpdatedAt, jobs.data, queryClient])
  return null
}
