"""Daily checked primary calendars; unavailable or changed pages fail closed."""
import re
from datetime import date, timedelta
from html import unescape
from urllib.parse import urljoin, urlsplit
from concurrent.futures import ThreadPoolExecutor

from common import MONTHS, atomic_json, http_get, read_json

CBR = 'https://www.cbr.ru/dkp/cal_mp/'
FED = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
MINFIN = 'https://minfin.gov.ru/ru/perfomance/public_debt/internal/operations/ofz/auction'


def plain(raw):
    raw = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', raw, flags=re.S | re.I)
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', raw))).strip()


def parse_cbr(raw):
    text = plain(raw)
    pattern = r'(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(20\d{2})\s+года'
    hits = list(re.finditer(pattern, text, re.I))
    rows = []
    for i, m in enumerate(hits):
        chunk = text[m.end(): hits[i+1].start() if i+1 < len(hits) else len(text)]
        dt = date(int(m[3]), MONTHS.index(m[2].lower())+1, int(m[1])).isoformat()
        if 'Заседание Совета директоров Банка России по ключевой ставке' in chunk:
            rows.append(dict(date=dt, type='cbr', title='Решение ЦБ по ключевой ставке, ориентир 13:30 МСК', importance='высокая'))
        elif 'Резюме обсуждения ключевой ставки' in chunk:
            rows.append(dict(date=dt, type='cbr_summary', title='Резюме обсуждения ключевой ставки ЦБ', importance='средняя'))
    if len({r['date'] for r in rows if r['type'] == 'cbr'}) < 8:
        raise ValueError('CBR calendar structure changed or is incomplete')
    return list({(r['date'], r['type']): r for r in rows}.values())


def parse_fed(raw):
    text = plain(raw)
    years = list(re.finditer(r'(20\d{2}) FOMC Meetings', text))
    months = 'January February March April May June July August September October November December'.split()
    rows = []
    for i, y in enumerate(years):
        chunk = text[y.end(): years[i+1].start() if i+1 < len(years) else len(text)]
        chunk = chunk.split('Note:')[0]
        entries = re.findall(r'('+'|'.join(months)+r')(?:/('+'|'.join(months)+r'))?\s+(\d{1,2})\s*[-–]\s*(\d{1,2})', chunk)
        if len(entries) != 8:
            continue  # Never certify a partial calendar as a complete year.
        for month, end_month, first, last in entries:
            dt = date(int(y[1]), months.index(end_month or month)+1, int(last))
            rows.append(dict(date=dt.isoformat(), type='fomc', title='Решение FOMC; время уточнить в календаре ФРС', importance='высокая'))
    if not rows:
        raise ValueError('FOMC calendar structure changed or is incomplete')
    return rows


def _get(url):
    return http_get(url, headers={'User-Agent': 'Mozilla/5.0'}, retries=1, timeout=6).decode('utf-8', 'replace')


def parse_minfin_document(raw, url, today):
    # Import lazily to keep events/calendar formatting free of network side effects.
    from events import parse_auction_result
    body = re.split(r'<h1\b[^>]*>', raw, maxsplit=1, flags=re.I)
    text = plain(body[-1])
    record = parse_auction_result(text, url, 'Минфин России')
    explicit = re.search(r'аукцион\w*[^.]{0,100}?(\d{2}\.\d{2}\.20\d{2})', text, re.I)
    # Only explicitly stated units are converted; bare numbers are not assumed to be billions.
    for key, label in (("placed", r"(?:объ[её]м\s+размещени\w*|размещ[её]нн\w*\s+объ[её]м)"),
                       ("demand", r"(?:объ[её]м\s+спрос\w*|спрос)")):
        if record[key] is not None:
            continue
        unit = r"(тыс|млн|млрд|трлн)"
        number = r"(\d[\d \xa0]*(?:[,.]\d+)?)"
        match = re.search(label + r"[^\d]{0,25}?" + number + r"\s*" + unit, text, re.I)
        if match:
            value, scale = match[1], match[2]
        else:
            match = re.search(label + r"[^\d]{0,10}?" + unit + r"[^\d]{0,15}?" + number, text, re.I)
            if not match:
                continue
            scale, value = match[1], match[2]
        record[key] = float(value.replace(' ', '').replace('\xa0', '').replace(',', '.')) * {
            'тыс': .000001, 'млн': .001, 'млрд': 1, 'трлн': 1000}[scale.lower()]
    if not explicit or len(record['issues']) != 1:
        return None
    day, month, year = map(int, explicit[1].split('.'))
    dt = date(year, month, day)
    if not today - timedelta(days=14) <= dt <= today or record['placed'] is None:
        return None
    record.update(date=dt.isoformat(), verified=True, source_kind='official', title=text[:300])
    record['demand_to_placed'] = record['demand'] / record['placed'] if record['placed'] and record['demand'] is not None else None
    record['missing_fields'] = [label for label, value in (('спрос', record['demand']), ('доходность отсечения', record['cutoff_yield'])) if value is None]
    record['warnings'] = ['Премия к рынку: нет сопоставимой котировки на время аукциона']
    return record


