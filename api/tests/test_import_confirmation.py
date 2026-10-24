from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import import_confirmation as confirmation, imports
from app.config import Settings
from app.database import connect, transaction
from app.main import create_app
from app.migrate import migrate
from app.models import Employee, SalaryBand
from app.repository import insert_band, insert_employee
from app.load_bands import load_bands
from app.xlsx import ImportProblem
import test_imports as preview_tests
from test_imports import HEADERS, ROW, SAMPLE, workbook


class ImportConfirmationTests(unittest.TestCase):
    setUpClass = classmethod(preview_tests.ImportPreviewTests.setUpClass.__func__)
    setUp = preview_tests.ImportPreviewTests.setUp
    create = preview_tests.ImportPreviewTests.create
    counts = preview_tests.ImportPreviewTests.counts

    def url(self, info):
        return '/imports/previews/' + info['preview_id']

    def confirm(self, info, expected=200):
        response = self.client.post(self.url(info) + '/confirm', json={},
            headers={**self.headers, 'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def assert_no_completion(self, expected_employees=0):
        with connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM employees').fetchone()[0], expected_employees)
            self.assertEqual(connection.execute('SELECT count(*) FROM import_receipts').fetchone()[0], 0)

    def batch(self, count=4):
        return workbook({'Employees': {1: HEADERS, **{number+2: [f'id{number}', 'Name', f'id{number}@example.com', *ROW[3:]] for number in range(count)}}})

    def test_full_sample_import_exact_counts_positions_order_and_idempotency(self):
        load_bands(self.path, SAMPLE)
        info = self.create(SAMPLE.read_bytes())
        result = self.confirm(info)
        self.assertEqual(result['inserted_count'], 10000)
        self.assertEqual(result['actor'], 'hr@example.com')
        self.assertEqual(len(set(result['employee_ids'])), 10000)
        with connect(self.path) as connection:
            rows = connection.execute('SELECT e.*, b.minimum_minor,b.maximum_minor FROM employees e JOIN salary_bands b USING(country,level)').fetchall()
            countries = Counter(row['country'] for row in rows)
            positions = Counter('below' if row['salary_minor'] < row['minimum_minor'] else 'above' if row['salary_minor'] > row['maximum_minor'] else 'inside' for row in rows)
            expected_ids = [row[0] for row in connection.execute('SELECT employee_id FROM import_preview_rows ORDER BY row_number')]
            self.assertTrue(all(type(row['salary_minor']) is int and row['revision'] == 1 for row in rows))
        self.assertEqual(countries, {'India':4000, 'US':2500, 'UK':1200, 'Germany':1000, 'Singapore':800, 'UAE':500})
        self.assertEqual(positions, {'inside':9500, 'below':250, 'above':250})
        self.assertEqual(result['employee_ids'], expected_ids)
        with patch('app.import_confirmation.insert_employee', side_effect=AssertionError('retry must never insert')):
            self.assertEqual(self.confirm(info), result)
        self.assertEqual(self.counts()['employees'], 10000)
        self.assertEqual(self.client.get(self.url(info)+'/receipt').json(), result)

    def test_new_conflicting_id_and_email_report_every_field_and_insert_nothing(self):
        info = self.create(self.batch())
        with transaction(self.path) as connection:
            insert_employee(connection, Employee('ID0', 'Existing', 'ID1@EXAMPLE.COM', 'Sales', 'L1', 'India', 1))
        failure = self.confirm(info, 409)['detail']
        self.assertEqual(failure['code'], 'preview_conflict')
        self.assertEqual({(error['row'],error['field']) for error in failure['errors']}, {(2,'Employee ID'),(3,'Email')})
        self.assertEqual(len(failure['employee_changes']), 1)
        self.assert_no_completion(1)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT name,salary_minor FROM employees').fetchone()[:], ('Existing',1))

    def test_existing_employee_revision_and_recreated_lifetime_make_preview_stale(self):
        with transaction(self.path) as connection:
            insert_employee(connection, Employee('other','Old','other@example.com','Sales','L1','India',1))
        info = self.create()
        with transaction(self.path) as connection:
            connection.execute("UPDATE employees SET name='New',revision=revision+1")
        failure = self.confirm(info,409)['detail']
        self.assertEqual(failure['employee_changes'][0]['expected']['revision'],1)
        self.assertEqual(failure['employee_changes'][0]['current']['revision'],2)
        self.assert_no_completion(1)
        info = self.create()
        with transaction(self.path) as connection:
            connection.execute('DELETE FROM employees')
            insert_employee(connection, Employee('other','Old','other@example.com','Sales','L1','India',1,revision=2))
        failure = self.confirm(info,409)['detail']
        change = failure['employee_changes'][0]
        self.assertEqual(change['expected']['revision'], change['current']['revision'])
        self.assertNotEqual(change['expected']['generation'], change['current']['generation'])
        self.assert_no_completion(1)

    def test_full_catalog_changes_including_unrelated_band_and_deleted_recreated_band(self):
        with transaction(self.path) as connection:
            insert_band(connection, SalaryBand('US','L8',0,100,200))
            insert_band(connection, SalaryBand('India','L1',0,100,200))
        for mutation in ('UPDATE salary_bands SET midpoint_minor=101,revision=revision+1 WHERE country=\'US\'',
                         'UPDATE salary_bands SET maximum_minor=201,revision=revision+1 WHERE country=\'India\'',
                         'DELETE FROM salary_bands WHERE country=\'India\''):
            info = self.create()
            with transaction(self.path) as connection:
                connection.execute(mutation)
            failure = self.confirm(info,409)['detail']
            self.assertEqual(failure['code'],'preview_conflict')
            self.assertEqual(len(failure['band_changes']),1)
            self.assertIsInstance(failure['band_changes'][0]['expected']['midpoint_minor'],str)
            if mutation.startswith('DELETE'):
                self.assertEqual(failure['errors'][0]['code'],'band_status_changed')
            self.assert_no_completion()
        with transaction(self.path) as connection:
            insert_band(connection, SalaryBand('India','L1',0,100,200))
        info = self.create()
        with transaction(self.path) as connection:
            connection.execute("DELETE FROM salary_bands WHERE country='India'")
            insert_band(connection, SalaryBand('India','L1',0,100,200))
        failure = self.confirm(info,409)['detail']
        self.assertNotEqual(failure['band_changes'][0]['expected']['generation'],failure['band_changes'][0]['current']['generation'])
        self.assert_no_completion()

    def test_added_band_changes_unbanded_status_and_fails_atomically(self):
        info = self.create()
        with transaction(self.path) as connection:
            insert_band(connection, SalaryBand('India','L1',0,100,200))
        failure = self.confirm(info,409)['detail']
        self.assertEqual(failure['errors'][0]['expected'],'Unbanded')
        self.assertEqual(failure['errors'][0]['current'],'Band-matched')
        self.assert_no_completion()

    def test_invalid_expired_foreign_unknown_and_replacement_payloads_cannot_confirm(self):
        bad = self.create(workbook({'Employees': {1:HEADERS,2:[*ROW[:-1],'-1']}}))
        self.assertEqual(self.confirm(bad,409)['detail']['code'],'preview_invalid')
        info = self.create()
        with patch('app.import_confirmation.time.time',return_value=info['expires_at']):
            self.assertEqual(self.confirm(info,410)['detail']['code'],'preview_expired')
        self.assertEqual(self.confirm({'preview_id':'unknown'},404)['detail']['code'],'preview_not_found')
        with self.assertRaises(ImportProblem) as caught:
            confirmation.confirm(self.path,'other@example.com',info['preview_id'])
        self.assertEqual(caught.exception.detail['code'],'preview_not_found')
        self.assertEqual(self.client.post(self.url(info)+'/confirm',json={'rows':[]},headers={**self.headers,'Content-Type':'application/json'}).status_code,422)
        self.assertEqual(self.client.get(self.url(info)+'/receipt').json()['detail']['code'],'receipt_not_found')
        self.assert_no_completion()

    def test_unbanded_and_out_of_band_rows_confirm_with_exact_rounding(self):
        info = self.create()
        result = self.confirm(info)
        self.assertEqual(result['employee_ids'],['0001'])
        record = self.client.get('/employees/records/0001').json()
        self.assertEqual((record['salary_minor'],record['band_status']),('12346','Unbanded'))
        with transaction(self.path) as connection:
            insert_band(connection,SalaryBand('India','L1',100000,200000,300000))
        info2 = self.create(self.batch(2))
        self.assertEqual(self.confirm(info2)['inserted_count'],2)

    def test_failure_after_some_inserts_rolls_back_rows_identities_and_receipt_then_safe_retry(self):
        info = self.create(self.batch())
        real_insert = confirmation.insert_employee
        calls = []
        def failing(connection, employee):
            real_insert(connection,employee)
            calls.append(employee.employee_id)
            if len(calls)==3:
                raise RuntimeError('private exception must not leak')
        with patch('app.import_confirmation.insert_employee',side_effect=failing):
            failure = self.confirm(info,503)['detail']
        self.assertEqual(len(calls),3)
        self.assertEqual(failure['code'],'confirmation_unavailable')
        self.assertTrue(failure['retryable'])
        self.assertNotIn('private',json.dumps(failure))
        self.assert_no_completion()
        with connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM employee_identities').fetchone()[0],0)
        self.assertEqual(self.confirm(info)['employee_ids'],['id0','id1','id2','id3'])

    def test_receipt_write_failure_rolls_back_entire_batch(self):
        info = self.create(self.batch())
        with transaction(self.path) as connection:
            connection.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON import_receipts BEGIN SELECT RAISE(ABORT,'forced'); END")
        self.assertEqual(self.confirm(info,409)['detail']['code'],'import_integrity_conflict')
        self.assert_no_completion()
        with transaction(self.path) as connection:
            connection.execute('DROP TRIGGER fail_receipt')
        self.assertEqual(self.confirm(info)['inserted_count'],4)

    def test_concurrent_same_preview_returns_one_receipt_using_separate_connections(self):
        info = self.create(self.batch())
        barrier = threading.Barrier(2)
        def run():
            barrier.wait(timeout=10)
            return confirmation.confirm(self.path,'hr@example.com',info['preview_id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(self.counts()['employees'],4)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM import_receipts').fetchone()[0],1)

    def test_concurrent_distinct_previews_with_same_rows_one_wins_other_adds_zero(self):
        previews = [self.create(self.batch()) for _ in range(2)]
        barrier = threading.Barrier(2)
        def run(info):
            barrier.wait(timeout=10)
            try:
                return confirmation.confirm(self.path,'hr@example.com',info['preview_id'])
            except ImportProblem as error:
                return error.detail
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run,previews))
        self.assertEqual(sum('inserted_count' in result for result in results),1)
        self.assertEqual([result['code'] for result in results if 'code' in result],['preview_conflict'])
        self.assertEqual(self.counts()['employees'],4)

    def test_busy_real_database_returns_retryable_conflict_then_succeeds(self):
        info = self.create()
        with transaction(self.path):
            failure = self.confirm(info,409)['detail']
            self.assertEqual(failure['code'],'database_busy')
            self.assertTrue(failure['retryable'])
        self.assert_no_completion()
        self.assertEqual(self.confirm(info)['inserted_count'],1)

    def test_lost_response_restart_expired_consumed_retry_returns_original_receipt(self):
        info = self.create(self.batch())
        # Discard the first HTTP response exactly as a disconnected client would.
        self.confirm(info)
        result = self.client.get(self.url(info)+'/receipt').json()
        subprocess.run([sys.executable,'-m','app.migrate'],env={**__import__('os').environ,'DATABASE_PATH':str(self.path)},check=True,capture_output=True)
        with TestClient(create_app(self.settings)) as restarted:
            restarted.cookies.update(self.client.cookies)
            with patch('app.import_confirmation.time.time',return_value=info['expires_at']+1):
                response = restarted.post(self.url(info)+'/confirm',headers={**self.headers,'Content-Type':'application/json'},json={})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json(),result)
                self.assertEqual(restarted.get(self.url(info)+'/receipt').json(),result)
        # Deleting an imported employee never makes this preview insert it again.
        with transaction(self.path) as connection:
            connection.execute("DELETE FROM employees WHERE employee_id='id0'")
        self.assertEqual(self.confirm(info),result)
        self.assertEqual(self.counts()['employees'],3)

    def test_snapshot_and_receipt_immutable_and_legacy_preview_requires_refresh(self):
        info = self.create()
        with connect(self.path) as connection:
            for sql in ('UPDATE import_preview_context SET snapshot=\'{}\'', 'DELETE FROM import_preview_context'):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql)
        result = self.confirm(info)
        with connect(self.path) as connection:
            for sql in ('UPDATE import_receipts SET actor=\'other\'', 'DELETE FROM import_receipts'):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql)
        self.assertEqual(self.confirm(info),result)
        # A real schema-v5 database is upgraded without fabricating history.
        legacy = Path(self.directory.name)/'v5.db'
        with connect(legacy) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            for migration in sorted((Path(__file__).parents[1]/'app/migrations').glob('*.sql'))[:5]:
                connection.executescript(migration.read_text())
            connection.execute('PRAGMA user_version=5')
            # Store an old header directly; absence of context must be explicit.
            connection.execute("INSERT INTO import_previews VALUES ('legacy','hr@example.com','Employees',?,?,2000000000,2000001800,'clean',1,1,0,0,0,0,1)",('a'*64,'b'*64))
        migrate(legacy)
        with self.assertRaises(ImportProblem) as caught:
            with patch('app.import_confirmation.time.time',return_value=2000000001):
                confirmation.confirm(legacy,'hr@example.com','legacy')
        self.assertEqual(caught.exception.detail['code'],'preview_stale')
        with connect(legacy) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM employees').fetchone()[0],0)

    def test_altered_proposed_values_and_context_rejected_with_zero_writes(self):
        info = self.create()
        with transaction(self.path) as connection:
            connection.execute('DROP TRIGGER import_preview_row_update')
            connection.execute('UPDATE import_preview_rows SET salary_minor=1')
        self.assertEqual(self.confirm(info,409)['detail']['code'],'preview_altered')
        self.assert_no_completion()
        info2 = self.create()
        with transaction(self.path) as connection:
            connection.execute('DROP TRIGGER import_context_update')
            connection.execute("UPDATE import_preview_context SET snapshot='{}' WHERE preview_id=?",(info2['preview_id'],))
        self.assertEqual(self.confirm(info2,409)['detail']['code'],'preview_altered')
        self.assert_no_completion()

    def test_confirmation_revalidates_fields_and_intra_file_duplicates_independently(self):
        info = self.create(self.batch())
        with transaction(self.path) as connection:
            connection.execute('DROP TRIGGER import_preview_row_update')
            connection.execute("UPDATE import_preview_rows SET employee_id='ID0',email='ID0@EXAMPLE.COM',salary_minor=NULL WHERE row_number=3")
        # Bypass only the proposal hash to exercise the second validation layer.
        real_digest = confirmation.digest
        with patch('app.import_confirmation.digest',side_effect=lambda value: info['proposed_digest'] if 'rows' in value else real_digest(value)):
            failure = self.confirm(info,409)['detail']
        self.assertEqual(failure['code'],'preview_conflict')
        duplicates = [error for error in failure['errors'] if error['code']=='duplicate_in_file']
        self.assertEqual({(error['row'],error['field']) for error in duplicates},{(2,'Employee ID'),(3,'Employee ID'),(2,'Email'),(3,'Email')})
        self.assertTrue(any(error['field']=='Annual Salary' and error['row']==3 for error in failure['errors']))
        self.assert_no_completion()

    def test_confirmation_auth_origin_csrf_and_receipt_owner(self):
        info = self.create()
        with TestClient(create_app(self.settings)) as anonymous:
            self.assertEqual(anonymous.post(self.url(info)+'/confirm',headers={'Origin':'http://testserver'}).status_code,401)
        self.assertEqual(self.client.post(self.url(info)+'/confirm',headers={'Origin':'http://testserver'}).status_code,403)
        self.assertEqual(self.client.post(self.url(info)+'/confirm',headers={**self.headers,'Origin':'http://other'}).status_code,403)
        self.assert_no_completion()
        result = self.confirm(info)
        with self.assertRaises(ImportProblem) as caught:
            confirmation.receipt(self.path,'other@example.com',info['preview_id'])
        self.assertEqual(caught.exception.detail['code'],'preview_not_found')
        self.assertEqual(self.confirm(info),result)

    def test_commit_succeeded_but_acknowledgement_failed_retry_resolves_receipt(self):
        info = self.create(self.batch())
        real_transaction = confirmation.transaction
        @contextmanager
        def lost_ack(path):
            with real_transaction(path) as connection:
                yield connection
            raise OSError('connection lost after commit')
        with patch('app.import_confirmation.transaction',lost_ack):
            failure = self.confirm(info,503)['detail']
        self.assertEqual(failure['code'],'confirmation_unavailable')
        self.assertTrue(failure['retryable'])
        self.assertEqual(self.counts()['employees'],4)
        receipt = self.client.get(self.url(info)+'/receipt').json()
        self.assertEqual(self.confirm(info),receipt)
        self.assertEqual(self.counts()['employees'],4)
