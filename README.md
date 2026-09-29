# Shopee Bulk Extractor — Railway Ready

Versi ini hanya berisi fitur **Shopee Bulk**. Modul Facebook/Meta Ads Library dan Playwright sudah dihapus agar deployment lebih ringan.

## Fitur

- Paste banyak shortlink `https://s.shopee.co.id/...`
- Import TXT/CSV
- CSV dengan kolom `Destination URL` + `Link Iklan Aktif` tetap didukung
- Resolve shortlink menjadi Shop ID + Product ID
- Concurrency, delay, retry, pause, resume, stop
- Retry item gagal
- Dedupe Product ID
- Copy Product ID, Brand Offer URL, Product Offer URL
- Save/load session JSON
- Export CSV
- Ambil kategori Brand Offer opsional dengan Cookie Shopee Affiliate
- SQLite untuk menyimpan job aktif
- Recovery session setelah service restart/redeploy

## Railway — konfigurasi yang sudah disiapkan

Project ini sudah punya:

- `Dockerfile` Railway-ready
- bind ke `0.0.0.0:$PORT`
- healthcheck endpoint `/api/health`
- `.railway/railway.ts` (Railway Infrastructure as Code)
- 1 replica
- volume `shopee-bulk-data` 512 MB
- region volume Singapore (`asia-southeast1-eqsg3a`)
- mount volume `/data`
- `DATA_DIR=/data`
- script setup Linux/macOS/WSL: `railway-setup.sh`
- script setup Windows PowerShell: `railway-setup.ps1`

## Opsi A — paling lengkap, Railway CLI

Pastikan Node.js dan Railway CLI sudah ada:

```bash
npm i -g @railway/cli
railway login
```

### Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\railway-setup.ps1
```

### Linux / macOS / WSL

```bash
chmod +x railway-setup.sh
./railway-setup.sh
```

Script akan:

1. install Railway IaC SDK
2. link ke project Railway
3. preview konfigurasi
4. membuat/mengatur service `shopee-bulk`
5. membuat volume `/data`
6. set 1 replica
7. set healthcheck `/api/health`
8. deploy app
9. mencoba generate public Railway domain

Railway IaC selalu menampilkan plan sebelum apply sehingga perubahan dapat dilihat sebelum diterapkan.

## Opsi B — GitHub → Railway

1. Extract ZIP lalu push seluruh isi project ke repository GitHub.
2. Railway → **New Project / New Service → GitHub Repo**.
3. Pilih repository tersebut. `Dockerfile` di root akan dipakai untuk build.
4. Beri nama service `shopee-bulk` jika ingin memakai `.railway/railway.ts` yang sudah disertakan.
5. Untuk menerapkan volume + healthcheck + 1 replica dari file IaC, di komputer jalankan:

```bash
npm install
railway login
railway link
railway config plan
railway config apply
```

6. Jika belum ada public domain, jalankan:

```bash
railway domain --service shopee-bulk
```

atau Generate Domain dari Railway dashboard.

> Railway tidak otomatis membaca `.railway/railway.ts` hanya karena file ada di repository. Konfigurasi Infrastructure as Code diterapkan dengan `railway config plan/apply`.

## Persistent storage

SQLite disimpan di:

```text
/data/app.db
```

Jika Railway Volume terpasang, backend juga otomatis mengenali variable Railway `RAILWAY_VOLUME_MOUNT_PATH`.

Jika service restart ketika job sedang jalan, item `processing/retrying` dikembalikan ke antrean dan job dipulihkan dalam status `paused`. Klik **Lanjutkan** untuk meneruskan.

## Healthcheck

Endpoint:

```text
GET /api/health
```

Selain mengecek web process, endpoint ini juga membuka SQLite dengan `SELECT 1`, sehingga healthcheck memastikan database bisa diakses.

Contoh response:

```json
{
  "ok": true,
  "service": "shopee-bulk",
  "jobStatus": "idle",
  "database": "app.db",
  "databasePath": "/data/app.db",
  "persistentVolume": true
}
```

## Jalankan lokal

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Buka `http://127.0.0.1:8000`.

Secara lokal database default berada di `./data/app.db`. Untuk mensimulasikan Railway:

```bash
DATA_DIR=/tmp/shopee-data PORT=8000 python app.py
```

## Catatan Shopee

Resolving shortlink melakukan request keluar dari server Railway ke Shopee. Jika Shopee melakukan rate-limit atau membatasi IP datacenter, kurangi concurrency dan gunakan delay `Normal`, `Aman`, atau `Lambat`. Respons HTTP 429 otomatis masuk jalur retry/cooldown.
