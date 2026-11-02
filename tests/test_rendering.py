"""Render the actual chart path in a temp folder, forbid all outbound traffic."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import card
import charts
import common
import digest
import evening
import events


class RenderRegression(unittest.TestCase):
    def test_actual_cards_and_details_without_network(self):
        now = datetime(2026, 10, 14, 7, 40, tzinfo=common.MSK)
        data = digest.demo_data()
        series, curves = charts.demo_history(now.date())
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(common, 'http_get', side_effect=AssertionError('Network forbidden')), \
             patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')), \
             patch.object(events, 'news', return_value=[]), \
             patch.object(events, 'watch_news', return_value=[]):
            morning = str(Path(tmp) / 'morning.png')
            closing = str(Path(tmp) / 'closing.png')
            collage = str(Path(tmp) / 'collage.png')
            self.assertEqual(card.render_card(data, series, now, digest.strong_moves(data, now.date()), path=morning), morning)
            closing_data = evening.demo()
            ups, downs = evening.leaders(closing_data['SHARES'])
            self.assertEqual(card.render_evening(closing_data, now, ups, downs, path=closing), closing)
            self.assertEqual(charts.render_collage(series, curves, now.date(), path=collage), collage)
            for path, expected in ((morning, (1080, 1350)), (closing, None), (collage, None)):
                with Image.open(path) as img:
                    img.verify()
                with Image.open(path) as img:
                    if expected:
                        self.assertEqual(img.size, expected)
                    if path == closing:
                        self.assertEqual(img.width, 1080)
                        self.assertTrue(1000 <= img.height <= 1050)
                    self.assertGreater(min(img.size), 500)
                    self.assertGreater(Path(path).stat().st_size, 10000)
            data['_card'] = morning
            public = digest.public_sections(data, now)
            text = '\n'.join(public['details'])
            self.assertIn('Гипотеза:', text)
            self.assertIn('Советнику:', text)
            self.assertIn('КРИВАЯ ОФЗ', text)
            self.assertIn('КАЛЕНДАРЬ НА 7 ДНЕЙ', text)


if __name__ == '__main__':
    unittest.main()
