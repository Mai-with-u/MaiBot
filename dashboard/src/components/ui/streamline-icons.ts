import type { IconifyIcon } from '@iconify/react'

export type StreamlineCollection = 'streamline-block' | 'streamline-sharp'

const STREAMLINE_ICON_SIZE = 24

const streamlineSharpIcons = {
  'allergens-fish-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M13.75 2.75c-2.794 0-4.707.854-6.185 2.11c-.982.835-1.79 1.885-2.496 2.82L2.073 5.06L0 3.244v17.51l2.073-1.814l2.997-2.62c.706.935 1.514 1.985 2.496 2.82c1.478 1.256 3.39 2.11 6.185 2.11c2.98 0 5.136-1.511 6.685-3.31c1.428-1.657 2.424-3.656 3.145-5.105l.138-.276l.28-.559l-.28-.559l-.137-.275v-.001c-.722-1.449-1.718-3.448-3.146-5.106c-1.55-1.798-3.705-3.309-6.685-3.309m-7.524 7.53c.317-.395.602-.773.873-1.133c.682-.905 1.279-1.696 2.086-2.382c1.022-.869 2.36-1.515 4.565-1.515c2.02 0 3.54.989 4.79 2.44c1.127 1.31 1.956 2.9 2.663 4.31c-.707 1.411-1.536 3.001-2.662 4.31c-1.251 1.451-2.771 2.44-4.791 2.44c-2.206 0-3.543-.646-4.565-1.515c-.807-.686-1.404-1.478-2.086-2.382c-.271-.36-.556-.738-.873-1.134L5.41 12.7l-.983.86L2.5 15.245v-6.49l1.927 1.686l.983.86zm8.658-3.628c-1.703 1.41-2.633 3.338-2.634 5.346c0 2.01.93 3.939 2.634 5.35l2.232-2.696c-.964-.798-1.366-1.766-1.366-2.653c0-.886.403-1.853 1.366-2.651z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'application-add-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M.75.75h10.5v10.5H.75zm16 22.25v-3.75H13v-2.5h3.75V13h2.5v3.75H23v2.5h-3.75V23zm-16-10.25h10.5v10.5H.75zm2.5 2.5v5.5h5.5v-5.5zM14 .75h-1.25v10.5h10.5V.75zm1.25 8v-5.5h5.5v5.5z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'button-power-circle-1-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M12 2.5a9.5 9.5 0 1 0 0 19a9.5 9.5 0 0 0 0-19M0 12C0 5.373 5.373 0 12 0s12 5.373 12 12s-5.373 12-12 12S0 18.627 0 12m9.373-2.695a3.764 3.764 0 1 0 5.255 0l1.745-1.79a6.264 6.264 0 1 1-8.745 0zM10.75 5v4h2.5V5z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'chat-bubble-square-write-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="m19 .086l.707.707l3.5 3.5l.707.707l-.707.707l-6 6l-.293.293H12V7.086l.293-.293l6-6zM2.75.5h13.007l-2.5 2.5H4v14.888l-.03.133l-.576 2.593l2.69-.585l.132-.029H21v-9.257l2.5-2.5V22.5H6.484l-4.468.972l-1.91.415l.424-1.908l.97-4.366V.5z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'chat-two-bubbles-oval-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M3.515 3.515A12 12 0 0 1 23.78 9.719a10 10 0 0 0-3.818-2.9A9.5 9.5 0 0 0 12 2.5a9.5 9.5 0 0 0-8.382 13.97l.193.364l-.06.407l-.52 3.535l3.57-.509l.145-.02A10 10 0 0 0 9.72 23.78a12 12 0 0 1-2.954-.983l-4.839.69l-1.657.236l.244-1.656l.707-4.797A12 12 0 0 1 3.515 3.515m6.828 6.828a8 8 0 0 1 12.87 9.118l.458 3.073l.198 1.33l-1.33-.192l-3.101-.448a7.999 7.999 0 0 1-9.095-12.88" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'cyborg-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M13 1v5h10v10h-4.914l-.293.293L16.086 18H7.914l-1.707-1.707L5.914 16H1V6h10V1zM1 18v5h22v-5h-4.086l-1.707 1.707l-.293.293H7.086l-.293-.293L5.086 18zm5.5-5v-3h2v3zm9-3v3h2v-3z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'delete-2-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M7 1L1 7l5 5l-5 5l6 6l5-5l5 5l6-6l-5-5l5-5l-6-6l-5 5z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'desktop-chat-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M11 0v13.223l1.496-.855l3.27-1.868H24V0zM9 3H0v16.5h10v2H6.25V24h10v-2.5H12.5v-2h10v-7H20V17H2.5V5.5H9z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'edit-pdf-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M1 1h12.414L20 7.586v3.626l-8.5 8.5V23H1zm3 19h5v-2H7.5v-4H9v-2H4v2h1.5v4H4zm11.667 3L23 15.667L20.333 13L13 20.333V23z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'file-bookmark-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M2 1h14.5L22 6.5V23H2zm3 2v9l3-3l3 3V3z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'happy-face-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M2.5 12a9.5 9.5 0 1 1 19 0a9.5 9.5 0 0 1-19 0M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12s12-5.373 12-12S18.627 0 12 0M7.75 7v2.5h2.5V7zm6 0v2.5h2.5V7zm-5.5 5a3.75 3.75 0 1 0 7.5 0h2.5a6.25 6.25 0 1 1-12.5 0z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'horizontal-slider-2-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M4 7a3 3 0 1 0 0-6a3 3 0 0 0 0 6m8 8a3 3 0 1 0 0-6a3 3 0 0 0 0 6m11 5a3 3 0 1 1-6 0a3 3 0 0 1 6 0m0-15H8.388a4.5 4.5 0 0 0 0-2H23zm0 8h-6.611a4.5 4.5 0 0 0 0-2H23zM7.5 12q.001.517.112 1H1v-2h6.612a4.5 4.5 0 0 0-.112 1m8 8q.001-.517.112-1H1v2h14.612a4.5 4.5 0 0 1-.112-1" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'information-circle-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M12 23c6.075 0 11-4.925 11-11S18.075 1 12 1S1 5.925 1 12s4.925 11 11 11M9 11.5h2v4H9v2h6v-2h-2v-6H9zM13 8V6h-2v2z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'line-arrow-right-1-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="m23.117 11.116l-8-8l-1.768 1.768l5.867 5.866H0v2.5h19.215l-5.866 5.866l1.767 1.768l8-8L24 12z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'module-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M23.255 5.87v-.816l-.746-.328l-10-4.4l-.504-.222l-.503.222l-10 4.4l-.747.328v13.88l.746.33l10 4.41l.504.222l.504-.223l10-4.41l.746-.328zm-10 4.956l7.5-3.307v9.786l-7.5 3.307zm-2.5 0l-7.5-3.307v9.786l7.5 3.307z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'one-finger-short-tap-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M13.25.5V3h-2.5V.5zM7 6.75H4.5v2.5H7zm12.5 0H17v2.5h2.5zm-9.25-1H9v9.516l-1.722-.574c-2.104-.702-4.277.865-4.277 3.083v.743l.366.366l5 5l1.768-1.768l-4.592-4.591a.75.75 0 0 1 .945-.462l3.367 1.123l1.646.548V8.25h.75A.75.75 0 0 1 13 9v5.242l.651.355l4.849 2.645V24H21v-8.242l-.652-.355l-4.848-2.645V9a3.25 3.25 0 0 0-3.25-3.25zm4.402-2.17l1.768-1.767l1.768 1.767l-1.768 1.768zm-8.839 0l1.768 1.768L9.35 3.58L7.581 1.813z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'page-setting-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M18.936 2.13L17.85.23l-1.086 1.9l-4 7L11.697 11h12.308l-1.07-1.87zM17.85 5.268l1.846 3.23h-3.692zM.5.999h10v10H.5zm5 14.5a3 3 0 1 0 0 6.001a3 3 0 0 0 0-6m-5.5 3a5.5 5.5 0 1 1 11 0a5.5 5.5 0 0 1-11 0M13.25 16h9.5v-2.5h-9.5zm0 5h9.5v2.5h-9.5zm9.5-3.75h-9.5v2.5h9.5z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'router-wifi-network-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M16.403 2.25v8.545H8.21V6.167h-2v4.628H1v8.627h3.846v2.328h2v-2.328h10.308v2.328h2v-2.328H23v-8.627h-4.597V2.25zm-6.3 11.858h9.232v2h-9.232zm-4.103 0H5v2h2.132v-2z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'script-1-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M3.75 0H24v8.5h-3.75V24H0v-9h3.75zm2.5 17.5H2.5v4h15.25v-19H6.25V15h10v4.25h-2.5V17.5zM16 8H8V5.5h8zm-8 4h8V9.5H8z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'search-bar-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M22.75 5.25H1.25v13.5h21.5zm-7.354 4.5a1.916 1.916 0 1 0 0 3.832a1.916 1.916 0 0 0 0-3.832m-3.416 1.916a3.416 3.416 0 1 1 6.303 1.827l1.42 1.42l-1.061 1.06l-1.42-1.42a3.416 3.416 0 0 1-5.242-2.887" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'sign-hashtag-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M8.27.776L7.275 6.25H3v2.5h3.82l-1.181 6.5H1v2.5h4.184l-.914 5.026l2.46.448l.995-5.474h6.46l-.915 5.026l2.46.448l.995-5.474H21v-2.5h-3.82l1.181-6.5H23v-2.5h-4.184l.914-5.026l-2.46-.448l-.995 5.474H9.816l.914-5.026zm6.37 14.474l1.181-6.5h-6.46l-1.181 6.5z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'store-2-solid': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M19.618 1H4.382L2 5.764V11h20V5.764zM3 17v-4.5h2V17h7v-4.5h2V21h5v-8.5h2V23H3z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
  'user-sticker-square-remix': {
    body: '<path fill="currentColor" fill-rule="evenodd" d="M.75.75h22.5v16.768l-.366.366l-5 5l-.366.366H.75zm2.5 2.5v17.5H15V15h5.75V3.25zm7 5.25V11h-2.5V8.5zm6 0V11h-2.5V8.5zM12 14.75a5.23 5.23 0 0 1-3.789-1.615l-1.803 1.73A7.73 7.73 0 0 0 12 17.25z" clip-rule="evenodd"/>',
    height: STREAMLINE_ICON_SIZE,
    width: STREAMLINE_ICON_SIZE,
  },
} satisfies Record<string, IconifyIcon>

const streamlineBlockIcons = {} satisfies Record<string, IconifyIcon>

export const streamlineIcons: Record<StreamlineCollection, Record<string, IconifyIcon>> = {
  'streamline-block': streamlineBlockIcons,
  'streamline-sharp': streamlineSharpIcons,
}

export function getStreamlineIcon(
  collection: StreamlineCollection,
  name: string
): IconifyIcon | undefined {
  return streamlineIcons[collection][name]
}
