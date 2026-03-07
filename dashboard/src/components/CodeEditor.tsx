import { useEffect, useState } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { css } from '@codemirror/lang-css'
import { json, jsonParseLinter } from '@codemirror/lang-json'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { EditorView } from '@codemirror/view'
import { StreamLanguage } from '@codemirror/language'
import { toml as tomlMode } from '@codemirror/legacy-modes/mode/toml'

import { useTheme } from '@/components/use-theme'

export type Language = 'python' | 'json' | 'toml' | 'css' | 'text'

interface CodeEditorProps {
  value: string

  onChange?: (value: string) => void
  language?: Language
  readOnly?: boolean
  height?: string
  minHeight?: string
  maxHeight?: string
  placeholder?: string
  theme?: 'light' | 'dark'
  className?: string
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const languageExtensions: Record<Language, any[]> = {
  python: [python()],
  json: [json(), jsonParseLinter()],
  toml: [StreamLanguage.define(tomlMode)],
  css: [css()],
  text: [],
}

export function CodeEditor({
  value,
  onChange,
  language = 'text',
  readOnly = false,
  height = '400px',
  minHeight,
  maxHeight,
  placeholder,
  theme,
  className = '',
}: CodeEditorProps) {
  const [mounted, setMounted] = useState(false)
  const { resolvedTheme } = useTheme()

  useEffect(() => {
    setMounted(true)
  }, [])

  if (!mounted) {
    return (
      <div
        className={`rounded-md border bg-muted animate-pulse ${className}`}
        style={{ height, minHeight, maxHeight }}
      />
    )
  }

  const extensions = [
    ...(languageExtensions[language] || []),
    EditorView.lineWrapping,
    // 应用 JetBrains Mono 字体
    EditorView.theme({
      '&': {
        fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
      },
      '.cm-content': {
        fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
      },
      '.cm-gutters': {
        fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
      },
      '.cm-scroller': {
        fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
      },
    }),
  ]

  if (readOnly) {
    extensions.push(EditorView.editable.of(false))
  }

  // 如果外部传了 theme prop 则使用，否则从 context 自动获取
  const effectiveTheme = theme ?? resolvedTheme

  return (
    <div className={`rounded-md overflow-hidden border custom-scrollbar ${className}`}>
      <CodeMirror
        value={value}
        height={height}
        minHeight={minHeight}
        maxHeight={maxHeight}
        theme={effectiveTheme === 'dark' ? oneDark : undefined}
        extensions={extensions}
        onChange={onChange}
        placeholder={placeholder}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLineGutter: true,
          highlightSpecialChars: true,
          history: true,
          foldGutter: true,
          drawSelection: true,
          dropCursor: true,
          allowMultipleSelections: true,
          indentOnInput: true,
          syntaxHighlighting: true,
          bracketMatching: true,
          closeBrackets: true,
          autocompletion: true,
          rectangularSelection: true,
          crosshairCursor: true,
          highlightActiveLine: true,
          highlightSelectionMatches: true,
          closeBracketsKeymap: true,
          defaultKeymap: true,
          searchKeymap: true,
          historyKeymap: true,
          foldKeymap: true,
          completionKeymap: true,
          lintKeymap: true,
        }}
      />
    </div>
  )
}

export default CodeEditor
