import { validateAPIArguments } from '@/lib/plugin-api-arguments'
import type { APIArguments, CustomPage } from '@/lib/plugin-webui'

export interface CustomPageRequest {
  id: number
  kind: 'queries' | 'actions' | 'uploads' | 'navigate'
  name: string
  args: APIArguments
  file?: File
}

/** Validate before forwarding untrusted iframe messages to the authenticated client. */
export function parseCustomPageRequest(value: unknown, page: CustomPage): CustomPageRequest {
  if (!value || typeof value !== 'object') throw new Error('Invalid bridge request')
  const request = value as CustomPageRequest
  if (
    !Number.isSafeInteger(request.id) ||
    request.id < 1 ||
    !['queries', 'actions', 'uploads', 'navigate'].includes(request.kind) ||
    typeof request.name !== 'string' ||
    request.name.length > 64 ||
    (request.kind !== 'navigate' && !Object.hasOwn(page[request.kind], request.name))
  )
    throw new Error('API binding is not declared')
  validateAPIArguments(request.args, request.kind === 'navigate' ? 4096 : 65536)
  if (Object.keys(request.args).some((key) => !/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(key)))
    throw new Error('Invalid argument name')
  if (request.kind === 'uploads') {
    const maxBytes = page.uploads![request.name].image_max_edge
      ? 64 * 1024 * 1024
      : 20 * 1024 * 1024
    if (
      !(request.file instanceof File) ||
      request.file.size === 0 ||
      request.file.size > maxBytes ||
      request.file.name.length > 512 ||
      !['', 'image/jpeg', 'image/png', 'image/webp'].includes(request.file.type)
    )
      throw new Error('Upload requires a bounded JPEG, PNG or WebP file')
    if (Object.hasOwn(request.args, 'upload_id'))
      throw new Error('Upload IDs are issued by the host')
  }
  return {
    id: request.id,
    kind: request.kind,
    name: request.name,
    args: structuredClone(request.args),
    ...(request.kind === 'uploads' ? { file: request.file } : {}),
  }
}

// The policy precedes all plugin markup. Later meta policies can only restrict it further.
// No same-origin permission, direct API/network access, forms, nested frames or workers.
const POLICY =
  "default-src 'none'; script-src 'unsafe-inline' blob:; style-src 'unsafe-inline'; " +
  "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; " +
  "frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"

const SDK = `(() => {
  let port, sequence = 0;
  const pending = new Map();
  let resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  const themeStyle = document.createElement('style');
  function applyFonts(fonts = []) {
    if (!fonts.length) return;
    const style = document.createElement('style');
    style.textContent = fonts.map(font => font.css).join('');
    document.head.append(style);
  }
  function applyContext(context) {
    api.context = context;
    document.documentElement.classList.toggle('dark', context.theme === 'dark');
    document.documentElement.style.colorScheme = context.theme === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.dashboardStyle = context.dashboardStyle;
    document.documentElement.lang = context.language;
    // textContent cannot turn theme CSS into markup/scripts. The host owns this port.
    if (themeStyle.textContent !== context.styles) themeStyle.textContent = context.styles || '';
    if (!themeStyle.isConnected) (document.body || document.head).append(themeStyle);
  }
  const api = {
    ready,
    context: {},
    page: {},
    query: (name, args = {}) => call('queries', name, args),
    action: (name, args = {}) => call('actions', name, args),
    upload: (name, file, args = {}, options = {}) => call('uploads', name, args, file, options),
    navigate: (pageId, state = {}) => call('navigate', pageId, state),
  };
  async function call(kind, name, args, file, options = {}) {
    await ready;
    if (options.signal?.aborted) throw new DOMException('Upload cancelled', 'AbortError');
    if (pending.size >= 2) throw new Error('Two requests are already running');
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const cleanup = () => { clearTimeout(timer); pending.delete(id); options.signal?.removeEventListener('abort', abort); };
      const abort = () => {
        cleanup(); port.postMessage({ kind: 'cancel', id });
        reject(new DOMException('Upload cancelled; received files remain saved', 'AbortError'));
      };
      const timer = setTimeout(() => {
        cleanup();
        if (kind === 'uploads') port.postMessage({ kind: 'cancel', id });
        reject(new Error('Request timed out; an action may still have completed'));
      }, kind === 'uploads' ? 150000 : 60000);
      pending.set(id, { resolve, reject, cleanup, onProgress: options.onProgress });
      options.signal?.addEventListener('abort', abort, { once: true });
      async function send() {
        try {
          if (kind === 'uploads') {
            if (!(file instanceof File) || file.size === 0 || file.size > 64 * 1024 * 1024)
              throw new Error('Upload requires a file within 64 MiB');
            // A sandbox-selected disk File cannot be uploaded by the parent renderer
            // (Chromium ERR_ACCESS_DENIED). Read it here and transfer an in-memory copy.
            file = new File([await file.arrayBuffer()], file.name, {
              type: file.type, lastModified: file.lastModified,
            });
          }
          if (pending.has(id)) port.postMessage({ id, kind, name, args, ...(file ? { file } : {}) });
        } catch (error) { cleanup(); reject(error); }
      }
      void send();
    });
  }
  function connect(event) {
    if (event.source !== parent || event.data?.type !== 'maibot:connect' || !event.ports[0] || port) return;
    port = event.ports[0];
    api.page = event.data.page;
    applyContext(event.data.context);
    applyFonts(event.data.fonts);
    window.removeEventListener('message', connect);
    port.onmessage = event => {
      if (event.data?.type === 'fonts') { applyFonts(event.data.fonts); return; }
      if (event.data?.type === 'viewport') {
        document.documentElement.style.setProperty('--maibot-viewport-top', event.data.top + 'px');
        document.documentElement.style.setProperty('--maibot-viewport-height', event.data.height + 'px');
        return;
      }
      if (event.data?.type === 'context') {
        applyContext(event.data.context);
        window.dispatchEvent(new CustomEvent('maibot:context', { detail: api.context }));
        return;
      }
      const item = pending.get(event.data?.id);
      if (!item) return;
      if (event.data.type === 'progress') { item.onProgress?.(event.data.percent); return; }
      item.cleanup();
      if (event.data.error) item.reject(new Error(event.data.error));
      else item.resolve(event.data.result);
    };
    // Measure content rather than scrollHeight, which cannot shrink below the iframe viewport.
    let layoutTimer;
    let lastHeight = 0;
    const reportLayout = () => {
      clearTimeout(layoutTimer);
      layoutTimer = setTimeout(() => {
        const style = getComputedStyle(document.body);
        const height = Math.max(1, Math.ceil(document.body.getBoundingClientRect().height +
          parseFloat(style.marginTop) + parseFloat(style.marginBottom)));
        if (height !== lastHeight) { lastHeight = height; port.postMessage({ type: 'layout', height }); }
      }, 100);
    };
    const observeLayout = () => {
      document.body.append(themeStyle);
      if (api.page.layout === 'document') {
        new ResizeObserver(reportLayout).observe(document.body);
        reportLayout();
      }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', observeLayout, { once: true });
    else observeLayout();
    resolveReady(api.context);
  }
  window.addEventListener('message', connect);
  Object.defineProperty(window, 'maibot', { value: api });
  parent.postMessage({ type: 'maibot:ready', nonce: __MAIBOT_NONCE__ }, '*');
})();`

export function customPageDocument(html: string, nonce: string): string {
  return (
    `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${POLICY}">` +
    `<meta name="referrer" content="no-referrer"><script>${SDK.replace('__MAIBOT_NONCE__', JSON.stringify(nonce))}</script></head><body>${html}</body></html>`
  )
}
