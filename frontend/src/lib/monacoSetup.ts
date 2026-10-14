/**
 * Monaco 本地化加载：禁止 @monaco-editor/react 默认的 jsdelivr CDN loader，
 * 全部资源打包进应用（验收：Network 面板 0 个 jsdelivr 请求）。
 *
 * 必须在任何 <Editor> 渲染前 import 本模块（编辑器页面顶部 import 即可）。
 */
import * as monaco from 'monaco-editor';
import editorWorker from 'monaco-editor/editor/editor.worker?worker';
import { loader } from '@monaco-editor/react';

self.MonacoEnvironment = {
  getWorker(): Worker {
    return new editorWorker();
  },
};

loader.config({ monaco });

/** 暗色主题：与平台 Tailwind 暗色调一致（红涨绿跌不影响编辑器）。 */
export function defineEditorTheme(): void {
  monaco.editor.defineTheme('mt5-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: 'comment', foreground: '64748b', fontStyle: 'italic' },
      { token: 'keyword', foreground: '7dd3fc' },
      { token: 'string', foreground: '86efac' },
      { token: 'number', foreground: 'fbbf24' },
      { token: 'type', foreground: 'c4b5fd' },
      { token: 'function', foreground: '93c5fd' },
    ],
    colors: {
      'editor.background': '#0f172a',
      'editorGutter.background': '#0f172a',
      'editor.lineHighlightBackground': '#1e293b80',
      'editorLineNumber.foreground': '#475569',
      'editorLineNumber.activeForeground': '#94a3b8',
      'editorWidget.background': '#1e293b',
      'editorSuggestWidget.background': '#1e293b',
      'editorSuggestWidget.selectedBackground': '#334155',
      'minimap.background': '#0f172a',
    },
  });
}

export { monaco };
