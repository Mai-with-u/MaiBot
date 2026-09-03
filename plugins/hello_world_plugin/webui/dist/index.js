/**
 * Hello World 页面示例：只依赖 Host 提供的容器和页面上下文。
 */
export function mount(container, context) {
  const root = document.createElement('div')
  root.style.maxWidth = '640px'
  root.style.padding = '24px'
  root.style.fontFamily = 'system-ui, sans-serif'

  const title = document.createElement('h1')
  title.textContent = 'Hello World 页面'
  title.style.margin = '0 0 8px'

  const description = document.createElement('p')
  description.textContent = '这是一个由插件声明并动态加载的 WebUI 页面。'
  description.style.margin = '0 0 16px'

  const button = document.createElement('button')
  button.type = 'button'
  button.textContent = '调用插件 API'
  button.style.padding = '8px 12px'

  const status = document.createElement('p')
  status.setAttribute('role', 'status')
  status.textContent = `插件 ID：${context.pluginId}`
  status.style.margin = '16px 0 0'

  const handleClick = async () => {
    button.disabled = true
    status.textContent = '正在调用插件 API...'
    try {
      const result = await context.request('greet', {
        body: { message: '你好，页面！' },
      })
      status.textContent = result?.message ?? '插件 API 已返回'
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : String(error)
    } finally {
      button.disabled = false
    }
  }

  button.addEventListener('click', handleClick)
  root.append(title, description, button, status)
  container.append(root)

  return () => {
    button.removeEventListener('click', handleClick)
    root.remove()
  }
}
