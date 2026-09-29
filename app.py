from __future__ import annotations

import csv
import io
import json
import os
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.db import DB_PATH, db_health, init_db
from core.shopee import ShopeeManager

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

init_db()
shopee = ShopeeManager()

app = FastAPI(title="Shopee Bulk Extractor", version="2.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ShopeeStart(BaseModel):
    links: str | list
    config: dict = {}


class CookieBody(BaseModel):
    cookie: str = ""


class SaveSessionBody(BaseModel):
    name: str = ""


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    job = shopee.get()
    database = db_health()
    return {
        "ok": database["ok"],
        "service": "shopee-bulk",
        "jobStatus": job.get("status") if job else "idle",
        "database": DB_PATH.name,
        "databasePath": database["path"],
        "persistentVolume": database["persistentVolume"],
    }


@app.get("/api/shopee/job")
def shopee_job():
    return {"job": shopee.get()}


@app.post("/api/shopee/start")
def shopee_start(body: ShopeeStart):
    try:
        return {"job": shopee.start(body.links, body.config)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/shopee/pause")
def shopee_pause():
    return {"job": shopee.pause()}


@app.post("/api/shopee/resume")
def shopee_resume():
    try:
        return {"job": shopee.resume()}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/shopee/stop")
def shopee_stop():
    return {"job": shopee.stop()}


@app.post("/api/shopee/retry")
def shopee_retry():
    return {"job": shopee.retry_failed()}


@app.delete("/api/shopee/job")
def shopee_clear():
    shopee.clear()
    return {"ok": True}


@app.post("/api/shopee/dedupe")
def shopee_dedupe():
    try:
        job, removed = shopee.dedupe_products()
        return {"job": job, "removed": removed}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/shopee/brand-categories")
def shopee_brand_categories(body: CookieBody):
    try:
        return {"job": shopee.fetch_brand_categories(body.cookie)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/shopee/sessions")
def shopee_sessions():
    sessions = shopee.saved_sessions()
    return {"sessions": [{k: v for k, v in item.items() if k != "job"} for item in sessions]}


@app.post("/api/shopee/sessions")
def shopee_save_session(body: SaveSessionBody):
    try:
        sessions = shopee.save_current_session(body.name)
        return {"sessions": [{k: v for k, v in item.items() if k != "job"} for item in sessions]}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/shopee/sessions/{session_id}/load")
def shopee_load_saved_session(session_id: str):
    try:
        return {"job": shopee.load_saved_session(session_id)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/shopee/sessions/{session_id}")
def shopee_delete_saved_session(session_id: str):
    sessions = shopee.delete_saved_session(session_id)
    return {"sessions": [{k: v for k, v in item.items() if k != "job"} for item in sessions]}


@app.get("/api/shopee/session.json")
def shopee_session():
    job = shopee.get()
    if not job:
        raise HTTPException(404, "Belum ada session")
    data = json.dumps(
        {
            "version": 1,
            "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "job": job,
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    return StreamingResponse(
        iter([data]),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=shopee_session_{job.get('jobId', 'job')}.json"
        },
    )


@app.post("/api/shopee/session")
async def shopee_load_session(file: UploadFile = File(...)):
    try:
        payload = json.loads((await file.read()).decode("utf-8-sig"))
        return {"job": shopee.restore(payload)}
    except Exception as exc:
        raise HTTPException(400, f"Session tidak valid: {exc}") from exc


@app.get("/api/shopee/export.csv")
def shopee_export():
    job = shopee.get()
    if not job:
        raise HTTPException(404, "Belum ada job")

    items = job.get("items", [])
    active_counts: dict[str, int] = {}
    product_counts: dict[str, int] = {}
    for item in items:
        if item.get("status") == "success" and item.get("adLibraryUrl"):
            if item.get("shopId"):
                active_counts[item["shopId"]] = active_counts.get(item["shopId"], 0) + 1
            if item.get("productId"):
                product_counts[item["productId"]] = product_counts.get(item["productId"], 0) + 1

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "Destination URL",
            "Link Iklan Aktif",
            "Shop ID",
            "Product ID",
            "Jumlah Iklan Aktif Toko",
            "Jumlah Iklan Produk",
            "Brand Offer URL",
            "Product Offer URL",
            "Status",
            "Error",
        ]
    )
    for item in items:
        if not item.get("productId"):
            continue
        shop_id = item.get("shopId", "")
        product_id = item.get("productId", "")
        writer.writerow(
            [
                item.get("shortUrl", ""),
                item.get("adLibraryUrl", ""),
                shop_id,
                product_id,
                item.get("activeAdCount", active_counts.get(shop_id, 0)),
                item.get("productAdCount", product_counts.get(product_id, 0)),
                f"https://affiliate.shopee.co.id/offer/brand_offer/{shop_id}" if shop_id else "",
                f"https://affiliate.shopee.co.id/offer/product_offer/{product_id}" if product_id else "",
                item.get("status", ""),
                item.get("error", ""),
            ]
        )

    filename = f"shopee_bulk_{job.get('jobId', 'job')}.csv"
    return StreamingResponse(
        iter([out.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "info"),
    )
