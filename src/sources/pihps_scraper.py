"""
Scraper harga pangan harian DIY dari PIHPS Bank Indonesia.

PENTING: situs ini memuat data lewat AJAX dan (sejauh penelusuran kami)
belum punya API publik terdokumentasi. Ada 2 mode di script ini:

1) --inspect  : buka browser (headed) supaya kamu bisa pilih filter secara
                manual sekali, sambil script mencatat semua request XHR/fetch
                yang lewat -- dari situ kita cari tahu apakah ada endpoint
                JSON asli yang bisa dipanggil langsung (jauh lebih stabil).
2) (default)  : mode otomatis -- scrape tabel HTML hasil filter untuk semua
                kab/kota DIY, simpan ke data/harga_pangan_diy.csv.

Kalau kamu menemukan endpoint JSON lewat mode --inspect, isi DATA_ENDPOINT
di bawah ini dan pakai fungsi fetch_via_api() supaya jauh lebih cepat &
stabil daripada scraping tabel HTML tiap hari.
"""

import argparse
import csv
import os
from datetime import datetime, timezone

import pandas as pd
from playwright.sync_api import sync_playwright

URL = "https://www.bi.go.id/hargapangan/TabelHarga/ProdusenDaerah"

# Isi ini kalau endpoint JSON asli sudah ditemukan lewat mode --inspect, mis:
# DATA_ENDPOINT = "https://www.bi.go.id/hargapangan/api/GetHargaProdusenDaerah"
DATA_ENDPOINT = None

DAERAH_DIY = [
    "Kota Yogyakarta",
    "Kabupaten Sleman",
    "Kabupaten Bantul",
    "Kabupaten Kulon Progo",
    "Kabupaten Gunung Kidul",
]

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "harga_pangan_diy.csv")


def inspect_mode():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250)
        page = browser.new_page()

        found = []

        def on_request(req):
            if req.resource_type in ("xhr", "fetch"):
                found.append((req.method, req.url))
                print(">>", req.method, req.url)

        page.on("request", on_request)
        page.goto(URL, wait_until="networkidle", timeout=60000)

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


def scrape_html_table(page, daerah: str) -> pd.DataFrame:
    """Otomasi filter di UI + scrape tabel HTML hasil.
    Selector di bawah PERKIRAAN berdasarkan pola umum ASP.NET MVC -- sesuaikan
    setelah menjalankan --inspect kalau selector-nya beda."""
    page.goto(URL, wait_until="networkidle", timeout=60000)

    # TODO: sesuaikan selector setelah inspeksi manual, contoh:
    # page.select_option("#Propinsi", label="DI Yogyakarta")
    # page.wait_for_timeout(1000)
    # page.select_option("#Kabupaten", label=daerah)
    # page.click("text=Lihat Laporan")
    # page.wait_for_selector("table.table-data", timeout=30000)
    # html = page.inner_html("table.table-data")
    # tables = pd.read_html(html)
    # df = tables[0]
    # df["kabupaten_kota"] = daerah
    # df["diambil_pada_utc"] = datetime.now(timezone.utc).isoformat()
    # return df

    raise NotImplementedError(
        "Selector belum dikonfirmasi. Jalankan --inspect dulu, lalu isi "
        "bagian yang di-comment di atas sesuai struktur halaman yang kamu lihat."
    )


def automated_mode():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for daerah in DAERAH_DIY:
            print(f"Scraping: {daerah}")
            try:
                df = scrape_html_table(page, daerah)
                all_rows.append(df)
            except NotImplementedError as e:
                print(f"  -> {e}")
                break
            except Exception as e:
                print(f"  -> GAGAL untuk {daerah}: {e}")
        browser.close()

    if not all_rows:
        print("\nTidak ada data yang berhasil di-scrape. Lihat pesan di atas.")
        return

    result = pd.concat(all_rows, ignore_index=True)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    file_exists = os.path.isfile(OUTPUT_PATH)
    result.to_csv(OUTPUT_PATH, mode="a", header=not file_exists, index=False)
    print(f"\n{len(result)} baris ditambahkan ke {OUTPUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true",
                         help="Buka browser untuk eksplorasi manual & temukan endpoint asli")
    args = parser.parse_args()

    if args.inspect:
        inspect_mode()
    else:
        automated_mode()
