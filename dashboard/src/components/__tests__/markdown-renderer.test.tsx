// @vitest-environment node
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { MarkdownRenderer } from '../markdown-renderer'

describe('MarkdownRenderer', () => {
  it('keeps inline code inside ordered-list text', () => {
    const content = '2. `config.toml` 中旧的 `tags = [...]` 会自动迁移到 `tag_templates`'

    const html = renderToStaticMarkup(<MarkdownRenderer content={content} />)

    expect(html).toContain('<li><code')
    expect(html).toContain('config.toml')
    expect(html).toContain('tags = [...]')
    expect(html).toContain('tag_templates')
    expect(html).not.toContain('<pre')
    expect(html).not.toMatch(/<code[^>]*\bblock\b/)
  })

  it('keeps fenced code blocks inside pre containers', () => {
    const html = renderToStaticMarkup(<MarkdownRenderer content={'```toml\ncount = 3\n```'} />)

    expect(html).toContain('<pre')
    expect(html).toContain('overflow-x-auto')
    expect(html).toContain('language-toml')
    expect(html).toContain('count = 3')
  })

  it('renders headings with the custom typography classes', () => {
    const html = renderToStaticMarkup(
      <MarkdownRenderer
        content={['# 一级标题', '## 二级标题', '### 三级标题', '#### 四级标题'].join('\n\n')}
      />
    )

    expect(html).toContain('<h1 class="text-3xl font-bold mt-6 mb-4"')
    expect(html).toContain('一级标题</h1>')
    expect(html).toContain('<h2 class="text-2xl font-bold mt-5 mb-3"')
    expect(html).toContain('二级标题</h2>')
    expect(html).toContain('<h3 class="text-xl font-bold mt-4 mb-2"')
    expect(html).toContain('三级标题</h3>')
    expect(html).toContain('<h4 class="text-lg font-semibold mt-3 mb-2"')
    expect(html).toContain('四级标题</h4>')
  })

  it('wraps GFM tables and styles th/td cells', () => {
    const html = renderToStaticMarkup(
      <MarkdownRenderer content={'| 列A | 列B |\n| --- | --- |\n| 单元格1 | 单元格2 |'} />
    )

    expect(html).toContain('<div class="overflow-x-auto">')
    expect(html).toContain('<table class="border-collapse border border-border"')
    expect(html).toContain(
      '<th class="border border-border bg-muted px-4 py-2 text-left font-semibold"'
    )
    expect(html).toContain('<td class="border border-border px-4 py-2"')
    expect(html).toContain('单元格1</td>')
    expect(html).toContain('单元格2</td>')
  })

  it('opens links in a new tab with noopener', () => {
    const html = renderToStaticMarkup(
      <MarkdownRenderer content={'请阅读 [文档](https://example.com/docs)'} />
    )

    expect(html).toContain('class="text-primary hover:underline"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('href="https://example.com/docs"')
    expect(html).toContain('>文档</a>')
  })

  it('renders blockquotes, unordered lists and thematic breaks', () => {
    const html = renderToStaticMarkup(
      <MarkdownRenderer content={['> 引用内容', '', '- 无序项', '', '---'].join('\n')} />
    )

    expect(html).toContain(
      '<blockquote class="border-l-4 border-primary pl-4 italic text-muted-foreground"'
    )
    expect(html).toContain('引用内容')
    expect(html).toContain('<ul class="list-disc list-inside space-y-1 my-2"')
    expect(html).toContain('无序项')
    expect(html).toContain('<hr class="my-4 border-border"')
  })

  it('appends the optional className onto the prose wrapper', () => {
    const html = renderToStaticMarkup(
      <MarkdownRenderer content="一段正文" className="custom-md" />
    )

    expect(html).toContain('prose prose-sm dark:prose-invert max-w-none custom-md')
    expect(html).toContain('<p class="my-2 leading-relaxed"')
    expect(html).toContain('一段正文</p>')
  })
})
