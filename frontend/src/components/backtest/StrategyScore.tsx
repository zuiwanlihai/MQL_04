import type { StrategyScore } from '@/types/api';

const GRADE_COLORS: Record<StrategyScore['grade'], { bg: string; text: string; ring: string }> = {
  S: { bg: 'bg-gradient-to-br from-amber-400 to-orange-500', text: 'text-white', ring: 'ring-amber-400/40' },
  A: { bg: 'bg-gradient-to-br from-emerald-400 to-green-600', text: 'text-white', ring: 'ring-emerald-400/40' },
  B: { bg: 'bg-gradient-to-br from-sky-400 to-blue-600', text: 'text-white', ring: 'ring-sky-400/40' },
  C: { bg: 'bg-gradient-to-br from-yellow-400 to-amber-600', text: 'text-white', ring: 'ring-yellow-400/40' },
  D: { bg: 'bg-gradient-to-br from-orange-400 to-red-500', text: 'text-white', ring: 'ring-orange-400/40' },
  F: { bg: 'bg-gradient-to-br from-red-500 to-rose-700', text: 'text-white', ring: 'ring-red-500/40' },
};

const DIMENSION_WEIGHTS: Record<string, string> = {
  收益能力: '30%',
  风险调整: '30%',
  回撤控制: '20%',
  交易质量: '20%',
};

