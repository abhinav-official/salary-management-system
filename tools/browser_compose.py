#!/usr/bin/env python3
"""Run Chromium acceptance tests through an isolated two-service Compose stack."""

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.passwords import hash_password  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-running", action="store_true", help="Keep this test stack and its private state file for browser inspection")
    args = parser.parse_args()
    project = "salary-browser-" + secrets.token_hex(4)
    directory = Path(tempfile.mkdtemp(prefix="salary-browser-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    password = secrets.token_urlsafe(24)
    email = "browser@example.com"
    env_file = directory / ".env"
    env_file.write_text(f"WEB_PORT={port}\nPUBLIC_ORIGIN={origin}\nCOOKIE_SECURE=false\n"
                        f"HR_EMAIL={email}\nHR_PASSWORD_HASH='{hash_password(password)}'\n")
    env_file.chmod(0o600)
    compose = ["docker", "compose", "--project-name", project, "--env-file", str(env_file), "--file", str(ROOT / "compose.yaml")]
    env = {key: value for key, value in os.environ.items() if key not in {
        "WEB_PORT", "PUBLIC_ORIGIN", "COOKIE_SECURE", "HR_EMAIL", "HR_PASSWORD_HASH",
        "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES",
    }}
    env.update(E2E_ORIGIN=origin, E2E_EMAIL=email, E2E_PASSWORD=password)
    state = directory / "state.json"
    state.write_text(json.dumps({"compose": compose, "origin": origin, "email": email, "password": password}))
    state.chmod(0o600)
    print(f"Browser test origin: {origin}; private state: {state}", flush=True)

    def run(*commands):
        subprocess.run([*compose, *commands], cwd=ROOT, env=env, check=True)

    def browser(*options):
        subprocess.run(["npx", "playwright", "test", *options], cwd=ROOT / "web", env=env, check=True)

    started = False
    try:
        run("build")
        started = True
        run("up", "-d", "--wait", "--wait-timeout", "120")
        # Only API-owned domain/repository code writes this deterministic fixture.
        run("exec", "-T", "api", "python", "-c", """from pathlib import Path
import os
from app.database import transaction
from app.models import Employee, SalaryBand, COUNTRY_CURRENCIES
from app.repository import insert_employee, insert_band
with transaction(Path(os.environ['DATABASE_PATH'])) as connection:
    insert_band(connection, SalaryBand('India', 'L1', 10000, 20000, 30000))
    for number in range(10000):
        insert_employee(connection, Employee(f'EMP{number:05}', f'Employee {number:05}',
            f'person{number:05}@example.com', 'Last Page' if number < 26 else ('Engineering' if number % 2 == 0 else 'Sales'),
            f'L{number % 8 + 1}', list(COUNTRY_CURRENCIES)[number % 6], number * 101))
""")
        browser("--grep-invert", "after restart")
        run("down")
        run("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        browser("--grep", "after restart")
        print(f"BROWSER AND RESTART CHECKS PASSED. Retained test volume: {project}_salary_data", flush=True)
    finally:
        if args.keep_running:
            print(f"Test stack retained for inspection. Private state: {state}", flush=True)
        else:
            try:
                if started:
                    run("logs", "--tail", "30")
                    run("down")
            finally:
                shutil.rmtree(directory)


if __name__ == "__main__":
    main()
