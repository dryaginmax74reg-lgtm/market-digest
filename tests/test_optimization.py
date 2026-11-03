import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import bonds_meta
import common
import digest
import events
import interpretation
import official_events
from tools.wait_site import publication_url, wait_for_site


FIX = Path(__file__).parent / 'fixtures'


class Optimizations(unittest.TestCase):
    def test_site_url_spaces_query_and_html(self):
        expected = 'https://example.com/market/digest.json'
        for url in (' https://example.com/market/ ', 'https://example.com/market/index.html?v=overview#details'):
            self.assertEqual(publication_url(url), expected)
        with self.assertRaises(ValueError):
            publication_url('garbage')
        with self.assertRaises(ValueError):
            publication_url('https://user:secret@example.com/')

    def test_site_wait_bound_and_revision_not_just_minute(self):
        now = [0]
        sleeps = []
        def sleep(n):
            sleeps.append(n)
            now[0] += n
        with redirect_stdout(io.StringIO()):
            result = wait_for_site('https://example.com', {'updated': 'same-minute', 'revision': 'new'}, max_wait=12,
                fetch=lambda *a, **k: {'updated': 'same-minute', 'revision': 'old'}, clock=lambda: now[0], sleep=sleep)
        self.assertFalse(result)
        self.assertEqual(sum(sleeps), 12)
        calls = []
        def fetch(url, timeout):
            calls.append(url)
            return {'revision': 'new'}
        with redirect_stdout(io.StringIO()):
            self.assertTrue(wait_for_site(' https://example.com/ ', {'updated':'x', 'revision':'new'}, fetch=fetch))
        self.assertEqual(len(calls), 1)

    def test_public_revision_changes_with_content_in_same_minute(self):
        now = datetime(2026, 11, 2, 7, 40, tzinfo=common.MSK)
        data = digest.demo_data()
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, '__file__', tmp+'/digest.py'), patch.object(events, 'news', return_value=[]):
            Path(tmp+'/docs').mkdir()
            path = digest.write_public(data, now)
            first = json.loads(Path(path).read_text())
            data['brent'] = (50, 100, 60)
            digest.write_public(data, now)
            second = json.loads(Path(path).read_text())
        self.assertEqual(first['updated'], second['updated'])
        self.assertNotEqual(first['revision'], second['revision'])

    def test_corrupt_calendar_cannot_suppress_fallback(self):
        today = date(2027, 2, 10)
        bad = {'schema':1, 'sources':{key:{'checked':today.isoformat(), 'attempt':today.isoformat(), 'rows':[]} for key in ('cbr','fed','minfin')}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(official_events,'_get',side_effect=AssertionError('No extra attempt')):
            common.atomic_json(tmp+'/cache', bad)
            result = official_events.collect(today, tmp+'/cache')
        self.assertFalse(result['calendar_sources'])
        self.assertTrue(result['warnings'])
        rows, _ = events.calendar_events(today, {'official_events':result})
        self.assertTrue(any(x['type']=='cbr' for x in rows))

    def test_snapshot_same_msk_day_and_pair_integrity(self):
        now = datetime(2026, 11, 3, 7, 40, tzinfo=common.MSK)
        with tempfile.TemporaryDirectory() as tmp, patch.object(bonds_meta, 'META', tmp+'/meta'), patch.object(bonds_meta, 'STATE', tmp+'/state'):
            def save(stamp, state_stamp=None):
                common.atomic_json(tmp+'/meta', {'updated':stamp, 'rated':1000, 'b':{'RU':[]}})
                common.atomic_json(tmp+'/state', {'updated':state_stamp or stamp, 'ratings':{'RU':'AAA'}, 'days':['2026-11-02']})
            save('2026-11-03 06:30')
            self.assertTrue(bonds_meta.snapshot_fresh(now))
            with patch.object(bonds_meta, 'datetime') as dt, patch.object(bonds_meta, 'emitters', side_effect=AssertionError('Must skip collection')), patch.object(sys, 'argv', ['bonds_meta.py','--if-stale']):
                dt.now.return_value = now
                dt.fromisoformat = datetime.fromisoformat
                bonds_meta.main()
            save('2026-11-02 22:28')
            self.assertFalse(bonds_meta.snapshot_fresh(now))
            save('2026-11-03 09:00')
            self.assertFalse(bonds_meta.snapshot_fresh(now))
            save('2026-11-03 06:30','2026-11-02 06:30')
            self.assertFalse(bonds_meta.snapshot_fresh(now))
            Path(tmp+'/meta').write_text('invalid')
            self.assertFalse(bonds_meta.snapshot_fresh(now))

    def test_curve_closed_cache_and_live_refresh(self):
        today = datetime.now(common.MSK).date()
        past = today - timedelta(days=1)
        def block(dt):
            return {'yearyields':{'columns':['tradedate','tradetime','period','value'], 'data':[[dt.isoformat(),'18:50:00',2,14.2],[dt.isoformat(),'18:50:00',10,15.3]]}}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CURVE_CACHE_PATH':tmp+'/curves'}), patch.object(common,'iss_json') as request:
            common._CURVE_MEMORY.clear()
            request.side_effect = [block(past), block(today), block(today)]
            a = common.moex_zcyc(past)
            common._CURVE_MEMORY.clear()  # next process: history reused
            self.assertEqual(common.moex_zcyc(past), a)
            common.moex_zcyc(today)
            common.moex_zcyc()
            self.assertEqual(request.call_count, 2)
            common._CURVE_MEMORY.clear()
            common.moex_zcyc(today)
            self.assertEqual(request.call_count, 3)  # no persisted intraday snapshot
            cache = common.read_json(tmp+'/curves')
            self.assertNotIn(today.isoformat(), cache['queries'])
            cache['queries'][past.isoformat()]['checked'] = (today-timedelta(days=8)).isoformat()
            common.atomic_json(tmp+'/curves',cache)
            common._CURVE_MEMORY.clear()
            request.side_effect = None
            request.return_value = block(past)
            common.moex_zcyc(past)
            self.assertEqual(request.call_count,4)

    def test_empty_weekend_cached_errors_not_cached(self):
        past = datetime.now(common.MSK).date() - timedelta(days=1)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CURVE_CACHE_PATH':tmp+'/curves'}), patch.object(common,'iss_json',return_value={'yearyields':{'columns':[], 'data':[]}}) as request:
            common._CURVE_MEMORY.clear()
            with self.assertRaises(common.NoCurveData): common.moex_zcyc(past)
            common._CURVE_MEMORY.clear()
            with self.assertRaises(common.NoCurveData): common.moex_zcyc(past)
            self.assertEqual(request.call_count,1)
            Path(tmp+'/curves').write_text('corrupt')
            common._CURVE_MEMORY.clear()
            request.side_effect = OSError('network')
            with self.assertRaises(OSError): common.moex_zcyc(past)
            self.assertEqual(Path(tmp+'/curves').read_text(),'corrupt')

    def test_primary_calendars_parse_and_change_detection(self):
        cbr = official_events.parse_cbr((FIX/'cbr_calendar.html').read_text())
        fed = official_events.parse_fed((FIX/'fomc_calendar.html').read_text())
        self.assertEqual(len([x for x in cbr if x['type']=='cbr']),8)
        self.assertEqual(len(fed),8)
        self.assertIn('2027-12-17', [x['date'] for x in cbr])
        self.assertIn('2027-12-08', [x['date'] for x in fed])
        for parse in (official_events.parse_cbr, official_events.parse_fed):
            with self.assertRaises(ValueError): parse('<html>maintenance</html>')
        official = {'calendar_sources':['cbr','fed'], 'calendar': [dict(date='2026-12-17',type='cbr',title='Перенесённое заседание',importance='высокая',source=official_events.CBR,checked='2026-12-16',verified=True)]}
        rows,_ = events.calendar_events(date(2026,12,16), {'official_events':official})
        self.assertTrue(any(x['date']=='2026-12-17' and x['verified'] for x in rows))
        self.assertFalse(any(x['date']=='2026-12-18' and x['type']=='cbr' for x in rows))

    def test_official_daily_cache_negative_cache_and_expiry(self):
        now=date(2027,2,10)
        cbr=(FIX/'cbr_calendar.html').read_text()
        fed=(FIX/'fomc_calendar.html').read_text()
        def get(url):
            return cbr if url==official_events.CBR else fed
        with tempfile.TemporaryDirectory() as tmp, patch.object(official_events,'_get',side_effect=get) as request, patch.object(official_events,'fetch_minfin',side_effect=OSError('unavailable')) as minfin:
            a=official_events.collect(now,tmp+'/cache')
            self.assertEqual(set(a['calendar_sources']),{'cbr','fed'})
            self.assertTrue(a['warnings'])
            official_events.collect(now,tmp+'/cache')
            self.assertEqual(request.call_count,2)
            self.assertEqual(minfin.call_count,1)
            request.side_effect=OSError('offline')
            b=official_events.collect(now+timedelta(days=1),tmp+'/cache')
            self.assertTrue(b['calendar'])
            c=official_events.collect(now+timedelta(days=9),tmp+'/cache')
            self.assertFalse(c['calendar'])
            self.assertTrue(c['warnings'])

    def test_primary_auction_and_incomplete_media(self):
        raw='<h1>Результаты аукциона ОФЗ-ПД 26238 от 28.10.2026</h1><p>Объем размещения 96400 млн руб. Объем спроса 133400 млн руб. Доходность отсечения 14,25%</p>'
        row=official_events.parse_minfin_document(raw,official_events.MINFIN,date(2026,10,29))
        self.assertTrue(row['verified'])
        self.assertAlmostEqual(row['placed'],96.4)
        self.assertAlmostEqual(row['demand'],133.4)
        self.assertEqual(row['date'],'2026-10-28')
        self.assertIsNone(official_events.parse_minfin_document(raw,official_events.MINFIN,date(2026,12,1)))
        media=events.parse_auction_result('Минфин разместил ОФЗ 26238 и ОФЗ 26240 на 10 млрд при спросе 20 млрд, доходность отсечения 15%', 'https://example.com','СМИ')
        self.assertIsNone(media['cutoff_yield'])
        self.assertIn('дата',media['missing_fields'])
        self.assertFalse(media['verified'])
        media.update(date=row['date'],issues=row['issues'])
        combined=events.combine_auction_results({'records':[media]},[row],date(2026,10,29))
        self.assertEqual(len(combined['records']),1)
        self.assertEqual(combined['placed'],row['placed'])

    def test_compact_signals_keep_facts_and_rule_boundaries(self):
        for kind in ('OFZ','RGBITR','CNY','CBRT','BRENT','GOLD','BTC'):
            full=interpretation.explain('<b>Факт рынка</b>',kind,1)
            short=interpretation.explain('<b>Факт рынка</b>',kind,1,compact=True)
            self.assertLess(len(short),len(full))
            for text in (full,short):
                self.assertIn('<b>Факт рынка</b>',text)
                self.assertIn('<b>Гипотеза:</b>',text)
                self.assertIn('<b>Советнику:</b>',text)


if __name__=='__main__': unittest.main()
