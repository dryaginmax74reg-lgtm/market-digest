import tempfile
import unittest
from datetime import date,datetime
from pathlib import Path
from unittest.mock import patch

import credit_events
import digest
import insights
from common import MSK,pack


def change(sec='RU000A10ATC4', old='BB', new='C', issuer='Публичное акционерное общество "МГКЛ"', em=11004):
    return dict(secid=sec,name='МГКЛ-1P7',issuer=issuer,em=em,**{'from':old,'to':new,'date':'2026-11-04'})


class CreditEvents(unittest.TestCase):
    def test_c_is_not_a_payment_default_and_names_are_readable(self):
        for target in ('CCC','CC','C'):
            data={'bstate':{'rating_changes':[change(new=target)]},'credit_disclosures':[]}
            rows=digest.rating_day(data,date(2026,11,4))
            self.assertFalse(rows[0][5])
            self.assertEqual(rows[0][1],'МГКЛ')
            data['_rating_day']=rows
            status=insights.day_status(data,date(2026,11,4))
            self.assertNotIn('дефолт', str(status).lower())
            self.assertIn('BB→'+target,str(status))
        data={'bstate':{'rating_changes':[change(new='D')]},'credit_disclosures':[]}
        self.assertTrue(digest.rating_day(data,date(2026,11,4))[0][5])

    def test_same_issuer_different_transitions_and_duplicate_rows(self):
        a=change()
        data={'bstate':{'rating_changes':[a,a,change('RU2',old='A',new='BBB')]}}
        rows=credit_events.groups(data,date(2026,11,4))
        self.assertEqual(len(rows),2)
        self.assertEqual(sum(len(r['issues']) for r in rows),2)
        self.assertEqual({(r['old'],r['new']) for r in rows},{('BB','C'),('A','BBB')})

    def test_all_issues_are_visible_with_detection_dates(self):
        rows=[change('RU'+str(i)) for i in range(9)]
        rows.append(change('RU_BALT',old='AA-',new='A+',issuer='общество с ограниченной ответственностью "Балтийский лизинг"',em=7010))
        rows[-1]['date']='2026-11-02'
        data={'bstate':{'rating_changes':rows},'credit_disclosures':[]}
        text=digest.build_ratings(data,date(2026,11,4))
        self.assertIn('МГКЛ: BB → C',text)
        self.assertIn('выпусков: 9',text)
        self.assertIn('Балтийский лизинг: AA- → A+',text)
        self.assertIn('выявлено 2026-11-02',text)
        for r in rows: self.assertIn(r['secid'],text)
        self.assertNotIn('Публичное акционерное',text)

    def test_confirmed_report_is_one_issue_not_nine_defaults(self):
        data={'bstate':{'rating_changes':[change('RU'+str(i)) for i in range(9)]}}
        lines=credit_events.payment_lines(data,date(2026,11,4))
        self.assertEqual(len(lines),1)
        self.assertIn('RU000A10ATC4',lines[0])
        self.assertIn('2026-11-02',lines[0])
        self.assertIn('https://www.ra-national.ru/press_release/mgkl/42719_d/',lines[0])
        self.assertIn('остальным выпускам',lines[0])
        self.assertFalse(credit_events.load_disclosures(date(2026,11,20)))
        self.assertFalse(credit_events.load_disclosures(date(2026,10,30)))

    def test_caption_and_story_keep_issuer_and_rating_change(self):
        data=digest.demo_data()
        data['bstate']={'rating_changes':[change()]}
        data['_rating_day']=digest.rating_day(data,date(2026,11,4))
        data['_status']=insights.day_status(data,date(2026,11,4))
        now=datetime(2026,11,4,9,tzinfo=MSK)
        caption=digest.caption(data,now)
        self.assertIn('МГКЛ',caption)
        self.assertIn('BB→C',caption)
        self.assertIn('RU000A10ATC4',caption)
        story=insights.story(data,now.date(),now,data['_status'],[],[])
        self.assertIn('МГКЛ: BB → C',' '.join(story['ratings']))
        self.assertNotIn('Публичное акционерное',' '.join(story['status']['reasons']))

    def test_large_issue_group_fits_text_fallback_without_losing_ids(self):
        data={'bstate':{'rating_changes':[change('RU%06d'%i) for i in range(100)]},'credit_disclosures':[]}
        with patch.dict('os.environ',{'DASHBOARD_URL':'https://example.com/market/'}):
            blocks=digest.rating_blocks(data,date(2026,11,4))
        for message in pack(blocks): self.assertLessEqual(len(message),3900)
        text='\n'.join(blocks)
        for i in range(100): self.assertIn('RU%06d'%i,text)


if __name__=='__main__': unittest.main()
