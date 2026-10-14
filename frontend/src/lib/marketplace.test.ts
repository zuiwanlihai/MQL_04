import { describe, expect, it } from 'vitest';
import { buildListingQuery } from './marketplace';

describe('buildListingQuery', () => {
  it('默认参数只带分页（latest 排序不传）', () => {
    expect(buildListingQuery({ page: 1, pageSize: 20 })).toEqual({
      page: 1,
      page_size: 20,
      symbol: undefined,
      timeframe: undefined,
      q: undefined,
      sort: undefined,
    });
  });

  it('symbol 归一大写、q 去空格、popular 保留', () => {
    expect(
      buildListingQuery({
        page: 2,
        pageSize: 12,
        symbol: ' eurusd ',
        q: ' 双均线 ',
        sort: 'popular',
      }),
    ).toEqual({
      page: 2,
      page_size: 12,
      symbol: 'EURUSD',
      timeframe: undefined,
      q: '双均线',
      sort: 'popular',
    });
  });

  it('空字符串筛选剔除，timeframe 透传', () => {
    expect(
      buildListingQuery({ page: 1, pageSize: 20, symbol: '  ', q: '', timeframe: 'H4' }),
    ).toEqual({
      page: 1,
      page_size: 20,
      symbol: undefined,
      timeframe: 'H4',
      q: undefined,
      sort: undefined,
    });
  });
});
