"""Credit event labels: rating observations and sourced payment reports stay separate."""
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from bondscore import RAT, rscore
from common import e, pages_url


def issuer_name(name):
    name = (name or '').strip()
    short = re.sub(r'^(?:Международная компания публичное акционерное общество|Общество с ограниченной ответственностью|Публичное акционерное общество|Акционерное общество|Открытое акционерное общество|Закрытое акционерное общество|МКПАО|МКАО|ООО|ПАО|АО|ОАО|ЗАО)\s+', '', name, flags=re.I).strip()
    short = re.sub(r'\s*\((?:ПАО|АО|ООО|МКПАО|МКАО)\)\s*$', '', short).strip()
    if len(short)>1 and (short[0],short[-1]) in (('«','»'), ('"','"'), ('“','”')):
        short=short[1:-1].strip()
    return short or name


def groups(data, today, days=1):
    out = {}
    seen = set()
    for row in ((data.get('bstate') or {}).get('rating_changes') or []):
        try:
            dt=date.fromisoformat(row['date'])
            old,new=row['from'],row['to']
            if old not in RAT or new not in RAT or old==new or not 0 <= (today-dt).days < days:
                continue
            key=(row.get('em') or row.get('issuer') or row.get('name') or row['secid'], old,new,row['date'])
            identity=(key,row['secid'])
            if identity in seen:
                continue
            seen.add(identity)
            g=out.setdefault(key,dict(name=issuer_name(row.get('issuer') or row.get('name') or row['secid']),
                                     old=old,new=new,date=row['date'],issues=[],down=rscore(new)>rscore(old),
                                     default_rating=new=='D',critical=new in ('CCC','CC','C')))
            g['issues'].append(dict(id=row['secid'], name=row.get('name') or row['secid']))
        except (KeyError,TypeError,ValueError):
            continue
    return sorted(out.values(),key=lambda x:(not x['default_rating'],not x['critical'],not x['down'],-rscore(x['new']),x['name']))


def daily(data,today):
    return [('🔴' if g['default_rating'] else '⚠️' if g['critical'] else '⬇️' if g['down'] else '⬆️',
             g['name'],g['old'],g['new'],len(g['issues']),g['default_rating'],g['down']) for g in groups(data,today)]


def load_disclosures(today):
    try:
        rows=json.loads((Path(__file__).parent/'data'/'credit_disclosures.json').read_text())
    except (OSError,ValueError):
        return []
    if not isinstance(rows,list):
        return []
    valid=[]
    for row in rows:
        try:
            if (row.get('kind')=='technical_default' and row.get('source_url','').startswith('https://www.ra-national.ru/press_release/')
                    and 0 <= (today-date.fromisoformat(row['date'])).days <= 7
                    and all(isinstance(row.get(k),str) and row[k] for k in ('issuer','issue_name','isin','source','summary'))):
                valid.append(row)
        except (AttributeError,ValueError,TypeError,KeyError):
            continue
    return valid


def disclosures(data,today):
    return data.get('credit_disclosures') if data.get('credit_disclosures') is not None else load_disclosures(today)


def issue_link(issue):
    base=pages_url()
    label=e(issue['name'])+' · '+e(issue['id'])
    if not base:
        return label
    url=base.rstrip('/')+'/bonds.html?q='+quote(issue['id'],safe='')
    return '<a href="'+e(url)+'">'+label+'</a>'


def payment_lines(data,today):
    lines=[]
    for row in disclosures(data,today):
        lines.append('⚠️ <b>'+e(row['issuer'])+': технический дефолт по купону</b>\n'
                     + e(row['issue_name'])+' · '+e(row['isin'])+'\n'
                     + 'По сообщению <a href="'+e(row['source_url'])+'">'+e(row['source'])+' от '+e(row['date'])+'</a>. '
                     +e(row['summary']))
    return lines
