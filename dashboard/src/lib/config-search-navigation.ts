const BOT_CONFIG_PATH = '/config/bot'
const MODEL_CONFIG_PATH = '/config/model'
const SEARCH_FIELD_PARAM = 'field'

export type ModelConfigSearchTab = 'models' | 'providers' | 'tasks'

export function getModelConfigTabForField(fieldPath: string): ModelConfigSearchTab {
  const rootField = fieldPath.split('.')[0]
  if (rootField === 'models') {
    return 'models'
  }
  if (rootField === 'model_task_config') {
    return 'tasks'
  }
  return 'providers'
}

export function buildSearchNavigationPath(path: string, fieldPath?: string): string {
  if (!fieldPath || (path !== BOT_CONFIG_PATH && path !== MODEL_CONFIG_PATH)) {
    return path
  }

  const searchParams = new URLSearchParams({ [SEARCH_FIELD_PARAM]: fieldPath })
  if (path === MODEL_CONFIG_PATH) {
    searchParams.set('tab', getModelConfigTabForField(fieldPath))
  }
  return `${path}?${searchParams.toString()}`
}

export function getConfigSearchField(search: string): string {
  const normalizedSearch = search.startsWith('?') ? search.slice(1) : search
  return new URLSearchParams(normalizedSearch).get(SEARCH_FIELD_PARAM)?.trim() ?? ''
}

interface MatchedFieldElement {
  element: HTMLElement
  matchedPath: string
}

function findFieldElement(fieldPath: string, root: ParentNode): MatchedFieldElement | null {
  const candidates = root.querySelectorAll<HTMLElement>(
    '[data-config-field-path], [data-dynamic-field]'
  )
  const pathSegments = fieldPath.split('.')

  for (let pathLength = pathSegments.length; pathLength > 0; pathLength -= 1) {
    const targetPath = pathSegments.slice(0, pathLength).join('.')
    const targetElement = Array.from(candidates).find(
      (element) =>
        element.dataset.configFieldPath === targetPath ||
        element.dataset.dynamicField === targetPath
    )
    if (targetElement) {
      return { element: targetElement, matchedPath: targetPath }
    }
  }

  return null
}

export function scrollToConfigSearchField(
  fieldPath: string,
  root: ParentNode = document
): HTMLElement | null {
  const matchedField = findFieldElement(fieldPath, root)
  if (!matchedField) {
    return null
  }

  const { element: targetElement, matchedPath } = matchedField
  if (matchedPath !== fieldPath) {
    const expandButton = targetElement.querySelector<HTMLButtonElement>('[aria-expanded="false"]')
    expandButton?.click()
    if (expandButton) {
      window.setTimeout(() => scrollToConfigSearchField(fieldPath, root), 100)
    }
  }

  targetElement.scrollIntoView({ behavior: 'smooth', block: 'center' })
  targetElement.dataset.configSearchHighlight = 'true'
  window.setTimeout(() => {
    delete targetElement.dataset.configSearchHighlight
  }, 2200)
  return targetElement
}
