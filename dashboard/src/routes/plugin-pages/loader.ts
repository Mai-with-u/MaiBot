/** 插件入口模块的最小 Host ABI，页面 bundle 不需要依赖 Dashboard 的 React 实例。 */
export interface PluginPageModule {
  [exportName: string]: unknown
}

/**
 * 加载 Host 清单提供的同源 ESM 入口。
 * @vite-ignore 是必要的：入口来自运行时 API，不能在构建阶段被 Vite 解析为本地模块。
 */
export async function loadPluginPageModule(entry: string): Promise<PluginPageModule> {
  return (await import(/* @vite-ignore */ entry)) as PluginPageModule
}
