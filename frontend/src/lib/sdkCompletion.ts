/**
 * SDK v1 编辑器补全词表 —— 唯一维护点。
 *
 * 与 docs/SDK-v1-API规范.md §2/§5 同步：新增/废弃接口必须先改规范文档，
 * 再改本文件与后端 strategy_validator.py 白名单。
 */
import type { Monaco } from '@monaco-editor/react';
import type { languages, editor, IPosition } from 'monaco-editor';

export const SDK_CTX_API = [
  { label: 'declare', detail: 'declare(symbol, period="H1", params={}) — 仅 on_init 内调用' },
  { label: 'symbol', detail: 'ctx.symbol — 已声明的主品种' },
  { label: 'period', detail: 'ctx.period — 已声明的主周期' },
  { label: 'params', detail: 'ctx.params — 声明的参数字典' },
  { label: 'mode', detail: 'ctx.mode — "backtest" | "paper" | "live"' },
  { label: 'sdk_version', detail: 'ctx.sdk_version' },
  { label: 'strategy_id', detail: 'ctx.strategy_id' },
  { label: 'get_bars', detail: 'get_bars(symbol, period, count=100) -> list[Bar]（≤5000，截断至当前 bar）' },
  { label: 'get_quote', detail: 'get_quote(symbol) -> Quote | None' },
  { label: 'get_symbol_info', detail: 'get_symbol_info(symbol) -> SymbolInfo | None' },
  { label: 'get_account', detail: 'get_account() -> AccountInfo（只读）' },
  { label: 'get_positions', detail: 'get_positions() -> list[Position]' },
  { label: 'get_position', detail: 'get_position(position_id) -> Position | None' },
  { label: 'buy', detail: 'buy(symbol, volume, *, comment="") -> OrderResult（市价开多）' },
  { label: 'sell', detail: 'sell(symbol, volume, *, comment="") -> OrderResult（市价开空）' },
  { label: 'close', detail: 'close(position_id, *, volume=None, comment="") -> OrderResult' },
  { label: 'storage', detail: 'ctx.storage — 键值持久化（≤1MB，跨重启保留）' },
  { label: 'log', detail: 'ctx.log.info / warn / error（与 print 同源）' },
  { label: 'now', detail: 'ctx.now — 策略时钟（UTC）' },
  { label: 'current_bar', detail: 'ctx.current_bar -> Bar | None' },
] as const;

export const SDK_CALLBACKS = [
  { label: 'on_init(ctx)', detail: '必须 — 初始化：ctx.declare() 声明品种/周期/参数', insert: 'def on_init(ctx):\n    ${0}' },
  { label: 'on_bar(ctx, bar)', detail: '必须 — 每根已收盘 K 线回调（预热期同样调用）', insert: 'def on_bar(ctx, bar):\n    ${0}' },
  { label: 'on_order(ctx, order)', detail: '必须 — 成交回报回调', insert: 'def on_order(ctx, order):\n    ${0}' },
  { label: 'on_stop(ctx, reason)', detail: '可选 — 平台停止策略时收尾', insert: 'def on_stop(ctx, reason):\n    ${0}' },
  { label: 'on_tick(ctx, tick)', detail: '可选 — 真实 tick 回调，仅虚拟下单/实盘触发（回测不调用）', insert: 'def on_tick(ctx, tick):\n    ${0}' },
] as const;

/** import 白名单（标准库逐项授予 + 第三方）。 */
export const SDK_MODULES = [
  'math', 'statistics', 'datetime', 'decimal', 'collections', 'itertools',
  'functools', 'dataclasses', 'enum', 'typing', 'json', 're', 'string',
  'copy', 'random', 'numpy', 'pandas', 'scipy',
] as const;

export const SDK_INDICATORS = [
  'SMA', 'EMA', 'WMA', 'MACD', 'RSI', 'KDJ', 'BOLL', 'ATR', 'CCI', 'ADX', 'OBV',
] as const;

let registered = false;

/** 注册 SDK 补全 provider（幂等，全局仅一次）。 */
export function registerSdkCompletion(monaco: Monaco): void {
  if (registered) return;
  registered = true;

  monaco.languages.registerCompletionItemProvider('python', {
    provideCompletionItems(model: editor.ITextModel, position: IPosition) {
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };
      const line = model.getLineContent(position.lineNumber);

      const suggestions: languages.CompletionItem[] = [];

      // ctx. 成员补全
      if (/ctx\.\w*$/.test(line.slice(0, position.column - 1))) {
        for (const api of SDK_CTX_API) {
          suggestions.push({
            label: api.label,
            kind: monaco.languages.CompletionItemKind.Method,
            insertText: api.label,
            detail: api.detail,
            range,
          });
        }
      }

      // 顶层回调骨架
      if (position.column === 1 || /^\s*$/.test(line.slice(0, position.column - 1))) {
        for (const cb of SDK_CALLBACKS) {
          suggestions.push({
            label: cb.label,
            kind: monaco.languages.CompletionItemKind.Function,
            insertText: cb.insert,
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            detail: cb.detail,
            range,
          });
        }
        for (const mod of SDK_MODULES) {
          suggestions.push({
            label: `import ${mod}`,
            kind: monaco.languages.CompletionItemKind.Module,
            insertText: `import ${mod}`,
            detail: 'SDK v1 白名单模块',
            range,
          });
        }
      }

      return { suggestions };
    },
  });
}
