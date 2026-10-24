#!/usr/bin/env python3
"""Deployment, band, and employee checks on an isolated project and named volume.

Uses only Python's standard library and Docker. Keeps the tested volume, stops
its containers on exit, and never reads or replaces the user's local .env.
"""

import http.cookiejar
import csv
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.passwords import hash_password  # noqa: E402


def main():
    project = "salary-smoke-" + secrets.token_hex(4)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    password = secrets.token_urlsafe(24)
    email = "smoke@example.com"
    with tempfile.TemporaryDirectory(prefix="salary-smoke-") as directory:
        env_file = Path(directory) / ".env"
        env_file.write_text(f"WEB_PORT={port}\nPUBLIC_ORIGIN={origin}\nCOOKIE_SECURE=false\n"
                            f"HR_EMAIL={email}\nHR_PASSWORD_HASH='{hash_password(password)}'\n")
        env_file.chmod(0o600)
        # Avoid ambient local credentials overriding these test-only values.
        env = {key: value for key, value in os.environ.items() if key not in {
            "WEB_PORT", "PUBLIC_ORIGIN", "COOKIE_SECURE", "HR_EMAIL", "HR_PASSWORD_HASH",
            "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES",
        }}
        compose = ["docker", "compose", "--project-name", project, "--env-file", str(env_file),
                   "--file", str(ROOT / "compose.yaml")]

        def run(*args, capture=False):
            result = subprocess.run([*compose, *args], cwd=ROOT, env=env, check=True,
                                    text=True, stdout=subprocess.PIPE if capture else None)
            return result.stdout if capture else None

        def api_python(code):
            return run("exec", "-T", "api", "python", "-c", code, capture=True).strip()

        jar = http.cookiejar.CookieJar()
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def request(path, payload=None, headers=None, expected=200, method=None):
            body = json.dumps(payload).encode() if payload is not None else None
            actual_headers = {"Content-Type": "application/json", **(headers or {})}
            req = urllib.request.Request(origin + path, data=body, headers=actual_headers, method=method)
            try:
                response = client.open(req, timeout=15)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                assert response.status == expected, (path, response.status, response.read())
                content = response.read()
                return response.headers, (json.loads(content) if "application/json" in response.headers.get("Content-Type", "")
                                          else content) if content else None

        started = False
        try:
            config = json.loads(run("config", "--format", "json", capture=True))
            assert set(config["services"]) == {"api", "web"}
            assert not config["services"]["web"].get("volumes")
            assert not config["services"]["api"].get("ports")
            print("PASS: exactly api/web; web has no database mount; API has no host port", flush=True)
            run("build", "--no-cache")
            started = True
            run("up", "-d", "--wait", "--wait-timeout", "120")
            assert request("/api/health/live")[1] == {"status": "alive"}
            assert request("/api/health/ready")[1] == {"status": "ready"}
            with urllib.request.urlopen(origin, timeout=15) as response:
                html = response.read().decode()
                assert "Salary management" in html and "HR sign-in" in html
            print("PASS: built frontend and readiness/liveness through same-origin proxy", flush=True)
            web_id = run("ps", "-q", "web", capture=True).strip()
            inspect = subprocess.check_output(["docker", "inspect", web_id], text=True)
            assert not json.loads(inspect)[0]["Mounts"]
            assert api_python("import os,sqlite3; c=sqlite3.connect(os.environ['DATABASE_PATH']); "
                              "print(c.execute('PRAGMA journal_mode').fetchone()[0])") == "wal"
            print("PASS: live database uses WAL; running web container has no mounts", flush=True)

            request("/api/auth/session", expected=401)
            request("/api/analytics/summaries", expected=401)
            request("/api/analytics/compa-ratio", expected=401)
            request("/api/analytics/equity", expected=401)
            request("/api/auth/login", {"email": email, "password": password},
                    {"Origin": "https://attacker.example"}, expected=403)
            request("/api/auth/login", {"email": email, "password": "wrong"},
                    {"Origin": origin}, expected=401)
            headers, session = request("/api/auth/login", {"email": email, "password": password},
                                       {"Origin": origin})
            assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=lax" in headers["Set-Cookie"]
            assert request("/api/auth/session")[1] == session
            request("/api/auth/logout", {}, {"Origin": origin}, expected=403)
            print("PASS: authentication, cookie forwarding, bad credentials and CSRF rejection", flush=True)
            assert api_python("import urllib.request,urllib.error; "
                              "\ntry: urllib.request.urlopen('http://127.0.0.1:8000/auth/session')"
                              "\nexcept urllib.error.HTTPError as e: print(e.code)") == "401"

            # Loading is an explicit API-container command, never a startup hook.
            run("cp", str(ROOT / "data/employees_10000.xlsx"), "api:/tmp/employees_10000.xlsx")
            loaded = json.loads(run("exec", "-T", "api", "python", "-m", "app.load_bands",
                                    "/tmp/employees_10000.xlsx", capture=True))
            assert loaded["created"] == 48
            initial_catalog = request("/api/bands")[1]
            assert initial_catalog["total"] == 48
            assert {item["currency"] for item in initial_catalog["items"]} == {"INR", "USD", "GBP", "EUR", "SGD", "AED"}
            conflict = subprocess.run([*compose, "exec", "-T", "api", "python", "-m", "app.load_bands",
                                       "/tmp/employees_10000.xlsx"], env=env, cwd=ROOT,
                                      capture_output=True, text=True)
            assert conflict.returncode == 1 and json.loads(conflict.stderr)["existing_count"] == 48
            assert request("/api/bands")[1] == initial_catalog
            api_python("from app.database import transaction; from app.models import Employee; "
                       "from app.repository import insert_employee; from pathlib import Path; import os; "
                       "\nwith transaction(Path(os.environ['DATABASE_PATH'])) as c:"
                       "\n insert_employee(c, Employee('STEP3', 'Band Test', 'step3@example.com', 'Sales', 'L1', 'India', 60000000))")
            employee_state_code = (
                "from app.database import connect; from pathlib import Path; import os,json; "
                "\nwith connect(Path(os.environ['DATABASE_PATH'])) as c:"
                "\n row=dict(c.execute(\"SELECT * FROM employees WHERE employee_id='STEP3'\").fetchone())"
                "\n match=c.execute(\"SELECT b.midpoint_minor FROM employees e LEFT JOIN salary_bands b ON e.country=b.country AND e.level=b.level WHERE e.employee_id='STEP3'\").fetchone()[0]"
                "\n print(json.dumps({'employee': row, 'status': 'Unbanded' if match is None else 'Band-matched'}))"
            )
            original_employee = json.loads(api_python(employee_state_code))["employee"]
            _, original_band = request("/api/bands/India/L1")
            mutation_headers = {"Origin": origin, "X-CSRF-Token": session["csrf_token"], "If-Match": original_band["etag"]}
            request("/api/bands/India/L1", headers=mutation_headers, method="DELETE", expected=400)
            removed = request("/api/bands/India/L1?confirmed=true", headers=mutation_headers, method="DELETE")[1]
            assert removed["affected_employee_count"] == 1
            assert json.loads(api_python(employee_state_code)) == {"employee": original_employee, "status": "Unbanded"}
            deleted_catalog = request("/api/bands")[1]
            assert deleted_catalog["total"] == 47
            unbanded_compa = request("/api/analytics/compa-ratio?country=India")[1]
            assert (unbanded_compa["total_count"], unbanded_compa["eligible_count"], unbanded_compa["excluded_unbanded_count"]) == (1, 0, 1)
            assert unbanded_compa["state"] == "no_eligible_data"
            assert len(unbanded_compa["buckets"]) == 3
            assert all(bucket["count"] == 0 and bucket["percentage"] is None for bucket in unbanded_compa["buckets"])
            unbanded_summary = request("/api/analytics/summaries?country=India")[1]
            assert unbanded_summary["countries"][0]["average"]["amount"] == "600000.00"
            unbanded_equity = request("/api/analytics/equity")[1]
            assert (unbanded_equity["total_count"], unbanded_equity["excluded_unbanded_count"]) == (1, 1)
            assert unbanded_equity["items"][0]["state"] == "unbanded"
            assert unbanded_equity["items"][0]["percentage_difference"] is None
            request("/api/analytics/summaries?page=1", expected=422)
            print("PASS: explicit 48-band load, six currencies, existing-catalog refusal, confirmed deletion and unchanged Unbanded employee", flush=True)

            sentinel = secrets.token_hex(16)
            api_python("from app.database import connect; from pathlib import Path; import os; "
                       "\nwith connect(Path(os.environ['DATABASE_PATH'])) as c:"
                       "\n c.execute('CREATE TABLE _step1_sentinel (value TEXT NOT NULL)')"
                       f"\n c.execute('INSERT INTO _step1_sentinel VALUES (?)', ({sentinel!r},))")
            old_ids = {service: run("ps", "-q", service, capture=True).strip() for service in ("api", "web")}
            run("down")
            run("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
            for service in ("api", "web"):
                assert run("ps", "-q", service, capture=True).strip() != old_ids[service]
            assert api_python("import os,sqlite3; c=sqlite3.connect(os.environ['DATABASE_PATH']); "
                              "print(c.execute('SELECT value FROM _step1_sentinel').fetchone()[0])") == sentinel
            assert request("/api/auth/session")[1] == session
            assert request("/api/health/ready")[1] == {"status": "ready"}
            assert request("/api/bands")[1] == deleted_catalog
            assert json.loads(api_python(employee_state_code)) == {"employee": original_employee, "status": "Unbanded"}
            recreate = {key: original_band[key] for key in ("country", "level", "minimum_amount", "midpoint_amount", "maximum_amount")}
            _, recreated = request("/api/bands", recreate, {"Origin": origin, "X-CSRF-Token": session["csrf_token"]}, expected=201)
            assert recreated["etag"] != original_band["etag"]
            request("/api/bands/India/L1?confirmed=true", headers=mutation_headers, method="DELETE", expected=412)
            assert json.loads(api_python(employee_state_code)) == {"employee": original_employee, "status": "Band-matched"}
            employee_body = {"employee_id": "STEP4", "name": 'Zoë, "HR"', "email": "step4@example.com",
                             "department": "Engineering", "country": "US", "level": "L8", "salary_amount": "123.455"}
            employee_headers = {"Origin": origin, "X-CSRF-Token": session["csrf_token"]}
            create_headers, employee = request("/api/employees", employee_body, employee_headers, expected=201)
            assert employee["salary_minor"] == "12346" and employee["band_status"] == "Band-matched"
            assert create_headers["ETag"] == employee["etag"]
            restored_catalog = request("/api/bands")[1]
            restored_analytics = request("/api/analytics/summaries")[1]
            restored_compa = request("/api/analytics/compa-ratio")[1]
            restored_equity = request("/api/analytics/equity")[1]
            assert (restored_equity["total_count"], restored_equity["insufficient_peers_count"]) == (2, 2)
            assert restored_analytics["total_count"] == 2
            populated = {group["country"]: group for group in restored_analytics["countries"] if group["count"]}
            assert set(populated) == {"India", "US"}
            assert (populated["India"]["currency"], populated["India"]["median"]["amount"]) == ("INR", "600000.00")
            assert (populated["US"]["currency"], populated["US"]["median"]["amount"]) == ("USD", "123.46")
            for grouping in ("countries", "departments", "levels"):
                assert sum(group["count"] for group in restored_analytics[grouping]) == 2
            assert (restored_compa["total_count"], restored_compa["eligible_count"], restored_compa["excluded_unbanded_count"]) == (2, 2, 0)
            assert [bucket["count"] for bucket in restored_compa["buckets"]] == [1, 1, 0]
            filtered_analytics = request("/api/analytics/summaries?country=US&department=engineering&level=L8")[1]
            assert filtered_analytics["total_count"] == 1 and filtered_analytics["countries"][0]["average"]["amount"] == "123.46"
            run("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
            assert request("/api/bands")[1] == restored_catalog
            assert restored_catalog["total"] == 48
            assert request("/api/analytics/summaries")[1] == restored_analytics
            assert request("/api/analytics/compa-ratio")[1] == restored_compa
            assert request("/api/analytics/equity")[1] == restored_equity
            equity_peers = []
            for identity, salary in (("STEP7A", "200000.00"), ("STEP7B", "400000.00")):
                _, peer = request("/api/employees", {"employee_id": identity, "name": identity,
                    "email": identity.lower() + "@example.com", "department": "Equity Reference",
                    "country": "India", "level": "L1", "salary_amount": salary}, employee_headers, expected=201)
                equity_peers.append(peer)
            filtered_equity = request("/api/analytics/equity?country=India&level=L1&department=sales")[1]
            assert (filtered_equity["total_count"], filtered_equity["available_count"]) == (1, 1)
            comparison = filtered_equity["items"][0]
            assert comparison["employee_id"] == "STEP3" and comparison["peer_count"] == 2
            assert comparison["peer_median"]["amount"] == "300000.00" and comparison["percentage_difference"] == "100.00"
            for peer in equity_peers:
                request("/api/employees/records/" + peer["employee_id"] + "?confirmed=true",
                        headers={**employee_headers, "If-Match": peer["etag"]}, method="DELETE")
            assert request("/api/analytics/equity")[1] == restored_equity
            print("PASS: equity target filters preserve full peers, exact differences, Unbanded/insufficient states and recreation persistence", flush=True)
            assert json.loads(api_python(employee_state_code)) == {"employee": original_employee, "status": "Band-matched"}
            assert request("/api/employees/records/step4")[1] == employee
            listing = request("/api/employees?search=STEP4&country=US&level=L8&department=engineering&sort=salary&direction=desc&page_size=50")[1]
            assert listing["items"] == [employee] and listing["total"] == 1 and listing["page_size"] == 50
            export_headers, content = request("/api/employees/export.csv?search=STEP4&country=US&level=L8&department=engineering&sort=salary&direction=desc")
            exported = list(csv.reader(io.StringIO(content.decode("utf-8"))))
            assert len(exported) == 2 and exported[1] == ["STEP4", 'Zoë, "HR"', "step4@example.com", "Engineering", "L8", "US", "USD", "123.46", "Band-matched"]
            assert "attachment" in export_headers["Content-Disposition"]
            edits = {key: value for key, value in employee_body.items() if key != "employee_id"}
            edits["name"] = "Edited HR"
            current_headers = {**employee_headers, "If-Match": employee["etag"]}
            _, edited = request("/api/employees/records/STEP4", edits, current_headers, method="PUT")
            assert edited["revision"] == 2
            request("/api/employees/records/STEP4", edits, current_headers, method="PUT", expected=412)
            request("/api/employees/records/STEP4?confirmed=true", headers={**employee_headers, "If-Match": edited["etag"]}, method="DELETE")
            assert request("/api/employees?search=STEP4")[1]["total"] == 0
            print("PASS: employee CRUD, revisions, combined directory query, exact CSV and employee persistence through web proxy", flush=True)
            api_python("from app.database import transaction; from pathlib import Path; import os; "
                       "\nwith transaction(Path(os.environ['DATABASE_PATH'])) as c:"
                       "\n c.execute(\"DELETE FROM employees WHERE employee_id='STEP3'\")")
            assert request("/api/analytics/summaries")[1]["total_count"] == 0
            assert request("/api/analytics/compa-ratio")[1]["state"] == "no_eligible_data"
            empty_equity = request("/api/analytics/equity")[1]
            assert empty_equity["total_count"] == 0 and empty_equity["items"] == []
            print("PASS: full-population exact analytics, currency separation, Unbanded reconciliation and current results after recreation/deletion", flush=True)
            print("PASS: bands persist through recreation; startup never reseeds deleted bands; recreation restores derived status and rejects old revisions", flush=True)
            api_python("import os,sqlite3; c=sqlite3.connect(os.environ['DATABASE_PATH']); "
                       "c.execute('DROP TABLE _step1_sentinel'); c.commit()")
            print("PASS: sentinel and HR session survive down/up with recreated containers; sentinel removed", flush=True)
            request("/api/auth/logout", {}, {"Origin": origin, "X-CSRF-Token": session["csrf_token"]}, expected=204)
            request("/api/auth/session", expected=401)
            run("stop", "api")
            request("/api/health/ready", expected=503)
            run("up", "-d", "--wait", "--wait-timeout", "120")
            assert request("/api/health/ready")[1] == {"status": "ready"}
            print("PASS: logout, API outage through proxy, and recovery", flush=True)
            print(f"DEPLOYMENT, BAND AND EMPLOYEE CHECKS PASSED. Retained test volume: {project}_salary_data", flush=True)
        finally:
            if started:
                run("logs", "--tail", "40")
                run("down")


if __name__ == "__main__":
    main()
