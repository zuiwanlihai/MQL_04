import { useEffect, useRef } from 'react';
import {
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { EquityPoint } from '@/types/api';

const TIME_FMT = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

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
      tickMarkFormatter: (time: number) => {
        const d = new Date(time * 1000);
        return TIME_FMT.format(d).replace(/^\d{4}\/\d{2}\/\d{2} /, '');
      },
    },
    localization: { timeFormatter: (time: number) => TIME_FMT.format(new Date(time * 1000)) },
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
    const sorted = [...points].sort((a, b) => toEpoch(a.time) - toEpoch(b.time));
    const data = sorted.map((p) => ({ time: toEpoch(p.time) as UTCTimestamp }));
    equityRef.current?.setData(sorted.map((p, i) => ({ ...data[i], value: p.equity })));
    balanceRef.current?.setData(sorted.map((p, i) => ({ ...data[i], value: p.balance })));
    baselineRef.current?.setData(sorted.map((p, i) => ({ ...data[i], value: p.baseline })));
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
    const sorted = [...points].sort((a, b) => toEpoch(a.time) - toEpoch(b.time));
    seriesRef.current.setData(
      sorted.map((p) => ({ time: toEpoch(p.time) as UTCTimestamp, value: -p.drawdown })),
    );
    chartRef.current.timeScale().fitContent();
  }, [points]);

  return <div ref={containerRef} style={{ height }} className="w-full" />;
}
