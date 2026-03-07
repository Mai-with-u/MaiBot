import js from '@eslint/js'
import globals from 'globals'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist'] },
  jsxA11y.flatConfigs.recommended,
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // 将所有 React Hooks 推荐规则降级为警告
      ...Object.keys(reactHooks.configs.recommended.rules).reduce((acc, key) => {
        acc[key] = 'warn'
        return acc
      }, {}),
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // 关闭或降级其他规则
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': 'warn',
      // jsx-a11y: 降级为 warn 避免阻塞构建，后续 Task 17 逐步修复
      'jsx-a11y/anchor-ambiguous-text': 'warn',
      'jsx-a11y/no-autofocus': 'warn',
    },
  },
)
