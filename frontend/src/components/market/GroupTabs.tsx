import type { SymbolGroup } from '@/types/api';

type GroupValue = SymbolGroup | '' | 'fav';

const TABS: { value: GroupValue; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'fav', label: '自选' },
  { value: 'forex', label: '外汇' },
  { value: 'commodities', label: '商品' },
  { value: 'indices', label: '指数' },
  { value: 'crypto', label: '加密货币' },
  { value: 'stocks', label: '股票' },
];

interface Props {
  value: GroupValue;
  onChange: (value: GroupValue) => void;
}

export default function GroupTabs({ value, onChange }: Props) {
  return (
    <div className="flex items-center gap-1 bg-surface-900 rounded-lg p-1 border border-gray-700/50">
      {TABS.map((tab) => (
        <button
          key={tab.value || 'all'}
          onClick={() => onChange(tab.value)}
          className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
            value === tab.value
              ? 'bg-surface-700 text-white'
              : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
