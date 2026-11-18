import { getPromptCatalog } from '@/lib/prompt-api'

export const promptCatalogQueryOptions = {
  queryKey: ['prompts', 'catalog'] as const,
  queryFn: getPromptCatalog,
}