def fetch_minfin(today):
    raw = _get(MINFIN)
    links = []
    for href, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw, re.S | re.I):
        label = plain(label)
        url = urljoin(MINFIN, unescape(href))
        if (urlsplit(url).scheme == 'https' and urlsplit(url).hostname in ('minfin.gov.ru', 'www.minfin.gov.ru')
                and re.search(r'результат|итог', label, re.I) and 'ОФЗ' in label and url not in links):
            links.append(url)
    records = []
    for url in links[:3]:
        record = parse_minfin_document(_get(url), url, today)
        if record:
            records.append(record)
    if not records:
        raise ValueError('No dated OFZ results found in official archive')
    return list({(r['date'], tuple(r['issues'])): r for r in records}.values())


def collect(today, path):
    cache = read_json(path)
    if not isinstance(cache, dict) or cache.get('schema') != 1:
        cache = {'schema': 1, 'sources': {}}
    slots = cache.get('sources')
    if not isinstance(slots, dict):
        slots = cache['sources'] = {}
    specs = {'cbr': (CBR, lambda: parse_cbr(_get(CBR))),
             'fed': (FED, lambda: parse_fed(_get(FED))),
             'minfin': (MINFIN, lambda: fetch_minfin(today))}
    warnings = []
    todo = {k: v for k, v in specs.items() if not isinstance(slots.get(k), dict) or slots[k].get('attempt') != today.isoformat()}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {key: pool.submit(spec[1]) for key, spec in todo.items()}
        for key, future in futures.items():
            previous = slots.get(key) if isinstance(slots.get(key), dict) else {}
            try:
                rows = future.result()
                if key in ('cbr', 'fed'):
                    decisions = [r for r in rows if r['type'] in ('cbr', 'fomc')]
                    if (sum(r['date'].startswith(str(today.year)) for r in decisions) < 8
                            or not any(r['date'] >= today.isoformat() for r in decisions)):
                        raise ValueError('Official calendar does not cover the current horizon')
                slots[key] = dict(checked=today.isoformat(), attempt=today.isoformat(), rows=rows, error=None)
            except Exception as ex:
                slots[key] = dict(previous, attempt=today.isoformat(), error=type(ex).__name__)
    output = {'calendar': [], 'auctions': [], 'warnings': warnings, 'calendar_sources': []}
    for key, (url, _) in specs.items():
        slot = slots[key]
        try:
            age = (today - date.fromisoformat(slot.get('checked', ''))).days
        except (TypeError, ValueError):
            age = 999
        if slot.get('error'):
            warnings.append({'cbr': 'ЦБ', 'fed': 'ФРС', 'minfin': 'Минфин'}[key] + ': первоисточник недоступен или изменил формат; проверка не обновлена')
        if not 0 <= age <= 7 or not isinstance(slot.get('rows'), list):
            continue
        if key != 'minfin':
            decisions = [r for r in slot['rows'] if isinstance(r, dict) and r.get('type') in ('cbr', 'fomc')
                         and isinstance(r.get('date'), str)]
            if (len({r['date'] for r in decisions if r['date'].startswith(str(today.year))}) < 8
                    or not any(r['date'] >= today.isoformat() for r in decisions)):
                warnings.append('Сохранённый календарь неполон; используется резерв с предупреждениями')
                continue
            output['calendar_sources'].append(key)
        for row in slot['rows']:
            if not isinstance(row, dict) or not isinstance(row.get('date'), str):
                continue
            try:
                date.fromisoformat(row['date'])
            except ValueError:
                continue
            if key != 'minfin' and not all(isinstance(row.get(k), str) for k in ('type', 'title', 'importance')):
                continue
            row = dict(row, source=row.get('source', url) if key == 'minfin' else url, checked=slot['checked'], verified=True)
            output['auctions' if key == 'minfin' else 'calendar'].append(row)
    try:
        atomic_json(path, cache)
    except OSError:
        warnings.append('Кэш первоисточников не сохранён')
    return output
