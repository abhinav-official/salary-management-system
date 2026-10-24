#!/usr/bin/env python3
"""Step 12 rehearsal on two isolated Compose volumes. Standard library only.

Volumes are retained for inspection; containers and private credentials are removed.
Optional --report writes timings/checks without secrets or employee details.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import platform
import http.cookiejar
import io
from fractions import Fraction
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from time import perf_counter_ns
import urllib.error
import urllib.parse
import urllib.request

from acceptance_reference import display, verify, workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))
from app.passwords import hash_password  # Used only to provision a disposable HR login.

SNAPSHOT = """
import hashlib,json,os
from pathlib import Path
from app.database import connect
with connect(Path(os.environ['DATABASE_PATH'])) as c:
 result={}
 for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
  name=row[0]
  data=sorted([tuple(r) for r in c.execute('SELECT * FROM "'+name+'"')], key=repr)
  result[name]={'count':len(data),'sha256':hashlib.sha256(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()}
 print(json.dumps({'tables':result,'version':c.execute('PRAGMA user_version').fetchone()[0],'mode':c.execute('PRAGMA journal_mode').fetchone()[0]}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'host': platform.system() + ' ' + platform.machine(),
              'sample_sha256': hashlib.sha256((ROOT / 'data/employees_10000.xlsx').read_bytes()).hexdigest(),
              'timings_ms': {}, 'checks': [], 'volumes': []}
    def passed(message):
        report['checks'].append(message)
        print('PASS:', message, flush=True)
    environment = {k: v for k, v in os.environ.items() if k not in {
        'WEB_PORT', 'PUBLIC_ORIGIN', 'COOKIE_SECURE', 'HR_EMAIL', 'HR_PASSWORD_HASH',
        'COMPOSE_FILE', 'COMPOSE_PROJECT_NAME', 'COMPOSE_PROFILES'}}
    password, email = secrets.token_urlsafe(24), 'acceptance@example.com'
    employees, bands = workbook(ROOT / 'data/employees_10000.xlsx')
    with tempfile.TemporaryDirectory(prefix='salary-acceptance-') as temp:
        temp = Path(temp)
        stacks = []
        def stack(suffix):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            origin = f'http://127.0.0.1:{port}'
            project = 'salary-acceptance-' + secrets.token_hex(4) + suffix
            env_file = temp / (project + '.env')
            env_file.write_text(f"WEB_PORT={port}\nPUBLIC_ORIGIN={origin}\nCOOKIE_SECURE=false\nHR_EMAIL={email}\nHR_PASSWORD_HASH='{hash_password(password)}'\n")
            env_file.chmod(0o600)
            command = ['docker', 'compose', '-p', project, '--env-file', str(env_file), '-f', str(ROOT / 'compose.yaml')]
            def run(*parts, capture=False, stdin=None):
                return subprocess.run(command + list(parts), cwd=ROOT, env=environment, check=True,
                                      text=stdin is None, input=stdin, stdout=subprocess.PIPE if capture else None).stdout
            stacks.append(run)
            report['volumes'].append(project + '_salary_data')
            client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            token = None
            def request(path, payload=None, raw=None, expected=200, timed=None):
                headers = {'Origin': origin, 'Content-Type': 'application/octet-stream' if raw is not None else 'application/json'}
                if token:
                    headers['X-CSRF-Token'] = token
                body = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
                start = perf_counter_ns()
                try:
                    response = client.open(urllib.request.Request(origin + '/api' + path, body, headers), timeout=180)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    content = response.read()
                    assert response.status == expected, (path, response.status, content[:1000])
                    result = json.loads(content) if 'application/json' in response.headers.get('Content-Type', '') else content
                if timed:
                    report['timings_ms'][timed] = str((perf_counter_ns() - start) // 1_000_000)
                return result
            def login():
                nonlocal token
                token = request('/auth/login', {'email': email, 'password': password})['csrf_token']
            def python(code):
                return run('exec', '-T', 'api', 'python', '-c', code, capture=True).strip()
            return run, request, login, python
        try:
            run, request, login, python = stack('-source')
            config = json.loads(run('config', '--format', 'json', capture=True))
            assert set(config['services']) == {'api', 'web'} and not config['services']['web'].get('volumes')
            run('build', '--no-cache')
            run('up', '-d', '--wait', '--wait-timeout', '180')
            for path in ('/employees', '/employees/export.csv', '/bands', '/analytics/summaries', '/analytics/compa-ratio', '/analytics/equity', '/imports/previews/missing'):
                request(path, expected=401)
            request('/imports/inspect', raw=b'invalid', expected=401)
            login()
            assert request('/employees')['total'] == request('/bands')['total'] == 0
            passed('fresh named volume, exactly two services, no web database mount, unauthorized requests rejected')
            run('cp', str(ROOT / 'data/employees_10000.xlsx'), 'api:/tmp/sample.xlsx')
            loaded = json.loads(run('exec', '-T', 'api', 'python', '-m', 'app.load_bands', '/tmp/sample.xlsx', capture=True))
            assert loaded['created'] == 48
            catalog = request('/bands')
            for band in catalog['items']:
                expected = bands[band['country'], band['level']]
                assert band['currency'] == expected['Currency']
                for key in ('minimum', 'midpoint', 'maximum'):
                    assert band[key+'_minor'] == str(expected[key.title()])
            body = (ROOT / 'data/employees_10000.xlsx').read_bytes()
            inspection = request('/imports/inspect', raw=body, timed='inspect_10000')
            assert len(inspection['worksheets']) == 3
            preview = request('/imports/previews?worksheet=Employees', raw=body, expected=201, timed='preview_10000')
            assert (preview['status'], preview['valid_count'], preview['invalid_count'], preview['unbanded_count']) == ('clean', 10000, 0, 0)
            assert request('/employees')['total'] == 0
            path = '/imports/previews/' + preview['preview_id']
            preview_last = request(path + '/rows?page=100&page_size=100')
            receipt = request(path + '/confirm', {}, timed='confirm_10000')
            assert receipt['employee_ids'] == [e['Employee ID'] for e in employees]
            assert receipt['inserted_count'] == 10000 and request(path + '/confirm', {}) == receipt
            passed('48 exact bands and six currencies; clean 10000-row preview writes zero employees; atomic confirm and identical retry receipt')
            by_id = {e['Employee ID']: e for e in employees}
            ordered = sorted(by_id)
            for size in (25, 50, 100):
                for page in (1, 2, 10000 // size):
                    listing = request(f'/employees?page={page}&page_size={size}', timed=f'directory_{size}_page_{page}')
                    assert listing['total'] == 10000 and len(listing['items']) == size
                    assert [e['employee_id'] for e in listing['items']] == ordered[(page-1)*size:page*size]
            exported = request('/employees/export.csv', timed='csv_10000')
            rows = list(csv.reader(io.StringIO(exported.decode())))
            assert len(rows) == 10001
            for row, identity in zip(rows[1:], ordered):
                e = by_id[identity]
                assert row == [identity, e['Name'], e['Email'], e['Department'], e['Level'], e['Country'], bands[e['Country'],e['Level']]['Currency'], display(Fraction(e['minor'], 100)), 'Band-matched']
            query = urllib.parse.urlencode({'search': ' EMP00 ', 'country': 'India', 'level': 'L1', 'department': 'engineering', 'sort': 'salary', 'direction': 'desc'})
            selected = [e for e in employees if e['Country'] == 'India' and e['Level'] == 'L1' and e['Department'] == 'Engineering' and 'emp00' in (e['Employee ID']+e['Name']+e['Email']).casefold()]
            selected.sort(key=lambda e: (-e['minor'], e['Employee ID']))
            filtered = request('/employees?' + query, timed='directory_combined_filters')
            csv_filtered = list(csv.reader(io.StringIO(request('/employees/export.csv?' + query).decode())))
            assert filtered['total'] == len(selected)
            assert [e['employee_id'] for e in filtered['items']] == [e['Employee ID'] for e in selected[:25]]
            assert [e[0] for e in csv_filtered[1:]] == [e['Employee ID'] for e in selected]
            summary = request('/analytics/summaries', timed='summaries_10000')
            compa = request('/analytics/compa-ratio', timed='compa_10000')
            equity = request('/analytics/equity', timed='equity_10000')
            verify(employees, bands, summary, compa, equity)
            passed('independent workbook oracle: directory pages, full and filtered CSV, every summary/group, compa bucket and all 10000 leave-one-out equity results; 9500/250/250 positions')
            snapshot = json.loads(python(SNAPSHOT))
            assert snapshot['mode'] == 'wal'
            for parts in (('down',), ('up', '-d', '--wait', '--wait-timeout', '180'), ('up', '-d', '--force-recreate', '--wait', '--wait-timeout', '180')):
                run(*parts)
                if parts[0] == 'up':
                    assert json.loads(python(SNAPSHOT)) == snapshot
                    assert request(path)['preview_id'] == preview['preview_id']
                    assert request(path + '/rows?page=100&page_size=100') == preview_last
                    assert request(path + '/confirm', {}) == receipt
            passed('all table counts and content digests, bands, employee cents, preview rows, receipt and HR session survive down/up and recreation')
            # Keep a WAL reader open while a committed marker remains in WAL. A main-file-only copy would miss it.
            python("from pathlib import Path; import os,sqlite3; c=sqlite3.connect(os.environ['DATABASE_PATH'],isolation_level=None); c.execute('CREATE TABLE acceptance_wal_marker(value TEXT)'); c.close()")
            run('exec', '-T', '-d', 'api', 'python', '-c', "from pathlib import Path; import os,time,sqlite3; c=sqlite3.connect(os.environ['DATABASE_PATH'],isolation_level=None); c.execute('BEGIN'); c.execute('SELECT * FROM acceptance_wal_marker').fetchall(); Path('/tmp/reader-ready').touch(); time.sleep(300)")
            python("from pathlib import Path; import time;\nfor _ in range(100):\n if Path('/tmp/reader-ready').exists(): break\n time.sleep(.05)\nelse: raise RuntimeError('WAL reader failed')")
            python("from pathlib import Path; import os,sqlite3; c=sqlite3.connect(os.environ['DATABASE_PATH'],isolation_level=None); c.execute(\"INSERT INTO acceptance_wal_marker VALUES ('committed-in-wal')\"); c.close(); assert Path(os.environ['DATABASE_PATH']+'-wal').stat().st_size > 0")
            python("import os,sqlite3; c=sqlite3.connect('file:'+os.environ['DATABASE_PATH']+'?immutable=1',uri=True); assert c.execute('SELECT count(*) FROM acceptance_wal_marker').fetchone()[0] == 0; c.close()")
            backup_snapshot = json.loads(python(SNAPSHOT))
            python("import os,sqlite3; source=sqlite3.connect(os.environ['DATABASE_PATH']); target=sqlite3.connect('/tmp/backup.db'); source.backup(target); target.close(); source.close()")
            backup = temp / 'backup.db'
            run('cp', 'api:/tmp/backup.db', str(backup))
            backup.chmod(0o600)
            restore, restored_request, restored_login, restored_python = stack('-restore')
            restore('run', '--rm', '--no-deps', '-T', 'api', 'python', '-c', "import sqlite3,os,sys; from pathlib import Path; path=Path(os.environ['DATABASE_PATH']); assert not path.exists(), 'Restore requires fresh volume'; Path('/tmp/backup.db').write_bytes(sys.stdin.buffer.read()); source=sqlite3.connect('file:/tmp/backup.db?mode=ro',uri=True); target=sqlite3.connect(path); source.backup(target); target.close(); source.close()", stdin=backup.read_bytes())
            restore('up', '-d', '--wait', '--wait-timeout', '180')
            assert json.loads(restored_python(SNAPSHOT)) == backup_snapshot
            restored_login()
            assert restored_request('/employees')['total'] == 10000
            assert restored_request(path + '/receipt') == receipt
            assert restored_request('/employees/export.csv') == exported
            assert restored_request('/analytics/summaries') == summary
            assert restored_request('/analytics/compa-ratio') == compa
            assert restored_request('/analytics/equity') == equity
            passed('online SQLite backup includes live committed WAL marker; separate restored volume matches every table digest, all exact amounts, CSV, summaries, compa, equity and receipt')
            run('stop', 'api')
            request('/health/ready', expected=503)
            request('/employees', expected=503)
            run('up', '-d', '--wait', '--wait-timeout', '180')
            assert request('/health/ready') == {'status': 'ready'}
            assert request('/employees')['total'] == 10000 and request(path+'/confirm', {}) == receipt
            passed('API outage maps to proxy 503 and recovers with preserved session/data and safe confirm retry')
            if args.report:
                args.report.write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2), flush=True)
        finally:
            for stop in reversed(stacks):
                stop('down')


if __name__ == '__main__':
    main()
