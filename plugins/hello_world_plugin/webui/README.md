# Hello World WebUI 页面

这个目录展示插件页面的最小运行时结构：

```text
webui/
├── README.md
└── dist/
    └── index.js
```

`index.js` 导出 `mount(container, context)`。Host 会把插件 Manifest 中的 `entry` 映射到同源资源 URL，并在页面进入时调用 `mount`。返回的函数会在离开页面或插件被卸载时执行清理。

页面通过 `context.request('greet', { body })` 调用 Manifest 中 `api.greet` 映射的插件 API。页面不应直接拼接后端 URL，也不应读取插件目录的本地路径。

Hello World 插件默认保持禁用，以免改变已有示例插件的聊天行为。启用插件并确认运行时加载成功后，页面才会出现在“扩展与集成”菜单中。
