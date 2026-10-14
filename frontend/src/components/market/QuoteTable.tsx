import { Link, useNavigate } from 'react-router-dom';
import { Star } from 'lucide-react';
import type { Quote, Symbol } from '@/types/api';
import {
  changeClass,
  formatPercent,
  formatPrice,
  formatQuoteTime,
} from '@/lib/market';
import { useThemeStore } from '@/store/useThemeStore';

interface Props {
  symbols: Symbol[];
  quotes: Map<string, Quote>;
  /** 自选集合 + 切换（行情总览传入；缺省时不显示星标列） */
  favorites?: Set<string>;
  onToggleFavorite?: (name: string) => void;
  /** 空态文案（自选 tab 传入引导语） */
  emptyHint?: string;
}

export default function QuoteTable({ symbols, quotes, favorites, onToggleFavorite, emptyHint }: Props) {
  const navigate = useNavigate();
  const redUpGreenDown = useThemeStore((s) => s.redUpGreenDown);
  const showFav = Boolean(favorites && onToggleFavorite);
  if (symbols.length === 0) {
    return (
      <div className="card">
        <div className="card-body text-center text-gray-500 py-12">
          {emptyHint ?? '没有匹配的品种，请调整分组或搜索条件。'}
        </div>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <table className="table-dark">
        <thead>
          <tr>
            {showFav && <th className="w-8" aria-label="自选" />}
            <th>品种</th>
            <th>名称</th>
            <th className="text-right">买价</th>
            <th className="text-right">卖价</th>
            <th className="text-right">点差</th>
            <th className="text-right">涨跌幅</th>
            <th className="text-right">更新时间</th>
            <th className="text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {symbols.map((s) => {
            const q = quotes.get(s.name);
            const digits = s.digits || 5;
            return (
              <tr
                key={s.name}
                onClick={() => navigate(`/market/${s.name}`)}
                className="cursor-pointer"
                title="点击查看 K 线图"
              >
                {showFav && (
                  <td>
                    <button
                      type="button"
                      title={favorites!.has(s.name) ? '移出自选' : '加入自选'}
                      onClick={(e) => {
                        e.stopPropagation();
                        onToggleFavorite!(s.name);
                      }}
                      className="p-0.5 transition-colors"
                    >
                      <Star
                        className={`w-3.5 h-3.5 ${
                          favorites!.has(s.name)
                            ? 'fill-yellow-400 text-yellow-400'
                            : 'text-gray-600 hover:text-yellow-400'
                        }`}
                      />
                    </button>
                  </td>
                )}
                <td className="font-mono text-blue-300">{s.name}</td>
                <td className="text-gray-400 max-w-56 truncate">{s.description}</td>
                <td className="text-right font-mono">
                  {q ? formatPrice(q.bid, digits) : <span className="text-gray-600">—</span>}
                </td>
                <td className="text-right font-mono">
                  {q ? formatPrice(q.ask, digits) : <span className="text-gray-600">—</span>}
                </td>
                <td className="text-right font-mono text-gray-400">
                  {q ? q.spread : '—'}
                </td>
                <td
                  className={`text-right font-mono ${q ? changeClass(q.change_percent, redUpGreenDown) : 'text-gray-600'}`}
                >
                  {q ? formatPercent(q.change_percent) : '—'}
                </td>
                <td className="text-right font-mono text-gray-500 text-xs">
                  {q ? formatQuoteTime(q.bar_time ?? q.time) : '—'}
                </td>
                <td className="text-right">
                  <Link
                    to={`/market/${s.name}`}
                    onClick={(e) => e.stopPropagation()}
                    className="text-blue-400 hover:text-blue-300 text-sm"
                  >
                    详情
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
