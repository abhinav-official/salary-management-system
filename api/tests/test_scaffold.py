import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import COOKIE_NAME
from app.config import Settings
from app.database import SCHEMA_VERSION, connect
from app.main import create_app
from app.migrate import migrate
from app.passwords import hash_password, verify_password


class ScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "test-only-password-42"
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "salary.db"
        migrate(self.path)
        self.settings = Settings(self.path, "hr@example.com", self.password_hash,
                                 "http://testserver", cookie_secure=False)
        self.app = create_app(self.settings)

        @self.app.get("/private")
        def protected_read():
            return {"private": True}

        @self.app.post("/private")
        def protected_write():
            return {"changed": True}

        self.client = self.enterContext(TestClient(self.app))
        self.addCleanup(self.directory.cleanup)

    def login(self):
        return self.client.post("/auth/login", headers={"Origin": "http://testserver"},
                                json={"email": "HR@example.com", "password": self.password})

    def test_health_is_public_and_contains_no_database_or_credentials(self):
        self.assertEqual(self.client.get("/health/live").json(), {"status": "alive"})
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_ready_fails_but_live_survives_schema_drift(self):
        with connect(self.path) as connection:
            connection.execute("PRAGMA user_version=999")
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        self.assertEqual(self.client.get("/health/live").status_code, 200)

    def test_ready_does_not_recreate_missing_database(self):
        self.path.unlink()
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        self.assertFalse(self.path.exists())

    def test_wal_foreign_keys_timeout_and_version(self):
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_migration_rerun_preserves_records(self):
        self.login()
        migrate(self.path)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(self.client.get("/auth/session").status_code, 200)

    def test_newer_database_is_not_downgraded(self):
        with connect(self.path) as connection:
            connection.execute("PRAGMA user_version=999")
        with self.assertRaises(RuntimeError):
            migrate(self.path)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 999)

    def test_failed_migration_rolls_back_version(self):
        path = Path(self.directory.name) / "bad.db"
        with connect(path) as connection:
            connection.execute("CREATE TABLE sessions (conflict TEXT)")
        with self.assertRaises(sqlite3.OperationalError):
            migrate(path)
        with connect(path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA table_info(sessions)").fetchone()[1], "conflict")

    def test_default_deny_including_future_routes_and_api_docs(self):
        for path in ("/auth/session", "/private", "/docs", "/openapi.json"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_login_cookie_and_protected_session(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Max-Age=28800", cookie)
        self.assertNotIn("Secure;", cookie)
        self.assertEqual(self.client.get("/auth/session").json(), response.json())
        self.assertEqual(self.client.get("/private").status_code, 200)
        with connect(self.path) as connection:
            stored = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
        self.assertNotEqual(stored, self.client.cookies.get(COOKIE_NAME))

    def test_session_survives_application_recreation(self):
        self.login()
        token = self.client.cookies.get(COOKIE_NAME)
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get("/auth/session", headers={"Cookie": f"{COOKIE_NAME}={token}"}).status_code, 200)

    def test_bad_credentials_and_untrusted_login_origin(self):
        response = self.client.post("/auth/login", headers={"Origin": "http://testserver"},
                                    json={"email": "hr@example.com", "password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("set-cookie", response.headers)
        for origin in (None, "https://attacker.example"):
            headers = {"Origin": origin} if origin else {}
            response = self.client.post("/auth/login", headers=headers,
                                        json={"email": "hr@example.com", "password": self.password})
            self.assertEqual(response.status_code, 403)

    def test_mutations_require_both_csrf_and_trusted_origin(self):
        csrf = self.login().json()["csrf_token"]
        for headers in ({"Origin": "http://testserver"},
                        {"Origin": "http://testserver", "X-CSRF-Token": "wrong"},
                        {"Origin": "https://attacker.example", "X-CSRF-Token": csrf}):
            self.assertEqual(self.client.post("/private", headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/private", headers={
            "Origin": "http://testserver", "X-CSRF-Token": csrf,
        }).status_code, 200)

    def test_logout_revokes_token_even_if_cookie_is_replayed(self):
        csrf = self.login().json()["csrf_token"]
        token = self.client.cookies.get(COOKIE_NAME)
        self.assertEqual(self.client.post("/auth/logout", headers={
            "Origin": "http://testserver", "X-CSRF-Token": csrf,
        }).status_code, 204)
        self.assertEqual(self.client.get("/auth/session", headers={
            "Cookie": f"{COOKIE_NAME}={token}",
        }).status_code, 401)

    def test_expired_and_tampered_sessions_are_rejected(self):
        self.login()
        with connect(self.path) as connection:
            connection.execute("UPDATE sessions SET expires_at=0")
        self.assertEqual(self.client.get("/auth/session").status_code, 401)
        self.assertEqual(self.client.get("/auth/session", headers={
            "Cookie": f"{COOKIE_NAME}={'x' * 43}",
        }).status_code, 401)

    def test_login_rotates_previous_session(self):
        self.login()
        old = self.client.cookies.get(COOKIE_NAME)
        self.login()
        self.assertEqual(self.client.get("/auth/session", headers={
            "Cookie": f"{COOKIE_NAME}={old}",
        }).status_code, 401)

    def test_secure_deployment_cookie(self):
        settings = replace(self.settings, public_origin="https://testserver", cookie_secure=True)
        with TestClient(create_app(settings), base_url="https://testserver") as client:
            response = client.post("/auth/login", headers={"Origin": "https://testserver"},
                                   json={"email": settings.hr_email, "password": self.password})
            self.assertIn("Secure", response.headers["set-cookie"])
            self.assertEqual(client.get("/auth/session").status_code, 200)

    def test_invalid_configuration_fails_closed(self):
        for kwargs in ({"hr_password_hash": "not-a-hash"},
                       {"public_origin": "http://testserver/extra"},
                       {"cookie_secure": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                replace(self.settings, **kwargs)

    def test_password_hash_is_salted_and_checks_exact_password(self):
        self.assertNotEqual(hash_password(self.password), self.password_hash)
        self.assertTrue(verify_password(self.password, self.password_hash))
        self.assertFalse(verify_password("wrong", self.password_hash))