function DimensionBar({ label, value, weight }: { label: string; value: number; weight: string }) {
  const pct = Math.max(0, Math.min(100, value));
  const color =
    pct >= 80 ? 'bg-emerald-500' :
    pct >= 60 ? 'bg-sky-500' :
    pct >= 40 ? 'bg-yellow-500' :
    pct >= 20 ? 'bg-orange-500' :
    'bg-red-500';
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-sm">
        <span className="text-gray-300">
          {label}
          <span className="ml-2 text-xs text-gray-500">权重 {weight}</span>
        </span>
        <span className={`font-mono ${pct >= 60 ? 'text-emerald-400' : pct >= 40 ? 'text-yellow-400' : 'text-red-400'}`}>
          {value.toFixed(1)}
        </span>
      </div>
      <div className="h-2 bg-surface-900 rounded-full overflow-hidden border border-gray-700/50">
        <div
          className={`h-full ${color} transition-all duration-500`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function StrategyScorePanel({ score, report }: { score: StrategyScore; report: { total_trades: number; win_rate: number; profit_factor: number; sharpe_ratio: number; max_drawdown_percent: number; annual_return_percent: number } }) {
  const gc = GRADE_COLORS[score.grade];

  return (
    <div className="space-y-5">
      {/* ── 总分 + 等级徽章 ── */}
      <div className="grid grid-cols-1 md:grid-cols-[auto_1fr] gap-6 items-center">
        {/* 圆形分数 */}
        <div className={`relative w-36 h-36 rounded-full ${gc.bg} ring-4 ${gc.ring} flex flex-col items-center justify-center shadow-xl shrink-0`}>
          <span className="text-5xl font-bold text-white drop-shadow-md tabular-nums">
            {score.score.toFixed(1)}
          </span>
          <span className="text-xs text-white/90 mt-1 tracking-wider">综合评分</span>
          {/* 等级字母 */}
          <div className={`absolute -top-2 -right-2 w-10 h-10 rounded-full ${gc.bg} ring-2 ring-surface-800 flex items-center justify-center text-xl font-black ${gc.text} shadow-lg`}>
            {score.grade}
          </div>
        </div>

        {/* 右侧等级描述 + 可信度 */}
        <div className="space-y-3">
          <div>
            <h2 className="text-2xl font-bold text-gray-100">
              {score.grade_label}
              <span className="ml-3 text-sm text-gray-500 font-normal">
                等级 {score.grade} （满分 100）
              </span>
            </h2>
          </div>

          {/* 可信度条 */}
          <div className="bg-surface-900 border border-gray-700/50 rounded-lg px-4 py-3">
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-400 shrink-0">可信度</span>
              <div className="flex-1 h-1.5 bg-surface-800 rounded-full overflow-hidden">
                <div
                  className={`h-full transition-all ${
                    score.credibility >= 0.9 ? 'bg-emerald-500' :
                    score.credibility >= 0.7 ? 'bg-yellow-500' :
                    'bg-orange-500'
                  }`}
                  style={{ width: `${score.credibility * 100}%` }}
                />
              </div>
              <span className={`text-xs font-mono ${
                score.credibility >= 0.9 ? 'text-emerald-400' :
                score.credibility >= 0.7 ? 'text-yellow-400' :
                'text-orange-400'
              }`}>
                {(score.credibility * 100).toFixed(0)}%
              </span>
            </div>
            <p className="text-xs text-gray-500 mt-1.5">{score.credibility_note}</p>
          </div>
        </div>
      </div>

      {/* ── 四个维度分解 ── */}
      <div className="card">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">维度评分</h3>
          <span className="text-xs text-gray-500">加权后 = 最终分数</span>
        </div>
        <div className="card-body space-y-3">
          {Object.entries(score.dimensions).map(([label, val]) => (
            <DimensionBar
              key={label}
              label={label}
              value={val}
              weight={DIMENSION_WEIGHTS[label] ?? ''}
            />
          ))}
        </div>
      </div>

      {/* ── 亮点 & 短板 ── */}
      <div className="grid md:grid-cols-2 gap-4">
        <div className="card border-emerald-400/20">
          <div className="card-header">
            <h3 className="text-sm font-medium text-emerald-400 flex items-center gap-2">
              <span>✨</span> 亮点
            </h3>
          </div>
          <div className="card-body">
            {score.highlights.length === 0 ? (
              <p className="text-xs text-gray-500">暂无突出优势（无指标 ≥ 70 分）</p>
            ) : (
              <ul className="space-y-1.5">
                {score.highlights.map((h, i) => (
                  <li key={i} className="text-sm text-gray-200 flex items-start gap-2">
                    <span className="text-emerald-400 mt-0.5">●</span>
                    <span>{h}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="card border-red-400/20">
          <div className="card-header">
            <h3 className="text-sm font-medium text-red-400 flex items-center gap-2">
              <span>⚠️</span> 短板 / 风险
            </h3>
          </div>
          <div className="card-body">
            {score.shortcomings.length === 0 ? (
              <p className="text-xs text-gray-500">暂无明显短板</p>
            ) : (
              <ul className="space-y-1.5">
                {score.shortcomings.map((s, i) => (
                  <li key={i} className="text-sm text-gray-200 flex items-start gap-2">
                    <span className="text-red-400 mt-0.5">●</span>
                    <span>{s}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      {/* ── 评分依据（当前指标 vs 阈值）── */}
      <div className="card">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">指标诊断</h3>
          <span className="text-xs text-gray-500">各指标实际值（点击展开评分规则）</span>
        </div>
        <div className="card-body">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <IndCell label="年化收益" value={`${report.annual_return_percent.toFixed(2)}%`} ok={report.annual_return_percent >= 10} />
            <IndCell label="夏普比率" value={report.sharpe_ratio.toFixed(2)} ok={report.sharpe_ratio >= 1} />
            <IndCell label="最大回撤" value={`${report.max_drawdown_percent.toFixed(2)}%`} ok={report.max_drawdown_percent <= 20} invert />
            <IndCell label="胜率" value={`${(report.win_rate * 100).toFixed(1)}%`} ok={report.win_rate >= 0.45 && report.win_rate <= 0.7} />
            <IndCell label="盈亏比" value={report.profit_factor.toFixed(2)} ok={report.profit_factor >= 1.5} />
            <IndCell label="交易笔数" value={report.total_trades.toString()} ok={report.total_trades >= 50} />
          </div>
        </div>
      </div>
    </div>
  );
}

function IndCell({ label, value, ok, invert }: { label: string; value: string; ok: boolean; invert?: boolean }) {
  const good = invert ? !ok : ok;
  return (
    <div className="bg-surface-900 border border-gray-700/50 rounded-lg px-3 py-2 text-center">
      <div className={`text-xs ${good ? 'text-emerald-400' : 'text-red-400'}`}>{label}</div>
      <div className={`font-mono text-base mt-0.5 ${good ? 'text-gray-100' : 'text-gray-400'}`}>{value}</div>
    </div>
  );
}
