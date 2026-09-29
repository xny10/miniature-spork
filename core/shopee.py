from __future__ import annotations
import csv
import io
import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse
import requests
from .db import get_json, set_json, delete_key

JOB_KEY = "shopee_active_job"
SAVED_SESSIONS_KEY = "shopee_saved_sessions"
DEFAULT_CONFIG = {
    "concurrency": 5,
    "batchDelayMin": 2000,
    "batchDelayMax": 6000,
    "requestTimeout": 15000,
    "maxRetries": 2,
    "retryDelayMin": 5000,
    "retryDelayMax": 12000,
    "rateLimitCooldownMin": 30000,
    "rateLimitCooldownMax": 90000,
}

class ParseError(Exception):
    def __init__(self, message, code="ERROR"):
        super().__init__(message); self.code = code


def now_ms(): return int(time.time()*1000)

def short_error(s):
    return re.sub(r"https?://\S+", "", str(s or "")).strip()[:180]


def normalize_rows(raw):
    if isinstance(raw, list):
        entries = raw
    else:
        text = str(raw or "")
        entries = []
        lines = [x for x in text.splitlines() if x.strip()]
        if lines and "," in lines[0]:
            try:
                rows = list(csv.reader(io.StringIO(text)))
                header = [h.lower().lstrip("\ufeff").strip() for h in rows[0]]
                has_header = any("destination" in h for h in header) or any("link iklan" in h for h in header)
                dest_idx = next((i for i,h in enumerate(header) if "destination" in h), 0)
                active_idx = next((i for i,h in enumerate(header) if "link iklan" in h or "adlibrary" in h or "ad library" in h), 1)
                for row in rows[1:] if has_header else rows:
                    short = row[dest_idx].strip() if len(row)>dest_idx else ""
                    active = row[active_idx].strip() if len(row)>active_idx else ""
                    entries.append({"shortUrl": short, "adLibraryUrl": active})
            except Exception:
                entries = lines
        else:
            entries = lines
    seen = {}
    for entry in entries:
        if isinstance(entry, dict):
            val = str(entry.get("shortUrl") or entry.get("destinationUrl") or "")
            active = str(entry.get("adLibraryUrl") or entry.get("activeAdLink") or "")
        else:
            val, active = str(entry), ""
        m = re.search(r"https?://s\.shopee\.co\.id/[^\s,\"'|]+", val, re.I)
        if not m: continue
        short = re.sub(r"[),.;]+$", "", m.group(0).strip())
        key = short.lower()
        seen[key] = {"shortUrl": short, "adLibraryUrl": active or seen.get(key,{}).get("adLibraryUrl","")}
    return list(seen.values())


def safe_decode(value):
    out = str(value or "")
    for _ in range(3):
        try:
            d = unquote(out)
            if d == out: break
            out = d
        except Exception: break
    return out


def extract_ids_from_url(value):
    decoded = safe_decode(value).replace("\\u002F", "/").replace("\\/", "/")
    patterns = [
        (re.compile(r"/product/(\d+)/(\d+)", re.I), False),
        (re.compile(r"/[^/?#]+/(\d{6,})/(\d{6,})(?:[/?#]|$)", re.I), False),
        (re.compile(r"(?:^|[?&#])shopid=(\d+).*?(?:[?&#])itemid=(\d+)", re.I), False),
        (re.compile(r"(?:^|[?&#])shop_id=(\d+).*?(?:[?&#])item_id=(\d+)", re.I), False),
        (re.compile(r"(?:^|[?&#])itemid=(\d+).*?(?:[?&#])shopid=(\d+)", re.I), True),
        (re.compile(r"(?:^|[?&#])item_id=(\d+).*?(?:[?&#])shop_id=(\d+)", re.I), True),
        (re.compile(r"(?:-|%2D)i\.(\d+)\.(\d+)", re.I), False),
        (re.compile(r"/i\.(\d+)\.(\d+)", re.I), False),
        (re.compile(r"shop(?:id|_id)[\"'=: ]+(\d+).*?item(?:id|_id)[\"'=: ]+(\d+)", re.I|re.S), False),
        (re.compile(r"item(?:id|_id)[\"'=: ]+(\d+).*?shop(?:id|_id)[\"'=: ]+(\d+)", re.I|re.S), True),
    ]
    for pat, rev in patterns:
        m = pat.search(decoded)
        if m:
            return (m.group(2), m.group(1)) if rev else (m.group(1), m.group(2))
    return "", ""


