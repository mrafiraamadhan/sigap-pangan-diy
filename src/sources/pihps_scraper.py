"""
Scraper harga pangan harian DIY dari PIHPS Bank Indonesia -- endpoint API ASLI.

Endpoint & parameter di bawah ini ditemukan lewat mode --inspect (Playwright,
network capture manual di browser sungguhan pada 30 Agt 2026). province_id=15
sudah dikonfirmasi = DI Yogyakarta lewat endpoint GetRefProvince.

Contoh URL asli yang terbukti jalan (hasil klik "DI Yogyakarta" di UI):
https://www.bi.go.id/hargapangan/WebSite/TabelHarga/GetGridDataDaerah?
  price_type_id=4&comcat_id=&province_id=15&regency_id=&market_id=&
  tipe_laporan=1&start_date=2026-08-22&end_date=2026-08-30&_=<timestamp>

Struktur respons JSON (dikonfirmasi via fetch langsung):
{
  "data": [
    {"no": "I", "name": "Beras", "level": 1, "24/08/2026": "14,100", ...},
    {"no": 1, "name": "Beras Kualitas Bawah I", "level": 2, "24/08/2026": "12,750", ...},
    ...
  ]
}
- Kolom tanggal bersifat DINAMIS (key = "DD/MM/YYYY"), jumlahnya mengikuti
  rentang start_date..end_date yang diminta (dan dibatasi data yang memang
  sudah tersedia -- biasanya ada jeda 1-2 hari dari tanggal hari ini).
- "level": 1 = baris kelompok komoditas (mis. "Beras", "Daging Ayam"),
  "level": 2 = rincian per kualitas/jenis di bawah kelompok itu. Keduanya
  kita simpan apa adanya -- untuk deteksi anomali, nama komoditas yang beda
  ya dianggap deret data yang beda, tidak masalah kalau ada baris ringkasan
  & baris rincian sekaligus.
- Harga berformat string dengan pemisah ribuan tanda koma, mis. "14,100"
  artinya Rp 14.100 (bukan 14,1 -- ini format en-US bawaan ASP.NET grid,
  bukan format Indonesia).

Cakupan data: dengan regency_id dikosongkan, endpoint ini mengembalikan data
TINGKAT PROVINSI DI Yogyakarta (bukan pecahan per kabupaten/kota). Ini sudah
sesuai dengan sifat halaman "Produsen Daerah" PIHPS & tetap sah dipakai
sebagai pilar data tingkat provinsi untuk sistem peringatan dini.

Kalau suatu saat endpoint ini berubah/berhenti jalan, jalankan lagi:
    python3 src/sources/pihps_scraper.py --inspect
lalu ulangi proses inspeksi manual (pilih provinsi di browser yang terbuka,
lihat request baru yang tercetak di terminal).
"""

import argparse
import csv
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from playwright.sync_api import sync_playwright

INSPECT_URL = "https://www.bi.go.id/hargapangan/TabelHarga/ProdusenDaerah"
GRID_ENDPOINT = "https://www.bi.go.id/hargapangan/WebSite/TabelHarga/GetGridDataDaerah"

PROVINCE_ID_DIY = 15
PRICE_TYPE_ID = 4     # sesuai hasil inspeksi (mode "Produsen")
TIPE_LAPORAN = 1
WINDOW_DAYS = 14       # minta 14 hari terakhir tiap run -- kalau ada run yang
                       # gagal/lewat, hari yang kelewat tetap ke-cover & di-dedup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SIGAP-Pangan-DIY/1.0; "
                  "+https://github.com/mrafiraamadhan/sigap-pangan-diy)",
    "Referer": INSPECT_URL,
    "X-Requested-With": "XMLHttpRequest",
}

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "harga_pangan_diy.csv")
FIELDNAMES = ["tanggal", "komoditas", "level", "no_urut", "harga_rp",
              "provinsi", "diambil_pada_utc"]


def inspect_mode():
    """Mode diagnostik manual -- pakai ini lagi kalau endpoint di atas suatu
    saat berhenti jalan (situs berubah struktur)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250)
        page = browser.new_page()

        found = []

        def on_request(req):
            if req.resource_type in ("xhr", "fetch"):
                found.append((req.method, req.url))
                print(">>", req.method, req.url)

        page.on("request", on_request)
        page.goto(INSPECT_URL, wait_until="networkidle", timeout=60000)

        print("\n=== MODE INSPEKSI ===")
        print("1. Di jendela browser yang terbuka, pilih Provinsi = DI Yogyakarta")
        print("2. Pilih salah satu kabupaten/kota & rentang tanggal")
        print("3. Klik 'Lihat Laporan' atau tombol serupa")
        print("4. Perhatikan request XHR/fetch yang tercetak di terminal ini --")
        print("   itu kandidat endpoint data asli.")
        print("\nTekan Enter di sini setelah selesai eksplorasi...")
        input()

        print(f"\nTotal {len(found)} request XHR/fetch tercatat selama sesi ini.")
        browser.close()


def ambil_data():
    """Panggil endpoint JSON asli PIHPS langsung (tanpa browser), ambil data
    harga pangan DI Yogyakarta beberapa hari terakhir."""
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=WINDOW_DAYS)

    params = {
        "price_type_id": PRICE_TYPE_ID,
        "comcat_id": "",
        "province_id": PROVINCE_ID_DIY,
        "regency_id": "",
        "market_id": "",
        "tipe_laporan": TIPE_LAPORAN,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "_": int(time.time() * 1000),
    }

    resp = requests.get(GRID_ENDPOINT, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("data", [])

    hasil = []
    diambil_pada = datetime.now(timezone.utc).isoformat()

    for row in rows:
        nama_komoditas = row.get("name")
        level = row.get("level")
        no_urut = row.get("no")

        for key, value in row.items():
            if key in ("no", "name", "level"):
                continue
            try:
                tanggal = datetime.strptime(key, "%d/%m/%Y").date().isoformat()
            except ValueError:
                # kolom bukan tanggal (jaga-jaga kalau ada field lain di masa depan)
                continue

            if value in (None, "", "-"):
                continue
            try:
                harga = float(str(value).replace(",", "").strip())
            except ValueError:
                continue

            hasil.append({
                "tanggal": tanggal,
                "komoditas": nama_komoditas,
                "level": level,
                "no_urut": no_urut,
                "harga_rp": harga,
                "provinsi": "DI Yogyakarta",
                "diambil_pada_utc": diambil_pada,
            })

    return hasil


def append_csv(rows):
    if not rows:
        print("Tidak ada baris untuk disimpan (respons API kosong).")
        return

    existing_keys = set()
    file_exists = os.path.isfile(OUTPUT_PATH)
    if file_exists:
        with open(OUTPUT_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                existing_keys.add((r["tanggal"], r["komoditas"], str(r["no_urut"])))

    baru = [r for r in rows
            if (r["tanggal"], r["komoditas"], str(r["no_urut"])) not in existing_keys]

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        for r in baru:
            writer.writerow(r)

    print(f"{len(baru)} baris baru ditambahkan ke {OUTPUT_PATH} "
          f"(dari {len(rows)} baris hasil fetch, {len(rows) - len(baru)} sudah ada sebelumnya).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true",
                         help="Mode diagnostik manual (buka browser) -- pakai kalau "
                              "endpoint API berhenti jalan & perlu dicek ulang")
    args = parser.parse_args()

    if args.inspect:
        inspect_mode()
    else:
        data = ambil_data()
        append_csv(data)
