import { css } from '@codemirror/lang-css'
import { json, jsonParseLinter } from '@codemirror/lang-json'
import { python } from '@codemirror/lang-python'
import { StreamLanguage } from '@codemirror/language'
import { toml as tomlMode } from '@codemirror/legacy-modes/mode/toml'
import { linter } from '@codemirror/lint'
import type { Extension, Range, RangeSet } from '@codemirror/state'
import { oneDark } from '@codemirror/theme-one-dark'
import { Decoration, EditorView, ViewPlugin } from '@codemirror/view'
import CodeMirror from '@uiw/react-codemirror'

import { useTheme } from '@/components/use-theme'

import type { CodeEditorProps, CodeEditorRangeClassName, Language } from './CodeEditor'

const languageExtensions: Record<Language, Extension[]> = {
  python: [python()],
  json: [json(), linter(jsonParseLinter())],
  toml: [StreamLanguage.define(tomlMode)],
  css: [css()],
  text: [],
}

const dashboardCodeScrollerMarker = ViewPlugin.fromClass(
  class {
    constructor(view: EditorView) {
      // 标记 CodeMirror 的真实 scrollDOM，避免依赖它内部 class 的注入顺序。
      view.scrollDOM.dataset.dashboardCodeScroller = 'true'
    }
  }
)

function createDiffDecorationExtension(
  lineClassNames: Record<number, string> = {},
  rangeClassNames: CodeEditorRangeClassName[] = []
) {
  return EditorView.decorations.compute([], (state) => {
    const lineDecorations = Object.entries(lineClassNames)
      .map(([lineNumber, className]) => {
        const parsedLineNumber = Number(lineNumber)
        if (!Number.isInteger(parsedLineNumber) || parsedLineNumber < 1 || parsedLineNumber > state.doc.lines) {
          return null
        }
        return Decoration.line({ class: className }).range(state.doc.line(parsedLineNumber).from)
      })
      .filter((decoration): decoration is Range<Decoration> => decoration !== null)

    const rangeDecorations = rangeClassNames
      .map((range) => {
        if (
          range.fromLine < 1 ||
          range.toLine < 1 ||
          range.fromLine > state.doc.lines ||
          range.toLine > state.doc.lines ||
          range.fromLine > range.toLine
        ) {
          return null
        }

        const fromLine = state.doc.line(range.fromLine)
        const toLine = state.doc.line(range.toLine)
        const fromCh = Math.max(0, Math.min(range.fromCh, fromLine.length))
        const toCh = Math.max(0, Math.min(range.toCh, toLine.length))
        const from = fromLine.from + fromCh
        const to = toLine.from + toCh
        if (to <= from) {
          return null
        }
        return Decoration.mark({ class: range.className }).range(from, to)
      })
      .filter((decoration): decoration is Range<Decoration> => decoration !== null)

    return Decoration.set([...lineDecorations, ...rangeDecorations], true) as RangeSet<Decoration>
  })
}

export default function CodeEditorImpl({
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
  lineClassNames = {},
  rangeClassNames = [],
}: CodeEditorProps) {
  const { resolvedTheme } = useTheme()

  const extensions = [
    ...(languageExtensions[language] || []),
    EditorView.lineWrapping,
    // 应用 JetBrains Mono 字体
    EditorView.theme({
      '&': {
        fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
        minHeight: 0,
      },
      '.cm-content': {
        fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
      },
      '.cm-gutters': {
        fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
      },
      '.cm-scroller': {
        fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
        minHeight: 0,
        overflow: 'auto !important',
        overscrollBehavior: 'contain',
        touchAction: 'pan-x pan-y',
      },
      '.cm-prompt-diff-added': {
        backgroundColor: 'rgba(34, 197, 94, 0.18)',
        boxShadow: 'inset 4px 0 0 rgb(34, 197, 94)',
      },
      '.cm-prompt-diff-removed': {
        backgroundColor: 'rgba(239, 68, 68, 0.18)',
        boxShadow: 'inset 4px 0 0 rgb(239, 68, 68)',
      },
      '.cm-prompt-diff-added-text': {
        backgroundColor: 'rgba(34, 197, 94, 0.38)',
        borderRadius: '2px',
        color: 'rgb(20, 83, 45)',
      },
      '.cm-prompt-diff-removed-text': {
        backgroundColor: 'rgba(239, 68, 68, 0.34)',
        borderRadius: '2px',
        color: 'rgb(127, 29, 29)',
      },
    }),
    dashboardCodeScrollerMarker,
    createDiffDecorationExtension(lineClassNames, rangeClassNames),
  ]

  if (readOnly) {
    extensions.push(EditorView.editable.of(false))
  }

  // 如果外部传了 theme prop 则使用，否则从 context 自动获取
  const effectiveTheme = theme ?? resolvedTheme

  return (
    <div
      data-dashboard-code-editor="true"
      className={`custom-scrollbar min-h-0 overflow-hidden rounded-md border ${className}`}
    >
      <CodeMirror
        className="min-h-0"
        value={value}
        height={height}
        minHeight={minHeight}
        maxHeight={maxHeight}
        style={{ height, minHeight, maxHeight }}
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
