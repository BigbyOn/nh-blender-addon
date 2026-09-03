# ------------------------------------------------------------------------
#  NH Work Report server (tiny reference implementation)
# ------------------------------------------------------------------------
#  Collects signed work reports from the NH Blender add-on client.
#
#  Run:
#    pip install fastapi uvicorn
#    set NH_WORK_SECRET=<shared secret or per-license>
#    uvicorn nh_work_server:app --host 0.0.0.0 --port 8059
#
#  Client sends: POST /v1/heartbeat  (HMAC-SHA256 signature over the raw
#  body in X-NH-Sig, key = sha256("nh-work:" + license)).
#
#  Data protection:
#   - signature verified per request (rejects tampered body)
#   - machine identifiers are client-side hashed (no raw hostnames)
#   - plain endpoints below; use HTTPS reverse proxy (nginx/caddy) in prod
# ------------------------------------------------------------------------

import os
import hashlib
import hmac
import sqlite3
import time
from datetime import datetime, timezone

import fastapi
from fastapi import FastAPI, Header, Request, Response, UploadFile  # noqa: F401
import pydantic

DB_PATH = os.environ.get("NH_WORK_DB", "nh_work_reports.sqlite3")


class WorkReport(pydantic.BaseModel):
    plugin: str = ""
    version: str = ""
    license: str = ""
    machine_id: str = ""
    project: str = ""
    session_id: str = ""
    session_start: int = 0
    session_end: int = 0
    active_seconds: int = 0
    work_units: int = 0
    error_free_runs: int = 0
    event_hits: int = 0
    totals: list = []


def _sign_key(license_id: str) -> bytes:
    return hashlib.sha256(("nh-work:" + str(license_id)).encode()).hexdigest().encode()


def _secret() -> str:
    return os.environ.get("NH_WORK_SECRET", "")


app = FastAPI(title="NH Work Reports", version="1.0")


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER,
            plugin TEXT, version TEXT, machine TEXT, project TEXT,
            license TEXT, session_id TEXT,
            session_start INTEGER, session_end INTEGER,
            active_seconds INTEGER, work_units INTEGER,
            error_free_runs INTEGER, event_hits INTEGER,
            totals_json TEXT
        )
        """
    )
    conn.commit()
    return conn


@app.post("/v1/heartbeat")
async def heartbeat_async(
    request: Request,
    x_nh_sig: str = Header(default=""),
    x_nh_license: str = Header(default=""),
):
    raw = await request.body()
    sig_ok = False
    try:
        expected = hmac.new(_sign_key(x_nh_license), raw, hashlib.sha256).hexdigest()
        sig_ok = hmac.compare_digest(expected, x_nh_sig)
    except Exception:
        sig_ok = False
    if not sig_ok:
        if _secret():
            try:
                expected2 = hmac.new(_sign_key(_secret()), raw, hashlib.sha256).hexdigest()
                sig_ok = hmac.compare_digest(expected2, x_nh_sig)
            except Exception:
                sig_ok = False
    if not sig_ok:
        return Response(status_code=401, content=b"bad signature")

    try:
        report = WorkReport.model_validate_json(raw)
    except Exception:
        return Response(status_code=400, content=b"bad payload")

    conn = _db()
    conn.execute(
        """
        INSERT OR IGNORE INTO reports (
            ts, plugin, version, machine, project, license, session_id,
            session_start, session_end, active_seconds, work_units,
            error_free_runs, event_hits, totals_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(time.time()),
            report.plugin,
            report.version,
            report.machine_id,
            report.project,
            x_nh_license,
            report.session_id,
            report.session_start,
            report.session_end,
            report.active_seconds,
            report.work_units,
            report.error_free_runs,
            report.event_hits,
            __import__("json").dumps(report.totals, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()
    return Response(status_code=202, content=b"ok")


@app.get("/v1/stats")
def stats(api_key: str = ""):
    if not api_key or hmac.compare_digest(api_key, os.environ.get("NH_WORK_ADMIN_KEY", "")):
        return Response(status_code=403, content=b"forbidden")
    conn = _db()
    rows = conn.execute(
        "SELECT project, license, COUNT(*), SUM(work_units), SUM(active_seconds) "
        "FROM reports GROUP BY project, license"
    ).fetchall()
    conn.close()
    return {
        "summaries": [
            {"project": r[0], "license": r[1], "sessions": r[2],
             "work_units": r[3], "active_seconds": r[4]}
            for r in rows
        ],
        "now_utc": datetime.now(timezone.utc).isoformat(),
    }


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("NH_WORK_PORT", "8059")))


if __name__ == "__main__":
    main()
