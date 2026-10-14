interface Props {
  monthly: Record<string, number>;
}

const MONTHS = ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12'];

function cellColor(v: number): string {
  const a = Math.min(Math.abs(v) / 10, 1) * 0.55 + 0.05;
  return v >= 0 ? `rgba(34,197,94,${a})` : `rgba(239,68,68,${a})`;
}

export default function MonthlyHeatmap({ monthly }: Props) {
  const entries = Object.entries(monthly ?? {});
  if (entries.length === 0) {
    return <p className="text-sm text-gray-500 py-4 text-center">无月度收益数据</p>;
  }
  const byYear = new Map<string, Map<string, number>>();
  for (const [key, v] of entries) {
    const [year, month] = key.split('-');
    if (!year || !month) continue;
    if (!byYear.has(year)) byYear.set(year, new Map());
    byYear.get(year)!.set(month.padStart(2, '0'), v);
  }
  const years = [...byYear.keys()].sort();
  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-xs">
        <thead>
          <tr>
            <th className="px-2 py-1 text-gray-500 font-normal text-left">年份</th>
            {MONTHS.map((m) => (
              <th key={m} className="px-2 py-1 text-gray-500 font-normal">
                {m}月
              </th>
            ))}
            <th className="px-2 py-1 text-gray-400 font-normal">全年</th>
          </tr>
        </thead>
        <tbody>
          {years.map((y) => {
            const row = byYear.get(y)!;
            let acc = 1;
            let has = false;
            for (const m of MONTHS) {
              const v = row.get(m);
              if (v != null) {
                acc *= 1 + v / 100;
                has = true;
              }
            }
            return (
              <tr key={y}>
                <td className="px-2 py-1 text-gray-400 font-mono">{y}</td>
                {MONTHS.map((m) => {
                  const v = row.get(m);
                  return (
                    <td
                      key={m}
                      className="px-1 py-0.5 text-center font-mono"
                      style={v == null ? undefined : { backgroundColor: cellColor(v) }}
                      title={v == null ? undefined : `${y}-${m}: ${v.toFixed(2)}%`}
                    >
                      {v == null ? <span className="text-gray-700">—</span> : v.toFixed(1)}
                    </td>
                  );
                })}
                <td className="px-2 py-0.5 text-center font-mono text-gray-300">
                  {has ? `${((acc - 1) * 100).toFixed(1)}%` : '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
