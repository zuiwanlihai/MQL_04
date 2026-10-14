import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Check, Loader2 } from 'lucide-react';
import Editor, { type Monaco } from '@monaco-editor/react';
import type { editor as MonacoEditor } from 'monaco-editor';
import '@/lib/monacoSetup';
import { defineEditorTheme } from '@/lib/monacoSetup';
import { registerSdkCompletion } from '@/lib/sdkCompletion';
import { useSaveSource, useStrategy, useValidateSource } from '@/hooks/useStrategies';
import { apiError } from '@/lib/api';
import EditorFilePanel from '@/pages/Strategies/components/EditorFilePanel';
import EditorLogDrawer from '@/pages/Strategies/components/EditorLogDrawer';
import EditorSidePanel from '@/pages/Strategies/components/EditorSidePanel';
import type { StrategyStatus, ValidationResult } from '@/types/api';

const STATUS_LABELS: Record<StrategyStatus, string> = {
  draft: '草稿',
  active: '启用',
  paused: '暂停',
  archived: '归档',
};

const AUTOSAVE_DELAY_MS = 1200;
const VALIDATE_DELAY_MS = 800;

export default function StrategyEditorPage() {
  const { id } = useParams<{ id: string }>();
  const strategyQuery = useStrategy(id);
  const saveSource = useSaveSource();
  const validateSource = useValidateSource();

  const [code, setCode] = useState('');
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [conflict, setConflict] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  const editorRef = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const savedCodeRef = useRef('');
  const baseUpdatedAtRef = useRef<string | null>(null);
  const dirtyRef = useRef(false);
  const saveNowRef = useRef<() => void>(() => undefined);

  const strategy = strategyQuery.data;
  const archived = strategy?.status === 'archived';
  const dirty = code !== savedCodeRef.current;
  dirtyRef.current = dirty;

  /** 把校验问题映射为 Monaco 行内波浪线标记。 */
  const applyMarkers = useCallback((result: ValidationResult | null) => {
    const model = editorRef.current?.getModel();
    const monaco = monacoRef.current;
    if (!model || !monaco) return;
    const severityMap = {
      error: monaco.MarkerSeverity.Error,
      warning: monaco.MarkerSeverity.Warning,
      info: monaco.MarkerSeverity.Info,
    } as const;
    monaco.editor.setModelMarkers(
      model,
      'sdk-lint',
      (result?.issues ?? []).map((i) => ({
        startLineNumber: i.line,
        endLineNumber: i.line,
        startColumn: i.column + 1,
        endColumn: i.column + 80,
        message: `${i.code}: ${i.message}`,
        severity: severityMap[i.severity],
      })),
    );
  }, []);

  // 策略加载完成后初始化编辑缓冲区（仅在切换策略时重置）
  useEffect(() => {
    if (!strategy) return;
    savedCodeRef.current = strategy.source_code;
    baseUpdatedAtRef.current = strategy.updated_at;
    setCode(strategy.source_code);
    setConflict(false);
    setSavedAt(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy?.id]);

  // debounce 自动校验
  useEffect(() => {
    if (!strategy) return;
    const timer = window.setTimeout(() => {
      validateSource.mutate(code, {
        onSuccess: (r) => {
          setValidation(r);
          applyMarkers(r);
        },
      });
    }, VALIDATE_DELAY_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, strategy?.id]);

  const doSave = useCallback(() => {
    if (!id || dirtyRef.current === false || saveSource.isPending) return;
    saveSource.mutate(
      { id, source_code: code, base_updated_at: baseUpdatedAtRef.current },
      {
        onSuccess: (res) => {
          savedCodeRef.current = res.source_code;
          baseUpdatedAtRef.current = res.updated_at;
          setValidation(res.validation);
          applyMarkers(res.validation);
          setConflict(false);
          setSavedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }));
        },
        onError: (e) => {
          const status = (e as { response?: { status?: number } })?.response?.status;
          if (status === 409) setConflict(true);
        },
      },
    );
  }, [id, code, saveSource, applyMarkers]);

  saveNowRef.current = doSave;

  // debounce 自动保存（冲突/归档/只读时暂停）
  useEffect(() => {
    if (!dirty || archived || conflict || saveSource.isPending) return;
    const timer = window.setTimeout(doSave, AUTOSAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [dirty, archived, conflict, code, doSave, saveSource.isPending]);

  // 未保存修改时提示关闭页面
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirtyRef.current) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, []);

  const jumpToIssue = useCallback((line: number) => {
    const ed = editorRef.current;
    if (!ed) return;
    ed.revealLineInCenter(line);
    ed.setPosition({ lineNumber: line, column: 1 });
    ed.focus();
  }, []);

  const handleEditorMount = useCallback(
    (ed: MonacoEditor.IStandaloneCodeEditor, monaco: Monaco) => {
      editorRef.current = ed;
      monacoRef.current = monaco;
      defineEditorTheme();
      registerSdkCompletion(monaco);
      ed.addAction({
        id: 'save-strategy',
        label: '保存策略',
        keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS],
        precondition: undefined,
        run: () => saveNowRef.current(),
      });
      applyMarkers(validation);
    },
    [applyMarkers, validation],
  );

  if (strategyQuery.isLoading) {
    return (
      <div className="card">
        <div className="card-body text-center text-gray-500 py-10">正在加载策略…</div>
      </div>
    );
  }

  if (strategyQuery.isError || !strategy) {
    return (
      <div className="card border-loss/40 bg-loss/10">
        <div className="card-body flex items-center justify-between">
          <span className="text-sm text-loss">加载失败：{apiError(strategyQuery.error)}</span>
          <Link to="/strategies" className="btn-secondary">
            返回策略列表
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex flex-col gap-3"
      style={{ height: 'calc(100vh - 104px)' }}
    >
      <div className="flex items-center gap-3 flex-wrap shrink-0">
        <Link
          to="/strategies"
          className="btn-ghost w-9 h-9 p-0 flex items-center justify-center"
          title="返回策略列表"
        >
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <h1 className="text-lg font-semibold text-white">{strategy.name}</h1>
        <span
          className={`px-2 py-0.5 rounded border text-xs ${
            archived ? 'bg-gray-500/15 text-gray-400 border-gray-500/40' : 'bg-blue-500/15 text-blue-300 border-blue-500/40'
          }`}
        >
          {STATUS_LABELS[strategy.status]}
        </span>
        <span className="text-xs text-gray-500 font-mono">
          {strategy.current_version == null ? '未冻结版本' : `当前 v${strategy.current_version}`}
        </span>
        <div className="ml-auto flex items-center gap-3">
          {conflict ? (
            <span className="text-xs text-loss">检测到外部修改，自动保存已暂停，请手动保存或刷新</span>
          ) : saveSource.isPending ? (
            <span className="text-xs text-gray-400 flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> 保存中…
            </span>
          ) : dirty ? (
            <span className="text-xs text-yellow-400">● 未保存</span>
          ) : savedAt ? (
            <span className="text-xs text-profit flex items-center gap-1">
              <Check className="w-3 h-3" /> 已保存 {savedAt}
            </span>
          ) : null}
          <button
            type="button"
            className="btn-primary px-3 py-1.5"
            disabled={!dirty || saveSource.isPending || archived}
            onClick={doSave}
          >
            保存 (Ctrl+S)
          </button>
        </div>
      </div>

      {saveSource.isError && !conflict && (
        <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-xs text-loss shrink-0">
          保存失败：{apiError(saveSource.error)}
        </div>
      )}

      <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[240px_minmax(0,1fr)_300px] gap-3">
        <div className="hidden lg:flex min-h-0">
          <EditorFilePanel
            strategyName={strategy.name}
            onLoadTemplate={(name, source) => {
              if (
                dirtyRef.current &&
                !window.confirm(`载入模板「${name}」将覆盖当前未保存的修改，是否继续？`)
              ) {
                return;
              }
              setCode(source);
            }}
          />
        </div>

        <div className="card flex flex-col overflow-hidden min-h-0 min-w-0">
          <div className="flex-1 min-h-0">
            <Editor
              path={`strategy-${strategy.id}.py`}
              language="python"
              theme="mt5-dark"
              value={code}
              onChange={(v) => setCode(v ?? '')}
              onMount={handleEditorMount}
              options={{
                fontSize: 13,
                fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                automaticLayout: true,
                tabSize: 4,
                readOnly: archived,
                renderWhitespace: 'selection',
                padding: { top: 8 },
              }}
            />
          </div>
          <EditorLogDrawer
            validation={validation}
            open={logOpen}
            onToggle={() => setLogOpen((o) => !o)}
            onJumpToIssue={jumpToIssue}
          />
        </div>

        <div className="hidden xl:flex min-h-0">
          <EditorSidePanel
            strategy={strategy}
            dirty={dirty}
            saving={saveSource.isPending || validateSource.isPending}
            validation={validation}
            onJumpToIssue={jumpToIssue}
            onSaved={() => {
              void strategyQuery.refetch().then((r) => {
                if (r.data) baseUpdatedAtRef.current = r.data.updated_at;
              });
            }}
          />
        </div>
      </div>
    </div>
  );
}
