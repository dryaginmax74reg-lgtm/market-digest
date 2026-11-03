"""Bounded publication readiness check. Does not send messages or retry delivery."""
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode
from urllib.request import Request, urlopen


def publication_url(value):
    p = urlsplit(value.strip())
    if p.scheme not in ('https', 'http') or not p.netloc or p.username or p.password:
        raise ValueError('DASHBOARD_URL must be an absolute public HTTP(S) URL')
    path = p.path
    if path.endswith(('/index.html', '/index.htm')):
        path = path.rsplit('/', 1)[0] + '/'
    return urlunsplit((p.scheme, p.netloc, path.rstrip('/') + '/digest.json', '', ''))


def wait_for_site(base, expected, max_wait=60, fetch=None, clock=time.monotonic, sleep=time.sleep):
    url = publication_url(base)
    deadline = clock() + max_wait
    def get(url, timeout):
        with urlopen(Request(url, headers={'Cache-Control': 'no-cache', 'User-Agent': 'market-digest'}), timeout=timeout) as r:
            return json.load(r)
    fetch = fetch or get
    attempts = 0
    while clock() < deadline:
        attempts += 1
        try:
            got = fetch(url + '?' + urlencode({'v': expected.get('revision', expected['updated'])}),
                        timeout=min(5, max(.1, deadline - clock())))
            key = 'revision' if expected.get('revision') else 'updated'
            if isinstance(got, dict) and got.get(key) == expected[key]:
                print(f'Сайт обновлён; проверок: {attempts}')
                return True
        except (OSError, ValueError, KeyError, TypeError):
            pass
        sleep(min(5, max(0, deadline - clock())))
    print('Сайт ещё публикуется; выпуск собран, отправка продолжится без повторного запуска')
    return False


if __name__ == '__main__':
    try:
        expected = json.loads(Path('docs/digest.json').read_text())
        wait_for_site(os.environ.get('DASHBOARD_URL', ''), expected)
    except (OSError, ValueError) as ex:
        print(f'Проверка сайта пропущена: {ex}', file=sys.stderr)
