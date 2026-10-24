from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.accounts import DEMO_EMAIL, DEMO_PASSWORD, initialize_account
from app.config import Settings
from app.database import connect
from app.main import create_app
from app.migrate import migrate
from app.passwords import hash_password, verify_password


class AccountStartupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'salary.db'
        migrate(self.path)
        self.settings = Settings(self.path, '', '', 'http://testserver', cookie_secure=False)

    def account(self):
        with connect(self.path) as connection:
            rows = connection.execute('SELECT * FROM hr_account').fetchall()
        self.assertEqual(len(rows), 1)
        return dict(rows[0])

    def login(self, client, email=DEMO_EMAIL, password=DEMO_PASSWORD):
        return client.post('/auth/login', headers={'Origin': 'http://testserver'},
                           json={'email': email, 'password': password})

    def test_fresh_environment_seeds_hashed_demo_and_can_sign_in(self):
        with patch.dict(os.environ, {'DATABASE_PATH': str(self.path), 'PUBLIC_ORIGIN': 'http://testserver',
                                    'COOKIE_SECURE': 'false'}, clear=True):
            with TestClient(create_app()) as client:
                response = self.login(client)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['email'], DEMO_EMAIL)
                self.assertEqual(client.get('/auth/session').json(), response.json())
                self.assertEqual(self.login(client, password='wrong').status_code, 401)
        account = self.account()
        self.assertEqual(account['email'], DEMO_EMAIL)
        self.assertTrue(account['password_hash'].startswith('scrypt$'))
        self.assertNotEqual(account['password_hash'], DEMO_PASSWORD)
        self.assertTrue(verify_password(DEMO_PASSWORD, account['password_hash']))

    def test_existing_account_is_untouched_by_restart_or_new_environment_credentials(self):
        original_hash = hash_password('existing-password-42')
        with connect(self.path) as connection:
            connection.execute('INSERT INTO hr_account VALUES (1,?,?)', ('owner@example.com', original_hash))
        original = self.account()
        for settings in (self.settings, replace(self.settings, hr_email='replacement@example.com',
                                                hr_password_hash=hash_password('replacement-password'))):
            with TestClient(create_app(settings)) as client:
                self.assertEqual(self.login(client, 'owner@example.com', 'existing-password-42').status_code, 200)
                self.assertEqual(client.get('/auth/session').json()['email'], 'owner@example.com')
                self.assertEqual(self.login(client).status_code, 401)
                self.assertEqual(self.login(client, 'replacement@example.com', 'replacement-password').status_code, 401)
            self.assertEqual(self.account(), original)

    def test_configured_legacy_account_is_saved_once_and_survives_omitted_environment(self):
        # Model the pre-user-table release: credentials existed only in configuration.
        with connect(self.path) as connection:
            connection.execute('DROP TABLE hr_account')
            connection.execute('PRAGMA user_version=6')
        migrate(self.path)
        configured = replace(self.settings, hr_email='OWNER@EXAMPLE.COM', hr_password_hash=hash_password('custom-password-42'))
        with TestClient(create_app(configured)) as client:
            response = self.login(client, 'owner@example.com', 'custom-password-42')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.login(client).status_code, 401)
            cookies = client.cookies
        original = self.account()
        self.assertEqual(original['password_hash'], configured.hr_password_hash)
        migrate(self.path)
        with TestClient(create_app(self.settings)) as restarted:
            restarted.cookies.update(cookies)
            self.assertEqual(restarted.get('/auth/session').json()['email'], 'owner@example.com')
            self.assertEqual(self.login(restarted, 'owner@example.com', 'custom-password-42').status_code, 200)
        self.assertEqual(self.account(), original)

    def test_demo_hash_and_session_are_preserved_on_repeated_startup(self):
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(self.login(client).status_code, 200)
            cookies = client.cookies
        original = self.account()
        with patch('app.accounts.hash_password', side_effect=AssertionError('must not rehash existing password')):
            with TestClient(create_app(self.settings)) as restarted:
                restarted.cookies.update(cookies)
                self.assertEqual(restarted.get('/auth/session').status_code, 200)
                self.assertEqual(self.login(restarted).status_code, 200)
        self.assertEqual(self.account(), original)

    def test_concurrent_bootstrap_creates_one_account_with_one_hash(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: initialize_account(self.path, '', ''), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.account()['password_hash'], results[0][1])

    def test_partial_credentials_are_rejected_instead_of_seeding_demo(self):
        for values in ({'hr_email': 'owner@example.com'}, {'hr_password_hash': hash_password('custom-password')}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(self.settings, **values)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM hr_account').fetchone()[0], 0)
