import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from html.parser import HTMLParser
from unittest.mock import patch

import alerts
import common
import digest
import evening
import events
import interpretation
import live


DATES = ['2026-11-02', '2026-10-30', '2026-10-29', '2026-10-28', '2026-10-27', '2026-10-26']


def history():
    return [dict(date=d, points={1: 14+i*.1, 2: 14+i*.1, 5: 15+i*.2, 10: 16+i*.3})
            for i, d in enumerate(DATES)]


class HTMLCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, tag


class MarketIntelligence(unittest.TestCase):
    def test_trading_observations_units_and_slope(self):
        a = common.curve_analysis(history() + [history()[0]], date(2026, 11, 2))
        self.assertAlmostEqual(a['changes'][1][10], -30)
        self.assertAlmostEqual(a['changes'][5][2], -50)
        self.assertAlmostEqual(a['slope_bp'], 200)
        self.assertAlmostEqual(a['slope_changes'][5], -100)
        self.assertEqual(a['base_dates'][1], '2026-10-30')
        self.assertEqual(a['base_dates'][5], '2026-10-26')

    def test_missing_invalid_future_and_stale(self):
        a = common.curve_analysis(history()[:2], date(2026, 11, 2))
        self.assertNotIn(5, a['changes'])
        self.assertTrue(a['warnings'])
        self.assertFalse(common.curve_analysis(history(), date(2026, 11, 10))['changes'])
        self.assertFalse(common.curve_analysis(history(), date(2026, 11, 2), False)['changes'])
        a = common.curve_analysis([dict(date='2026-11-03', points={2: 12}),
                                  dict(date='bad', points={2: 12}),
                                  dict(date='2026-11-02', points={2: float('nan'), 10: float('inf')})], date(2026, 11, 2))
        self.assertFalse(a['points'])
        h = history()
        del h[1]['points'][10]
        a = common.curve_analysis(h, date(2026, 11, 2))
        self.assertNotIn(10, a['changes'][1])
        self.assertNotIn(1, a['slope_changes'])

    def test_loader_moves_back_from_returned_trade_date(self):
        with patch.object(common, 'moex_zcyc', side_effect=history()) as get:
            a = common.moex_curve_analysis(date(2026, 11, 2))
        self.assertEqual(get.call_args_list[2].args[0], date(2026, 10, 29))
        self.assertIn(5, a['changes'])
        with patch.object(common, 'moex_zcyc', side_effect=[history()[0], RuntimeError('offline')]):
            a = common.moex_curve_analysis(date(2026, 11, 2))
        self.assertFalse(a['changes'])
        self.assertTrue(a['warnings'])

    def test_loader_skips_empty_dates_but_not_network_errors(self):
        snapshots = [common.NoCurveData('weekend'), common.NoCurveData('weekend')] + history()
        with patch.object(common, 'moex_zcyc', side_effect=snapshots) as get:
            result = common.moex_curve_analysis(date(2026, 11, 4))
        self.assertIn(5, result['changes'])
        self.assertEqual(get.call_args_list[1].args[0], date(2026, 11, 3))
        with patch.object(common, 'moex_zcyc', side_effect=common.NoCurveData('empty')) as get:
            result = common.moex_curve_analysis(date(2026, 11, 2))
        self.assertEqual(get.call_count, 32)
        self.assertFalse(result['changes'])
        self.assertTrue(result['warnings'])

    def test_auction_records_not_merged(self):
        title = ('Минфин разместил ОФЗ 26238 на 96,4 млрд руб. при спросе 133,4 млрд руб. '
                 '28.10.2026 доходность отсечения 14,25%, средневзвешенная доходность 14,20%')
        r = events.parse_auction_result(title, 'https://example.com/a', 'СМИ')
        self.assertEqual(r['issues'], ['26238'])
        self.assertEqual(r['date'], '2026-10-28')
        self.assertEqual(r['placed'], 96.4)
        self.assertAlmostEqual(r['demand_to_placed'], 133.4/96.4)
        self.assertEqual(r['cutoff_yield'], 14.25)
        self.assertEqual(r['average_yield'], 14.2)
        self.assertIsNone(r['market_premium_bp'])
        with patch.object(events, 'news', return_value=[('СМИ', title, 'https://example.com/a'),
                                                        ('Другое СМИ', title, 'https://example.com/b')]):
            a = events.ofz_auction_results(date(2026, 10, 29))
        self.assertEqual(a['placed'], 96.4)
        self.assertEqual(len(a['records']), 2)
        r = events.parse_auction_result('Итоги ОФЗ', '', '')
        self.assertIsNone(r['demand_to_placed'])
        self.assertIsNone(r['cutoff_yield'])
        self.assertIsNone(r['date'])
        self.assertEqual(events.parse_auction_title('Минфин разместил ОФЗ на 1,2 трлн руб. при спросе 2 трлн руб.'), (1200, 2000))

    def test_calendar_bounds_sources_and_uncertainty(self):
        today = date(2026, 11, 2)
        rows, warnings = events.calendar_events(today, {'divs': [{'registry': 'bad'}]})
        for placements in ([{'book': '2026-11-03', 'name': 'Issuer'}], {'items': [{'book': '2026-11-03', 'name': 'Issuer'}]}):
            books, _ = events.calendar_events(today, {'placements': placements})
            self.assertTrue(any(x['type'] == 'book' for x in books))
        self.assertTrue(all('2026-11-02' <= x['date'] <= '2026-11-08' for x in rows))
        self.assertTrue(any(x['verified'] and x['date'] == '2026-11-05' for x in rows))
        self.assertTrue(all(not x['verified'] for x in rows if x['type'] == 'auc'))
        self.assertTrue(warnings)
        self.assertNotIn('биржа закрыта', events.week_ahead(today, {}))
        future, warnings = events.calendar_events(date(2028, 1, 1), {})
        self.assertTrue(any('исчерпан' in x for x in warnings))
        expired, warnings = events.calendar_events(date(2027, 2, 10), {})
        self.assertTrue(all(not x['verified'] for x in expired))
        self.assertTrue(any('повторной' in x for x in warnings))
        strip = digest.week_strip({}, today)
        self.assertEqual(len(strip), 7)
        self.assertEqual(sum(len(x['items']) for x in strip), len(rows))

    def test_rules_escape_and_existing_signals(self):
        for kind in ('OFZ', 'RGBITR', 'CNY', 'USD', 'BRENT', 'GOLD', 'BTC', 'KEY', 'OTHER'):
            for sign in (-1, 1):
                text = interpretation.explain(common.e('<untrusted>'), kind, sign)
                self.assertIn('<b>Факт:</b>', text)
                self.assertIn('<b>Гипотеза:</b>', text)
                self.assertIn('<b>Советнику:</b>', text)
                self.assertNotIn('<untrusted>', text)

    def test_offline_telegram_demo_and_rendering(self):
        now = datetime(2026, 11, 2, 18, tzinfo=common.MSK)
        data = digest.demo_data()
        data['curve_analysis'] = common.curve_analysis(history(), now.date())
        title = 'Минфин разместил ОФЗ 26238 на 10 млрд руб. при спросе 20 млрд руб.'
        data['auctions']['records'] = [events.parse_auction_result(title, 'https://example.com', 'СМИ')]
        # Block every network request, even with dummy credentials available.
        with patch.dict(os.environ, {'DRY_RUN': '1', 'TG_BOT_TOKEN': 'dummy', 'TG_CHAT_ID': 'dummy'}), \
             patch.object(common, 'http_get', side_effect=AssertionError('Network forbidden')), \
             patch.object(events, 'news', return_value=[]), \
             patch.object(events, 'watch_news', return_value=[]), \
             redirect_stdout(io.StringIO()) as output:
            blocks = digest.build_blocks(data, now)
            blocks.append(evening.build(evening.demo(), now))
            blocks.append(interpretation.explain('ОФЗ: доходность +30 б.п.', 'OFZ'))
            messages = common.pack([b for b in blocks if b])
            common.deliver(messages)
            live.update({'live': {'lines': [], 'id': None}}, now, [blocks[-1]])
            for text in messages:
                self.assertLessEqual(len(text), 3900)
                checker = HTMLCheck()
                checker.feed(text)
                self.assertFalse(checker.stack)
        self.assertIn('КРИВАЯ ОФЗ', output.getvalue())
        self.assertIn('КАЛЕНДАРЬ НА 7 ДНЕЙ', output.getvalue())
        self.assertIn('Гипотеза:', output.getvalue())


if __name__ == '__main__':
    unittest.main()
