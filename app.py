"""Caravan supplier price lists: a small API for caravansanat.com.

POST /jobs/pull   JSON {"meta_url": "...", "files": [{"url": "...", "name": "list1.pdf"}]}: the service downloads the meta
                  and the files itself (for a site whose server cannot upload abroad; hosts in PL_PULL_HOSTS)
POST /jobs        files[] + meta (JSON): {supplier_id, supplier_name, unit_hint, offers: [...], aliases: {key: offer_id},
                  callback_url}  ->  {"id": "<job>"}
GET  /jobs/<id>   {"status": queued|reading|matching|done|failed, "progress": .., "result": {...}}
GET  /health      {"ok": true, "model": .., "key": true|false}

Every call but /health needs the header X-PL-Token equal to the PL_TOKEN environment variable. Secrets live only in
the environment (OPENROUTER_API_KEY, PL_TOKEN); files stay on the server for PL_KEEP_DAYS (default 30), never in git.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware

from pl import llm
from pl.match import match, unmatched_offers
from pl.read import read_files

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pricelist")

DATA = Path(os.environ.get("PL_DATA_DIR", "/data")) / "jobs"
MAX_FILES = 40
MAX_BYTES = 30 * 1024 * 1024
ALLOWED = (".pdf", ".xlsx", ".xlsm", ".csv", ".jpg", ".jpeg", ".png", ".webp")

app = FastAPI(title="Caravan price lists", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)   # results reach Iran over a slow line: ~9x smaller
_lock = threading.Lock()


def _auth(token: str | None) -> None:
    want = os.environ.get("PL_TOKEN", "")
    if not want or not token or not hmac.compare_digest(want, token):
        raise HTTPException(status_code=401, detail="bad token")


def _save(job: Path, data: dict) -> None:
    with _lock:
        cur = json.loads((job / "job.json").read_text()) if (job / "job.json").exists() else {}
        cur.update(data)
        tmp = job / "job.json.tmp"
        tmp.write_text(json.dumps(cur, ensure_ascii=False))
        tmp.replace(job / "job.json")


def _cleanup() -> None:
    days = float(os.environ.get("PL_KEEP_DAYS", "30"))
    if not DATA.exists():
        return
    for d in DATA.iterdir():
        try:
            if d.is_dir() and time.time() - d.stat().st_mtime > days * 86400:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            continue


def _run(job: Path, meta: dict) -> None:
    try:
        files = sorted((job / "files").iterdir())
        _save(job, {"status": "reading", "progress": 0})
        got = read_files(files, meta.get("unit_hint") or "auto",
                         progress=lambda n, t: _save(job, {"progress": round(n / max(t, 1) * 0.8, 2)}))
        _save(job, {"status": "matching", "progress": 0.85})
        offers = meta.get("offers") or []
        m = match(got["rows"], offers, meta.get("aliases") or {}, float(meta.get("big_pct") or 20))
        miss = unmatched_offers(m["rows"], offers)
        result = {"rows": m["rows"], "counts": m["counts"], "not_in_list": miss[:300], "not_in_list_count": len(miss),
                  "failed": got["failed"], "pages": got["pages"], "calls": got["calls"],
                  "usd": round(got["usd"] + m["usd"], 4), "model": llm.model()}
        (job / "result.json").write_text(json.dumps(result, ensure_ascii=False))
        _save(job, {"status": "done", "progress": 1, "done_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "counts": m["counts"], "usd": result["usd"]})
    except Exception as e:                               # noqa: BLE001
        log.exception("job %s", job.name)
        _save(job, {"status": "failed", "error": str(e)[:300]})
    cb = meta.get("callback_url")
    if cb and re.match(r"^https://", str(cb)):
        try:
            requests.post(cb, json={"job": job.name, "status": json.loads((job / "job.json").read_text()).get("status")},
                          headers={"X-PL-Token": os.environ.get("PL_TOKEN", "")}, timeout=20)
        except requests.RequestException as e:
            log.info("callback %s: %s", job.name, e)


@app.get("/health")
def health():
    return {"ok": True, "model": llm.model(), "key": llm.has_key(), "token": bool(os.environ.get("PL_TOKEN"))}


@app.post("/jobs")
async def create_job(meta: str = Form("{}"), files: list[UploadFile] = File(...), x_pl_token: str | None = Header(None)):
    _auth(x_pl_token)
    try:
        meta_d = json.loads(meta or "{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="meta is not JSON")
    if not files or len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"1 to {MAX_FILES} files")
    _cleanup()
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    job = DATA / jid
    (job / "files").mkdir(parents=True)
    for i, up in enumerate(files):
        name = Path(up.filename or f"file{i}").name
        if not name.lower().endswith(ALLOWED):
            shutil.rmtree(job, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"{name}: نوع فایل پشتیبانی نمی‌شود")
        data = await up.read()
        if len(data) > MAX_BYTES:
            shutil.rmtree(job, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"{name}: بزرگ‌تر از ۳۰ مگابایت")
        safe = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE)[-120:]
        (job / "files" / f"{i:02d}-{safe}").write_bytes(data)
    (job / "meta.json").write_text(json.dumps({k: v for k, v in meta_d.items() if k != "offers"}, ensure_ascii=False))
    _save(job, {"id": jid, "status": "queued", "progress": 0, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "supplier_id": meta_d.get("supplier_id"), "files": len(files), "offers": len(meta_d.get("offers") or [])})
    threading.Thread(target=_run, args=(job, meta_d), daemon=True).start()
    return {"id": jid}


def _pull_ok(url: str) -> bool:
    hosts = [h.strip().lower() for h in os.environ.get("PL_PULL_HOSTS", "caravansanat.com").split(",") if h.strip()]
    m = re.match(r"^https://([^/:?#]+)", str(url or ""))
    return bool(m) and any(m.group(1).lower() == h or m.group(1).lower().endswith("." + h) for h in hosts)


def _download(url: str, limit: int) -> bytes:
    with requests.get(url, timeout=(20, 180), stream=True, headers={"User-Agent": "caravan-pricelist"}) as r:
        r.raise_for_status()
        out = b""
        for chunk in r.iter_content(65536):
            out += chunk
            if len(out) > limit:
                raise ValueError("file too big")
        return out


def _pull_and_run(job: Path, body: dict) -> None:
    try:
        _save(job, {"status": "downloading"})
        meta = json.loads(_download(body["meta_url"], 40 * 1024 * 1024).decode("utf-8"))
        for i, f in enumerate(body.get("files") or []):
            name = Path(str(f.get("name") or f"file{i}")).name
            safe = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE)[-120:]
            (job / "files" / f"{i:02d}-{safe}").write_bytes(_download(f["url"], MAX_BYTES))
        (job / "meta.json").write_text(json.dumps({k: v for k, v in meta.items() if k != "offers"}, ensure_ascii=False))
        _save(job, {"supplier_id": meta.get("supplier_id"), "offers": len(meta.get("offers") or [])})
    except Exception as e:                               # noqa: BLE001
        log.warning("pull %s: %s", job.name, e)
        _save(job, {"status": "failed", "error": f"دریافت فایل از سایت ممکن نشد: {e}"[:300]})
        return
    _run(job, meta)


@app.post("/jobs/pull")
async def create_pull_job(body: dict, x_pl_token: str | None = Header(None)):
    _auth(x_pl_token)
    files = body.get("files") or []
    if not _pull_ok(body.get("meta_url", "")) or not files or len(files) > MAX_FILES or \
            not all(_pull_ok(f.get("url", "")) and Path(str(f.get("name") or "")).suffix.lower() in ALLOWED for f in files):
        raise HTTPException(status_code=400, detail="bad meta_url or files")
    _cleanup()
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    job = DATA / jid
    (job / "files").mkdir(parents=True)
    _save(job, {"id": jid, "status": "queued", "progress": 0, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "files": len(files), "pulled": True})
    threading.Thread(target=_pull_and_run, args=(job, body), daemon=True).start()
    return {"id": jid}


@app.get("/jobs/{jid}")
def get_job(jid: str, x_pl_token: str | None = Header(None)):
    _auth(x_pl_token)
    if not re.fullmatch(r"[\w\-]{10,60}", jid):
        raise HTTPException(status_code=404)
    job = DATA / jid
    if not (job / "job.json").exists():
        raise HTTPException(status_code=404)
    out = json.loads((job / "job.json").read_text())
    if out.get("status") == "done" and (job / "result.json").exists():
        out["result"] = json.loads((job / "result.json").read_text())
    return out
