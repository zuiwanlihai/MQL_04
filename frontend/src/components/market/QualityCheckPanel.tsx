import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Gauge, Loader2, SearchX, ShieldAlert } from 'lucide-react';
import { useDataQuality } from '@/hooks/useMarket';
import { apiError } from '@/lib/api';
import type { DataQualityReport, MarketTimeframe, QualityIssue } from '@/types/api';

const TIMEFRAMES: MarketTimeframe[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

const VERDICT_STYLES: Record<DataQualityReport['verdict'], { label: string; cls: string }> = {
  ok: { label: '数据良好', cls: 'bg-profit/15 text-profit border-profit/40' },
  warn: { label: '存在警告', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/40' },
  poor: { label: '质量较差', cls: 'bg-loss/15 text-loss border-loss/40' },
};

const SEVERITY_STYLES: Record<QualityIssue['severity'], string> = {
  info: 'text-gray-500',
  warning: 'text-amber-300',
  error: 'text-loss',
};

function fmtTs(epoch: number | null): string {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleString('zh-CN', { hour12: false });
}

/** datetime-local 值 → epoch 秒（本地时区）。 */
function toEpoch(value: string): number {
  return Math.floor(new Date(value).getTime() / 1000);
}

/** 默认窗口：从 7 天前到现在。 */
function defaultWindow(): { start: string; end: string } {
  const now = new Date();
  const weekAgo = new Date(now.getTime() - 7 * 86_400_000);
  const fmt = (d: Date) => {
    const pad = (x: number) => String(x).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  return { start: fmt(weekAgo), end: fmt(now) };
}

function StatItem({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded border border-surface-700 px-2 py-1.5">
      <div className="text-[10px] text-gray-500">{label}</div>
      <div className={`text-sm font-mono ${tone ?? 'text-gray-200'}`}>{value}</div>
    </div>
  );
}

export default function QualityCheckPanel() {
  const check = useDataQuality();
  const [symbol, setSymbol] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState<MarketTimeframe>('H1');
  const [window_, setWindow_] = useState(defaultWindow);

  const submit = () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    check.mutate({
      symbol: sym,
      timeframe,
      start: toEpoch(window_.start),
      end: toEpoch(window_.end),
    });
  };

  const report = check.data;

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <Gauge className="w-4 h-4 text-gray-500" />
        <h2 className="text-sm font-medium text-gray-300">数据质量检查</h2>
        <span className="text-xs text-gray-600">缺口 / 异常值（P1，即时分析不落库）</span>
      </div>

      <div className="card">
        <div className="card-body space-y-3">
          <p className="text-xs text-gray-500">
            从网关拉取指定时间窗的 K 线并检测缺口（周末休市自动识别为正常）、无效 OHLC、价格跳变、重复时间戳与零成交。
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-gray-500">品种</span>
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="EURUSD"
                className="input w-32 font-mono"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-gray-500">周期</span>
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value as MarketTimeframe)}
                className="input w-24"
              >
                {TIMEFRAMES.map((tf) => (
                  <option key={tf} value={tf}>{tf}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-gray-500">开始时间</span>
              <input
                type="datetime-local"
                value={window_.start}
                onChange={(e) => setWindow_((w) => ({ ...w, start: e.target.value }))}
                className="input w-56"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-gray-500">结束时间</span>
              <input
                type="datetime-local"
                value={window_.end}
                onChange={(e) => setWindow_((w) => ({ ...w, end: e.target.value }))}
                className="input w-56"
              />
            </label>
            <button
              type="button"
              className="btn-primary h-9 px-4 disabled:opacity-50"
              disabled={check.isPending || !symbol.trim()}
              onClick={submit}
            >
              {check.isPending ? (
                <Loader2 className="w-4 h-4 inline animate-spin mr-1" />
              ) : (
                <Gauge className="w-4 h-4 inline mr-1" />
              )}
              开始检查
            </button>
          </div>
          {check.isError && (
            <p className="text-sm text-loss">检查失败：{apiError(check.error)}</p>
          )}
        </div>
      </div>

      {report && (
        <>
          <div className="card">
            <div className="card-body space-y-3">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded border text-xs font-medium ${VERDICT_STYLES[report.verdict].cls}`}>
                    {VERDICT_STYLES[report.verdict].label}
                  </span>
                  <span className="font-mono text-lg text-white">{report.quality_score}</span>
                  <span className="text-xs text-gray-500">/ 100</span>
                  <span className="text-xs text-gray-500 ml-2">
                    {report.symbol} · {report.timeframe} · 分析 {report.bars_analyzed.toLocaleString()} 根
                  </span>
                </div>
                {report.truncated && (
                  <span className="flex items-center gap-1 text-xs text-amber-300">
                    <AlertTriangle className="w-3.5 h-3.5" />
                    数据量超出单次分析上限，结果被截断
                  </span>
                )}
              </div>
              <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
                <StatItem label="缺口" value={report.gap_count} tone={report.gap_count ? 'text-loss' : undefined} />
                <StatItem label="周末休市" value={report.weekend_gap_count} />
                <StatItem label="无效 OHLC" value={report.invalid_count} tone={report.invalid_count ? 'text-loss' : undefined} />
                <StatItem label="价格跳变" value={report.spike_count} tone={report.spike_count ? 'text-amber-300' : undefined} />
                <StatItem label="重复时间戳" value={report.duplicate_count} tone={report.duplicate_count ? 'text-loss' : undefined} />
                <StatItem label="零成交" value={report.zero_volume_count} />
              </div>
            </div>
          </div>

          {report.issues.length > 0 && (
            <div className="card overflow-hidden">
              <table className="table-dark">
                <thead>
                  <tr>
                    <th className="w-16">级别</th>
                    <th className="w-32">类型</th>
                    <th>说明</th>
                    <th className="w-44">时间</th>
                  </tr>
                </thead>
                <tbody>
                  {report.issues.map((issue, i) => (
                    <tr key={`${issue.code}-${issue.start_time ?? i}-${i}`}>
                      <td>
                        {issue.severity === 'error' && <ShieldAlert className="w-4 h-4 text-loss" />}
                        {issue.severity === 'warning' && <AlertTriangle className="w-4 h-4 text-amber-300" />}
                        {issue.severity === 'info' && <CheckCircle2 className="w-4 h-4 text-gray-500" />}
                      </td>
                      <td className={`font-mono text-xs ${SEVERITY_STYLES[issue.severity]}`}>{issue.code}</td>
                      <td className="text-gray-200">{issue.message}</td>
                      <td className="text-xs text-gray-500">
                        {fmtTs(issue.start_time)}
                        {issue.end_time && issue.end_time !== issue.start_time && (
                          <> → {fmtTs(issue.end_time)}</>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {report.issues.length === 0 && report.bars_analyzed === 0 && (
            <div className="card">
              <div className="card-body flex items-center gap-2 text-sm text-gray-500">
                <SearchX className="w-4 h-4" />
                该时间窗内没有 K 线数据，请调整品种或时间范围。
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
