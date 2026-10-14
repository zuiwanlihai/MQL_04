"""Backtest task orchestration (M4 Phase D).

Modules:
    data_fetcher — gateway rates_range pagination + warm-up extension
    supervisor   — sandbox runner subprocess lifecycle + resource caps
    queue        — asyncio worker pool: CAS claim → compile → fetch →
                   spawn → event persistence → terminal state
"""
