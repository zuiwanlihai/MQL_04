import { useEffect, useRef } from 'react';
import {
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { EquityPoint } from '@/types/api';

const TIME_FMT_FULL = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

const TIME_FMT_SHORT = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

const TIME_FMT_CLOCK = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

/** 根据跨度选择合适的 tick 格式。同一天 → HH:mm；跨天 → MM/DD HH:mm；跨年 → YYYY/MM/DD HH:mm */
function pickTickFormatter(startEpoch: number, endEpoch: number) {
  const spanDays = (endEpoch - startEpoch) / 86400;
  if (spanDays < 1) return (time: number) => TIME_FMT_CLOCK.format(new Date(time * 1000));
  if (spanDays < 400) return (time: number) => TIME_FMT_SHORT.format(new Date(time * 1000));
  return (time: number) => TIME_FMT_FULL.format(new Date(time * 1000));
}

function baseOptions() {
  return {
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' as const },
      textColor: '#9ca3af',
      fontSize: 11,
    },
    grid: {
      vertLines: { color: 'rgba(55, 65, 81, 0.4)' },
      horzLines: { color: 'rgba(55, 65, 81, 0.4)' },
    },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor: '#374151' },
    timeScale: {
      borderColor: '#374151',
      timeVisible: true,
    },
    localization: {
      timeFormatter: (time: number) => TIME_FMT_FULL.format(new Date(time * 1000)),
    },
    handleScroll: true,
    handleScale: true,
  };
}

function toEpoch(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000);
}

/** 资金曲线：equity + balance + baseline（无操作基准）。 */
export function EquityChart({ points, height = 320 }: { points: EquityPoint[]; height?: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const equityRef = useRef<ISeriesApi<'Line'> | null>(null);
  const balanceRef = useRef<ISeriesApi<'Line'> | null>(null);
  const baselineRef = useRef<ISeriesApi<'Line'> | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, baseOptions());
    equityRef.current = chart.addLineSeries({ color: '#60a5fa', lineWidth: 2, title: '净值' });
    balanceRef.current = chart.addLineSeries({ color: '#a78bfa', lineWidth: 1, title: '余额' });
    baselineRef.current = chart.addLineSeries({
      color: '#6b7280',
      lineWidth: 1,
      lineStyle: 2,
      title: '基准',
    });
    chartRef.current = chart;
    const observer = new ResizeObserver((entries) => {
      const { width, height: h } = entries[0].contentRect;
      chart.applyOptions({ width, height: h });
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current) return;
    if (!points.length) {
      chartRef.current.timeScale().fitContent();
      return;
    }
    // lightweight-charts 要求 time 严格递增 —— 同一时间戳的多条记录
    // 必须去重，否则 setData 直接断言失败导致整个页面白屏。
    // 保留每个 time 的最后一个（后产生的权益值覆盖先前的）。
    const seen = new Set<number>();
    const deduped = [...points]
      .sort((a, b) => toEpoch(a.time) - toEpoch(b.time))
      .filter((p) => {
        const t = toEpoch(p.time);
        if (seen.has(t)) return false;
        seen.add(t);
        return true;
      });
    // 按数据跨度选择 tick 格式（跨天必须带 MM/DD，否则用户看到乱序）
    const startEpoch = toEpoch(deduped[0].time);
    const endEpoch = toEpoch(deduped[deduped.length - 1].time);
    chartRef.current.applyOptions({
      timeScale: {
        tickMarkFormatter: pickTickFormatter(startEpoch, endEpoch),
      },
    });
    const data = deduped.map((p) => ({ time: toEpoch(p.time) as UTCTimestamp }));
    equityRef.current?.setData(deduped.map((p, i) => ({ ...data[i], value: p.equity })));
    balanceRef.current?.setData(deduped.map((p, i) => ({ ...data[i], value: p.balance })));
    baselineRef.current?.setData(deduped.map((p, i) => ({ ...data[i], value: p.baseline })));
    chartRef.current.timeScale().fitContent();
  }, [points]);

  return <div ref={containerRef} style={{ height }} className="w-full" />;
}

/** 回撤曲线（百分比，向下）。 */
export function DrawdownChart({ points, height = 200 }: { points: EquityPoint[]; height?: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, baseOptions());
    seriesRef.current = chart.addAreaSeries({
      lineColor: '#ef4444',
      topColor: 'rgba(239,68,68,0.35)',
      bottomColor: 'rgba(239,68,68,0.02)',
      lineWidth: 1,
      title: '回撤%',
    });
    chartRef.current = chart;
    const observer = new ResizeObserver((entries) => {
      const { width, height: h } = entries[0].contentRect;
      chart.applyOptions({ width, height: h });
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current || !seriesRef.current) return;
    const seen = new Set<number>();
    const deduped = [...points]
      .sort((a, b) => toEpoch(a.time) - toEpoch(b.time))
      .filter((p) => {
        const t = toEpoch(p.time);
        if (seen.has(t)) return false;
        seen.add(t);
        return true;
      });
    if (deduped.length >= 2) {
      const startEpoch = toEpoch(deduped[0].time);
      const endEpoch = toEpoch(deduped[deduped.length - 1].time);
      chartRef.current.applyOptions({
        timeScale: {
          tickMarkFormatter: pickTickFormatter(startEpoch, endEpoch),
        },
      });
    }
    seriesRef.current.setData(
      deduped.map((p) => ({ time: toEpoch(p.time) as UTCTimestamp, value: -p.drawdown })),
    );
    chartRef.current.timeScale().fitContent();
  }, [points]);

  return <div ref={containerRef} style={{ height }} className="w-full" />;
}
