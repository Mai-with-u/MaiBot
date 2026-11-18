interface Window {
  /** Optional desktop navigation supplied by OneKey, without protocol management access. */
  maibotDesktopHost?: {
    openMessagePlatform: () => void
    /** True when the WebUI runs inside the OneKey window, whose title bar shows the brand, versions and updates. */
    embedded?: boolean
    /** Tells OneKey the sidebar width (0 when no sidebar is shown) so its title bar brand column lines up with it. */
    reportLayout?: (layout: { sidebarWidth: number; collapsed: boolean; homeLocked: boolean }) => void
    /** OneKey owns the sidebar collapse key in its title bar and asks the WebUI to apply it; returns an unsubscribe. */
    onSidebarCommand?: (listener: (command: { collapsed: boolean }) => void) => () => void
  }
}
