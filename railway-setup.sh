#!/usr/bin/env bash
set -euo pipefail

if ! command -v railway >/dev/null 2>&1; then
  echo "Railway CLI belum terpasang. Install: npm i -g @railway/cli"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js/npm dibutuhkan hanya untuk menjalankan Railway IaC (.railway/railway.ts)."
  exit 1
fi

if ! railway whoami >/dev/null 2>&1; then
  echo "Login Railway dulu: railway login"
  exit 1
fi

npm install

echo "Pilih/link project Railway ketika diminta..."
railway link

echo "Preview perubahan Railway..."
railway config plan

echo "Apply: volume /data + 1 replica + healthcheck..."
railway config apply

echo "Deploy aplikasi..."
railway up --service shopee-bulk --detach

echo "Generate domain publik..."
railway domain --service shopee-bulk || true

echo "Selesai. Cek Railway dashboard/logs jika domain tidak tercetak otomatis."
