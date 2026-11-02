import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import alerts
import digest
import evening
import live
from common import MSK


def row(**changes):
    r = dict(SECID='SU26238RMFS4', SHORTNAME='ОФЗ 26238', BOARD='TQOB',
             PREVPRICE=100, PREVWAPRICE=100, LAST=99, WAPRICE=99.4,
             VALTODAY=2e6, NUMTRADES=20, YIELDATWAPRICE=15.3,
             YIELDATPREVWAPRICE=15)
    r.update(changes)
    return r


class QuietAlerts(unittest.TestCase):
    def test_price_and_yield_thresholds(self):
        signals = alerts.ofz_candidates([row()])[0][3]
        self.assertEqual(len(signals), 2)
        self.assertFalse(alerts.ofz_candidates([row(LAST=99.1, YIELDATWAPRICE=15.29)]))
        self.assertTrue(alerts.ofz_candidates([row(LAST=100, YIELDATWAPRICE=15.3)]))
        self.assertTrue(alerts.ofz_candidates([row(LAST=None, YIELDATWAPRICE=15.3)]))

    def test_liquidity_and_confirmation(self):
        for changes in (dict(VALTODAY=999999), dict(NUMTRADES=4),
                        dict(WAPRICE=100.1), dict(WAPRICE=None)):
            with self.subTest(changes=changes):
                self.assertFalse(alerts.ofz_candidates([row(**changes)]))
        self.assertFalse(alerts.ofz_candidates([row(WAPRICE=99.6, YIELDATWAPRICE=None)]))

    def test_all_ofz_types_and_ruble_turnover(self):
        for name in ('ОФЗ 26238', 'ОФЗ 29006', 'ОФЗ 52005', 'ОФЗ 33 CNY'):
            with self.subTest(name=name):
                self.assertTrue(alerts.ofz_candidates([row(SHORTNAME=name, SECID='RU000A10DQA8',
                    VALTODAY=1e5, VALTODAY_RUR=2e6)]))
        self.assertFalse(alerts.ofz_candidates([row(VALTODAY=2e6, VALTODAY_RUR=1e5)]))

    def test_invalid_yields_still_allow_price(self):
        for value in (None, 0, -1, 100, float('nan'), float('inf'), 'bad'):
            result = alerts.ofz_candidates([row(YIELDATWAPRICE=value)])
            self.assertEqual(len(result[0][3]), 1)
            self.assertNotIn('доходность', result[0][2])
        self.assertFalse(alerts.ofz_candidates([row(LAST=float('nan'), WAPRICE=float('inf'))]))

    def test_excluded_assets_and_duplicates(self):
        self.assertFalse(alerts.ofz_candidates([row(BOARD='TQCB'), row(SHORTNAME='Корпорация'),
                                              row(SHORTNAME='Государственная облигация')]))
        self.assertEqual(len(alerts.ofz_candidates([row(), row()])), 1)
        keys = [x[0] for x in alerts.moves(alerts.demo())]
        self.assertNotIn('IMOEX', keys)
        self.assertNotIn('RUCBTRNS', keys)

    def test_collection_allowlist(self):
        with patch.object(alerts, 'parallel', side_effect=lambda jobs: jobs):
            self.assertEqual(set(alerts.collect()), {'CBRT', 'RGBITR', 'CNY', 'BRENT', 'GOLD', 'BTC', 'BONDS'})

    def run_main(self, state, rows):
        now = datetime(2026, 11, 2, 12, tzinfo=MSK)
        data = dict(BONDS=rows, SHARES=[row()], RATINGS={'x': {'to': 'D'}},
                    KEYNEWS=[('forbidden', 'https://example.com')],
                    CBRT={'date': '03.11.2026', 'rates': {'USD': (102, 100), 'EUR': (103, 100), 'CNY': (14, 13)}})
        with patch.object(alerts, 'datetime') as dt, patch('events.is_workday', return_value=True), \
             patch.object(alerts, 'load_state', return_value=state), \
             patch.object(alerts, 'save_state'), patch.object(alerts, 'load_cfg'), \
             patch.object(alerts, 'collect', return_value=data), \
             patch.object(live, 'update') as update, patch.object(alerts, 'deliver') as deliver, \
             patch('sys.argv', ['alerts.py']):
            dt.now.return_value = now
            alerts.main()
            deliver.assert_not_called()
            return update.call_args.args[2]

    def test_repeats_reversals_and_independent_metrics(self):
        state = dict(levels={}, titles=[])
        self.assertTrue(self.run_main(state, [row(YIELDATWAPRICE=None)]))
        self.assertFalse(self.run_main(state, [row(YIELDATWAPRICE=None)]))
        self.assertTrue(self.run_main(state, [row()]))  # new yield signal
        self.assertFalse(self.run_main(state, [row()]))
        self.assertTrue(self.run_main(state, [row(LAST=98.5)]))
        self.assertTrue(self.run_main(state, [row(LAST=101, WAPRICE=100.6, YIELDATWAPRICE=14.7)]))

    def test_overflow_is_deferred(self):
        rows = [row(SECID=f'SU{i}', SHORTNAME=f'ОФЗ {i}') for i in range(15)]
        state = dict(levels={}, titles=[])
        lines = self.run_main(state, rows)
        self.assertEqual(sum('<b>ОФЗ' in x for x in lines), 12)
        self.assertIn('ещё 3', lines[-1])
        self.assertEqual(sum('<b>ОФЗ' in x for x in self.run_main(state, rows)), 3)
        self.assertFalse(self.run_main(state, rows))

    def test_day_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'state.json')
            with open(path, 'w') as f:
                json.dump(dict(date='2026-10-30', levels={'old': 2}, titles=['old']), f)
            with patch.object(alerts, 'STATE', path):
                self.assertEqual(alerts.load_state('2026-11-02')['levels'], {})

    def test_issuer_names(self):
        cases = {
            'Публичное акционерное общество «Газпром»': 'Газпром',
            'Акционерное общество Сбербанк': 'Сбербанк',
            'ООО «Ромашка»': 'Ромашка',
            'МКПАО «ЭН+ ГРУП»': 'ЭН+ ГРУП',
            'Компания «Ромашка» Финанс (АО)': 'Компания «Ромашка» Финанс',
            'Общество с ограниченной ответственностью без кавычек': 'без кавычек',
            None: '',
        }
        for full, short in cases.items():
            self.assertEqual(digest._short_issuer(full), short)
        data = evening.demo()
        data['RATINGS'] = {'x': {'name': 'Публичное акционерное общество «Газпром»', 'from': 'AA', 'to': 'A'}}
        for text in (evening.caption(data, datetime.now(MSK)), evening.build(data, datetime.now(MSK))):
            self.assertIn('Газпром', text)
            self.assertNotIn('Публичное', text)


if __name__ == '__main__':
    unittest.main()
