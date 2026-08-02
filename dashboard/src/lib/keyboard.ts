export type ShortcutKey =
  | 'mod'
  | 'shift'
  | 'alt'
  | 'enter'
  | 'esc'
  | 'up'
  | 'down'
  | 'left'
  | 'right'
  | string

const MAC_PLATFORMS = /(Mac|iPhone|iPod|iPad)/i

export function isMacLikePlatform(): boolean {
  if (typeof navigator === 'undefined') {
    return false
  }

  return MAC_PLATFORMS.test(navigator.platform || navigator.userAgent)
}

export function getShortcutKeyLabel(key: ShortcutKey): string {
  const isMacLike = isMacLikePlatform()
  const normalizedKey = key.toLowerCase()

  switch (normalizedKey) {
    case 'mod':
      return isMacLike ? '⌘' : 'Ctrl'
    case 'shift':
      return isMacLike ? '⇧' : 'Shift'
    case 'alt':
      return isMacLike ? '⌥' : 'Alt'
    case 'enter':
      return isMacLike ? '↵' : 'Enter'
    case 'esc':
    case 'escape':
      return 'Esc'
    case 'up':
      return '↑'
    case 'down':
      return '↓'
    case 'left':
      return '←'
    case 'right':
      return '→'
    default:
      return key.length === 1 ? key.toUpperCase() : key
  }
}

export function getPlatformModifierAriaLabel(): string {
  return isMacLikePlatform() ? 'Command' : 'Control'
}

export function matchesShortcut(
  event: KeyboardEvent | React.KeyboardEvent,
  keys: ShortcutKey[]
): boolean {
  const normalizedKeys = keys.map((key) => key.toLowerCase())
  const eventKey = event.key.toLowerCase()

  const modifierChecks = {
    mod: isMacLikePlatform() ? event.metaKey : event.ctrlKey,
    shift: event.shiftKey,
    alt: event.altKey,
  }

  for (const key of normalizedKeys) {
    if (key in modifierChecks) {
      if (!modifierChecks[key as keyof typeof modifierChecks]) {
        return false
      }
      continue
    }

    if (eventKey !== key) {
      return false
    }
  }

  return true
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  return (
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA' ||
    target.isContentEditable ||
    target.getAttribute('role') === 'textbox'
  )
}
