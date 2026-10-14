import { useEffect, useRef } from 'react';
import {
  ColorType,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { NotebookChart } from '@/types/api';

const SERIES_COLORS = ['#60a5fa', '#a78bfa', '#34d399', '#fbbf24'];

/** 研究笔记本内置图表：lightweight-charts 折线，无需 matplotlib。 */
export default function NotebookLineChart({
  chart,
  height = 260,
}: {
  chart: NotebookChart;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const c = createChart(container, {
      width: container.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9ca3af',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(55, 65, 81, 0.4)' },
        horzLines: { color: 'rgba(55, 65, 81, 0.4)' },
      },
      rightPriceScale: { borderColor: '#374151' },
      timeScale: { borderColor: '#374151' },
    });
    chartRef.current = c;
    const observer = new ResizeObserver((entries) => {
      const { width } = entries[0].contentRect;
      c.applyOptions({ width });
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      c.remove();
      chartRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    const c = chartRef.current;
    if (!c) return;
    // 时间轴：全部可解析为日期 → epoch；否则退化为序号位置。
    const first = chart.series[0]?.points[0]?.time;
    const dateLike =
      !!first && !Number.isNaN(new Date(first.replace(' ', 'T')).getTime()) &&
      /\d{4}/.test(first);
    for (let i = 0; i < chart.series.length; i++) {
      const s = chart.series[i];
      const line = c.addLineSeries({
        color: SERIES_COLORS[i % SERIES_COLORS.length],
        lineWidth: 2,
        title: s.name,
      });
      const data = s.points
        .filter((p) => p.value !== null)
        .map((p, idx) => ({
          time: (
            dateLike
              ? Math.floor(new Date(p.time.replace(' ', 'T')).getTime() / 1000)
              : idx + 1
          ) as UTCTimestamp,
          value: p.value as number,
        }));
      line.setData(data);
    }
    c.timeScale().fitContent();
  }, [chart]);

  return (
    <div>
      {chart.title && (
        <p className="text-xs text-gray-400 mb-1">{chart.title}</p>
      )}
      <div ref={containerRef} className="w-full" />
    </div>
  );
}
