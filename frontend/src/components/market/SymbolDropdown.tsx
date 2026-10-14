import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ListPlus, Star } from 'lucide-react';
import { useWatchlist } from '@/lib/watchlist';

/** K 线图页左上角品种下拉：列表 = 自选品种，点选直接切换。 */
export default function SymbolDropdown({ current }: { current: string }) {
  const navigate = useNavigate();
  const { favorites } = useWatchlist();
  const [open, setOpen] = useState(false);

  // 列表 = 自选 ∪ 当前品种（当前未收藏也能快速切回）
  const items = current && !favorites.includes(current) ? [current, ...favorites] : favorites;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex items-center gap-1.5"
        title="切换自选品种"
      >
        <h1 className="text-xl font-semibold text-white font-mono group-hover:text-blue-300 transition-colors">
          {current || '—'}
        </h1>
        <ChevronDown className="w-4 h-4 text-gray-500 group-hover:text-gray-300" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full mt-2 z-50 w-60 card p-1.5 shadow-xl max-h-80 overflow-y-auto">
            <p className="px-2 py-1 text-2xs uppercase tracking-wider text-gray-500">
              自选品种
            </p>
            {items.length === 0 ? (
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  navigate('/market');
                }}
                className="w-full text-left px-2 py-2 text-xs text-gray-400 hover:bg-surface-700 rounded flex items-center gap-1.5"
              >
                <ListPlus className="w-3.5 h-3.5 flex-shrink-0" />
                自选列表为空 — 去行情页点 ☆ 添加
              </button>
            ) : (
              items.map((name) => (
                <button
                  key={name}
                  type="button"
                  disabled={name === current}
                  onClick={() => {
                    setOpen(false);
                    if (name !== current) navigate(`/market/${name}`);
                  }}
                  className={`w-full flex items-center justify-between px-2 py-1.5 rounded text-sm font-mono transition-colors ${
                    name === current
                      ? 'opacity-60 cursor-default text-gray-400'
                      : 'text-gray-200 hover:bg-surface-700'
                  }`}
                >
                  <span>{name}</span>
                  {name === current ? (
                    <span className="text-2xs text-profit">当前</span>
                  ) : (
                    <Star className="w-3 h-3 fill-yellow-400 text-yellow-400" />
                  )}
                </button>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
