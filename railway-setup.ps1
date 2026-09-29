$ErrorActionPreference = "Stop"

if (-not (Get-Command railway -ErrorAction SilentlyContinue)) {
    Write-Host "Railway CLI belum terpasang. Jalankan: npm i -g @railway/cli"
    exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "Node.js/npm dibutuhkan untuk Railway IaC (.railway/railway.ts)."
    exit 1
}

try { railway whoami | Out-Null } catch {
    Write-Host "Login Railway dulu: railway login"
    exit 1
}

npm install
Write-Host "Pilih/link project Railway ketika diminta..."
railway link

Write-Host "Preview perubahan Railway..."
railway config plan

Write-Host "Apply: volume /data + 1 replica + healthcheck..."
railway config apply

Write-Host "Deploy aplikasi..."
railway up --service shopee-bulk --detach

Write-Host "Generate domain publik..."
try { railway domain --service shopee-bulk } catch {}

Write-Host "Selesai. Cek Railway dashboard/logs jika domain tidak tercetak otomatis."