def resolve_shortlink(short_url, timeout_ms=15000):
    if not re.match(r"^https?://s\.shopee\.co\.id/", short_url, re.I):
        raise ParseError("Shortlink bukan domain s.shopee.co.id", "INVALID_SHORTLINK")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = requests.get(short_url, allow_redirects=True, timeout=timeout_ms/1000, headers=headers)
    except requests.Timeout:
        raise ParseError("Request timeout", "TIMEOUT")
    except requests.RequestException as e:
        raise ParseError(str(e), "NETWORK")
    if r.status_code == 429: raise ParseError("HTTP 429 Too Many Requests", "RATE_LIMIT")
    if r.status_code >= 400: raise ParseError(f"HTTP {r.status_code}", "NETWORK")
    full = r.url or short_url
    body = r.text if "text" in r.headers.get("content-type", "") or "html" in r.headers.get("content-type", "") else ""
    candidates = {full}
    for v in [full, safe_decode(full)]: candidates.add(v)
    try:
        parsed = urlparse(full)
        for part in parsed.query.split("&"):
            if "=" in part:
                candidates.add(safe_decode(part.split("=",1)[1]))
    except Exception: pass
    for raw in re.findall(r"https?:\\?/\\?/[^\s\"'<>]+", body, re.I):
        candidates.add(safe_decode(raw.replace("\\/", "/")))
    for cand in candidates:
        shop, prod = extract_ids_from_url(cand)
        if shop and prod:
            return {"fullUrl": full, "productUrl": cand, "shopId": shop, "productId": prod}
    # limited body scan fallback
    shop, prod = extract_ids_from_url(body[:2_000_000])
    if shop and prod:
        return {"fullUrl": full, "productUrl": full, "shopId": shop, "productId": prod}
    raise ParseError(f"URL akhir bukan halaman produk Shopee. Final URL: {full}", "NOT_PRODUCT")

class ShopeeManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.thread = None
        self.stop_flag = False
        self._recover()

    def _recover(self):
        job = get_json(JOB_KEY)
        if not job: return
        changed = False
        for item in job.get("items", []):
            if item.get("status") in ("processing","retrying"):
                item["status"]="queued"; item["error"]=""; changed=True
        if job.get("status") in ("processing","waiting"):
            job["status"]="paused"; job["waitingUntil"]=None; changed=True
        if changed: self.save(job)

    def get(self): return get_json(JOB_KEY)

    def save(self, job):
        job["updatedAt"] = now_ms()
        items = job.get("items",[])
        job["processed"] = sum(i.get("status") in ("success","failed","cancelled") for i in items)
        job["success"] = sum(i.get("status")=="success" for i in items)
        job["failed"] = sum(i.get("status")=="failed" for i in items)
        job["cancelled"] = sum(i.get("status")=="cancelled" for i in items)
        set_json(JOB_KEY, job)
        return job

    def start(self, raw, config):
        rows = normalize_rows(raw)
        if not rows: raise ValueError("Tidak ada shortlink Shopee valid")
        ts = now_ms(); cfg={**DEFAULT_CONFIG, **(config or {})}
        cfg["concurrency"] = max(1,min(10,int(cfg.get("concurrency",5))))
        job={"jobId":f"job_{ts}","status":"processing","createdAt":ts,"updatedAt":ts,"startedAt":ts,"finishedAt":None,"waitingUntil":None,"concurrency":cfg["concurrency"],"config":cfg,"total":len(rows),"processed":0,"success":0,"failed":0,"cancelled":0,
             "items":[{"id":f"item_{n+1}_{ts}","shortUrl":r["shortUrl"],"adLibraryUrl":r.get("adLibraryUrl", ""),"status":"queued","fullUrl":"","shopId":"","productId":"","attempts":0,"error":"","startedAt":None,"finishedAt":None} for n,r in enumerate(rows)]}
        self.stop_flag=False; self.save(job); self._ensure_thread(); return job

    def _ensure_thread(self):
        if self.thread and self.thread.is_alive(): return
        self.thread=threading.Thread(target=self._loop,daemon=True); self.thread.start()

    def pause(self):
        job=self.get();
        if job: job["status"]="paused"; job["waitingUntil"]=None; self.save(job)
        return job
    def resume(self):
        job=self.get()
        if not job: raise ValueError("Tidak ada job")
        if job.get("status") in ("completed","stopped") and not any(i.get("status")=="queued" for i in job.get("items",[])): return job
        self.stop_flag=False; job["status"]="processing"; job["startedAt"]=job.get("startedAt") or now_ms(); self.save(job); self._ensure_thread(); return job
    def stop(self):
        self.stop_flag=True; job=self.get()
        if job:
            job["status"]="stopped"; job["waitingUntil"]=None; job["finishedAt"]=now_ms()
            for i in job.get("items",[]):
                if i.get("status") in ("queued","retrying","processing"):
                    i["status"]="cancelled"; i["error"]="Dihentikan pengguna"; i["finishedAt"]=now_ms()
            self.save(job)
        return job
    def retry_failed(self):
        job=self.get()
        if not job: return None
        for i in job.get("items",[]):
            if i.get("status")=="failed":
                i.update(status="queued",attempts=0,error="",fullUrl="",shopId="",productId="",startedAt=None,finishedAt=None)
        job["status"]="processing"; job["finishedAt"]=None; self.save(job); self._ensure_thread(); return job
    def clear(self): delete_key(JOB_KEY)

    def saved_sessions(self):
        sessions = get_json(SAVED_SESSIONS_KEY, []) or []
        return sorted(sessions, key=lambda item: item.get("savedAt", 0), reverse=True)

    def save_current_session(self, name=""):
        job = self.get()
        if not job:
            raise ValueError("Belum ada job untuk disimpan")
        sessions = [s for s in self.saved_sessions() if s.get("jobId") != job.get("jobId")]
        ts = now_ms()
        success = int(job.get("success", 0))
        failed = int(job.get("failed", 0))
        total = int(job.get("total", len(job.get("items", []))))
        label = str(name or "").strip() or f"Session {datetime.fromtimestamp(ts / 1000).strftime('%d %b %H:%M')}"
        sessions.insert(0, {
            "id": f"saved_{ts}",
            "name": label,
            "jobId": job.get("jobId"),
            "status": job.get("status", "unknown"),
            "total": total,
            "success": success,
            "failed": failed,
            "savedAt": ts,
            "job": job,
        })
        set_json(SAVED_SESSIONS_KEY, sessions[:50])
        return self.saved_sessions()

    def load_saved_session(self, session_id):
        target = next((s for s in self.saved_sessions() if s.get("id") == session_id), None)
        if not target:
            raise ValueError("Session tersimpan tidak ditemukan")
        return self.restore({"job": target.get("job")})

    def delete_saved_session(self, session_id):
        sessions = [s for s in self.saved_sessions() if s.get("id") != session_id]
        set_json(SAVED_SESSIONS_KEY, sessions)
        return sessions

    def restore(self, payload):
        raw = payload.get("job",payload) if isinstance(payload,dict) else None
        if not raw or not isinstance(raw.get("items"),list): raise ValueError("Session tidak valid")
        ts=now_ms(); raw["status"]="paused" if raw.get("status") in ("processing","waiting") else raw.get("status","paused"); raw["waitingUntil"]=None; raw["updatedAt"]=ts
        for n,i in enumerate(raw["items"]):
            i["id"]=i.get("id") or f"item_{n+1}_{ts}"; i["adLibraryUrl"]=i.get("adLibraryUrl") or i.get("activeAdLink") or i.get("linkIklanAktif") or ""
            if i.get("status") in ("processing","retrying"): i["status"]="queued"
        return self.save(raw)

    def dedupe_products(self):
        job=self.get()
        if not job: return None,0
        if job.get("status") in ("processing","waiting"): raise ValueError("Pause/stop job dulu")
        active_counts={}; product_counts={}
        for i in job.get("items",[]):
            if i.get("status")=="success" and i.get("adLibraryUrl"):
                if i.get("shopId"): active_counts[i["shopId"]]=active_counts.get(i["shopId"],0)+1
                if i.get("productId"): product_counts[i["productId"]]=product_counts.get(i["productId"],0)+1
        seen=set(); kept=[]
        for i in job.get("items",[]):
            pid=i.get("productId")
            if pid and pid in seen: continue
            if pid: seen.add(pid)
            i["activeAdCount"]=active_counts.get(i.get("shopId"), i.get("activeAdCount",0))
            i["productAdCount"]=product_counts.get(pid, i.get("productAdCount",0)) if pid else i.get("productAdCount",0)
            kept.append(i)
        removed=len(job["items"])-len(kept); job["items"]=kept; job["total"]=len(kept); self.save(job); return job,removed

    def _loop(self):
        while True:
            with self.lock:
                job=self.get()
                if not job or job.get("status")!="processing" or self.stop_flag: return
                batch=[i for i in job.get("items",[]) if i.get("status")=="queued"][:max(1,int(job.get("concurrency",5)))]
                if not batch:
                    job["status"]="completed"; job["finishedAt"]=now_ms(); self.save(job); return
                ids=[i["id"] for i in batch]
                for i in job["items"]:
                    if i["id"] in ids: i["status"]="processing"; i["startedAt"]=now_ms()
                self.save(job)
                cfg=job.get("config",DEFAULT_CONFIG)
            results={}
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futs={pool.submit(self._process_item,dict(i),cfg):i["id"] for i in batch}
                for f in as_completed(futs):
                    iid=futs[f]
                    try: results[iid]=(True,f.result())
                    except Exception as e: results[iid]=(False,e)
            delay=0
            with self.lock:
                job=self.get()
                if not job: return
                for i in job.get("items",[]):
                    if i["id"] not in results: continue
                    ok,res=results[i["id"]]
                    i["finishedAt"]=now_ms()
                    if ok:
                        i.update(res); i["status"]="success"; i["error"]=""
                    else:
                        i["status"]="failed"; i["error"]=short_error(str(res))
                self.save(job)
                if job.get("status")!="processing" or self.stop_flag: return
                if any(i.get("status")=="queued" for i in job.get("items",[])):
                    lo=max(0,int(cfg.get("batchDelayMin",2000))); hi=max(lo,int(cfg.get("batchDelayMax",6000))); delay=random.randint(lo,hi) if hi else 0
                    if delay:
                        job["status"]="waiting"; job["waitingUntil"]=now_ms()+delay; self.save(job)
            if delay:
                end=time.time()+delay/1000
                while time.time()<end:
                    time.sleep(min(.25,end-time.time()))
                    current=self.get()
                    if self.stop_flag or not current or current.get("status") in ("paused","stopped"): return
                job=self.get()
                if not job: return
                job["status"]="processing"; job["waitingUntil"]=None; self.save(job)

    def _process_item(self,item,cfg):
        max_tries=max(1,int(cfg.get("maxRetries",2))+1); last=None
        for attempt in range(1,max_tries+1):
            item["attempts"]=attempt
            try: return resolve_shortlink(item["shortUrl"],int(cfg.get("requestTimeout",15000)))
            except ParseError as e:
                last=e
                if e.code in ("INVALID_SHORTLINK","NOT_PRODUCT"): break
                if attempt<max_tries:
                    if e.code=="RATE_LIMIT": lo,hi=int(cfg.get("rateLimitCooldownMin",30000)),int(cfg.get("rateLimitCooldownMax",90000))
                    else: lo,hi=int(cfg.get("retryDelayMin",5000)),int(cfg.get("retryDelayMax",12000))
                    time.sleep(random.randint(max(0,lo),max(lo,hi))/1000)
        raise last or ParseError("Gagal memproses link")

    def fetch_brand_categories(self, cookie=""):
        job=self.get()
        if not job: raise ValueError("Tidak ada job aktif")
        session=requests.Session(); headers={"User-Agent":"Mozilla/5.0 Chrome/153 Safari/537.36","Accept":"application/json, text/plain, */*","X-Requested-With":"XMLHttpRequest"}
        if cookie: headers["Cookie"]=cookie
        cache={}
        for sid in sorted({i.get("shopId") for i in job.get("items",[]) if i.get("shopId")}):
            try:
                r=session.get(f"https://affiliate.shopee.co.id/api/v3/offer/shop?shop_id={sid}",headers=headers,timeout=15)
                if r.status_code in (401,403): raise RuntimeError(f"Belum login / tidak punya akses affiliate ({r.status_code})")
                if r.status_code==429: raise RuntimeError("HTTP 429 Too Many Requests")
                r.raise_for_status(); data=r.json(); flat={}
                def walk(x,p=""):
                    if isinstance(x,dict):
                        for k,v in x.items(): walk(v,f"{p}.{k}" if p else k)
                    elif isinstance(x,list):
                        for n,v in enumerate(x): walk(v,f"{p}.{n}")
                    else: flat[p]=x
                walk(data)
                lower={k.lower():v for k,v in flat.items()}
                def pick(keys):
                    for k in keys:
                        v=lower.get(k.lower())
                        if v not in (None,""): return str(v)
                    return ""
                cache[sid]={"shopName":pick(["data.shop_name","data.shopName","data.name","shop_name","shopName","name"]),"brandCategory":pick(["data.category_name","data.categoryName","data.primary_category_name","category_name","categoryName","category"]),"commissionRate":pick(["data.commission_rate","data.commissionRate","commission_rate","commissionRate"]),"offerStatus":pick(["data.status","data.offer_status","status","offer_status"]),"offerId":pick(["data.offer_id","offer_id","offerId"]),"brandOfferError":""}
            except Exception as e: cache[sid]={"brandOfferError":short_error(str(e))}
            time.sleep(.3)
        for i in job.get("items",[]):
            if i.get("shopId"): i.update(cache.get(i["shopId"],{}))
        return self.save(job)
