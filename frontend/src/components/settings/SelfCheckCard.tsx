import { useState } from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  RefreshCw,
  Wrench,
  ClipboardCopy,
} from 'lucide-react';
import { usePowerFix, useRunSelfCheck } from '@/hooks/useSelfCheck';
import { apiError } from '@/lib/api';
import { relativeTime } from '@/lib/market';
import type { SelfCheckItem, SelfCheckStatus } from '@/types/api';

const STATUS_META: Record<SelfCheckStatus, { icon: typeof CheckCircle2; cls: string; label: string }> = {
  pass: { icon: CheckCircle2, cls: 'text-profit', label: '通过' },
  warn: { icon: AlertTriangle, cls: 'text-yellow-400', label: '注意' },
  fail: { icon: XCircle, cls: 'text-loss', label: '未通过' },
};

function ItemRow({ item }: { item: SelfCheckItem }) {
  const meta = STATUS_META[item.status] ?? STATUS_META.warn;
  const Icon = meta.icon;
  const [copied, setCopied] = useState(false);

  function copyCommands() {
    if (!item.fix_commands?.length) return;
    void navigator.clipboard.writeText(item.fix_commands.join('\n')).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <li className="py-2.5 border-b border-gray-700/40 last:border-0">
      <div className="flex items-center gap-2.5">
        <Icon className={`w-4 h-4 shrink-0 ${meta.cls}`} />
        <span className="text-sm text-gray-200 flex-1 min-w-0">{item.label}</span>
        <span className={`text-xs shrink-0 ${meta.cls}`}>{meta.label}</span>
      </div>
      {item.detail && <p className="text-xs text-gray-500 mt-1 ml-7">{item.detail}</p>}
      {item.fix_hint && (
        <p className="text-xs text-gray-400 mt-1 ml-7">
          修复指引：{item.fix_hint}
          {item.fix_commands && item.fix_commands.length > 0 && (
            <button
              type="button"
              onClick={copyCommands}
              className="ml-2 inline-flex items-center gap-1 text-blue-400 hover:text-blue-300"
              title={item.fix_commands.join('\n')}
            >
              <ClipboardCopy className="w-3 h-3" />
              {copied ? '已复制' : '复制命令'}
            </button>
          )}
        </p>
      )}
    </li>
  );
}

export default function SelfCheckCard() {
  const run = useRunSelfCheck();
  const powerFix = usePowerFix();
  const [powerGuidance, setPowerGuidance] = useState<string[] | null>(null);

  const result = run.data;
  const failing = (result?.items ?? []).filter((i) => i.status !== 'pass');
  const powerItem = result?.items.find((i) => i.key === 'power_plan');

  return (
    <section className="card">
      <div className="card-header flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
          <Wrench className="w-4 h-4 text-gray-500" /> 一键自检（网关常驻运维）
        </h2>
        <div className="flex items-center gap-2">
          {result && (
            <span className="text-xs text-gray-600">上次自检：{relativeTime(result.checked_at)}</span>
          )}
          <button
            type="button"
            className="btn-secondary"
            disabled={run.isPending}
            onClick={() => run.mutate()}
          >
            <RefreshCw className={`w-4 h-4 ${run.isPending ? 'animate-spin' : ''}`} />
            {run.isPending ? '检查中…' : result ? '重新自检' : '开始自检'}
          </button>
        </div>
      </div>
      <div className="card-body space-y-3">
        <p className="text-xs text-gray-600">
          终端登录 / 券商连接 / 算法交易开关 / 电源方案 / 系统时间 / 磁盘——实盘运行前提逐项体检（PRD M1）。
        </p>

        {run.isError && (
          <div className="text-sm text-loss bg-loss/10 border border-loss/30 rounded-md px-3 py-2">
            自检失败：{apiError(run.error)}
          </div>
        )}

        {result && (
          <div
            className={`rounded-md border px-3 py-2 text-sm ${
              result.all_pass
                ? 'border-profit/40 bg-profit/5 text-profit'
                : 'border-yellow-500/40 bg-yellow-500/5 text-yellow-400'
            }`}
          >
            {result.all_pass
              ? '全部检查项通过，可以安全启动实盘。'
              : `存在 ${failing.length} 个未通过/注意项，请先按修复指引处理。`}
          </div>
        )}

        {result && (
          <ul>
            {result.items.map((item) => (
              <ItemRow key={item.key} item={item} />
            ))}
          </ul>
        )}

        {/* 电源一键修复（默认指引模式） */}
        {powerItem && powerItem.status !== 'pass' && (
          <div className="border-t border-gray-700/50 pt-3">
            {powerFix.isError && (
              <p className="text-xs text-loss mb-2">{apiError(powerFix.error)}</p>
            )}
            {powerFix.data?.executed && (
              <p className="text-xs text-profit mb-2">{powerFix.data.detail}</p>
            )}
            {powerGuidance && (
              <div className="mb-2 rounded-md bg-surface-900 border border-gray-700/50 p-3">
                <p className="text-xs text-gray-500 mb-1">请以管理员身份在 PowerShell 执行：</p>
                <pre className="text-xs font-mono text-gray-300 whitespace-pre-wrap">
                  {powerGuidance.join('\n')}
                </pre>
              </div>
            )}
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="btn-secondary"
                disabled={powerFix.isPending}
                onClick={() =>
                  powerFix.mutate(false, {
                    onSuccess: (r) => setPowerGuidance(r.commands),
                  })
                }
              >
                <Wrench className="w-4 h-4" /> 获取电源修复命令
              </button>
              <span className="text-xs text-gray-600">
                （默认不直接修改系统电源设置，仅生成命令）
              </span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
