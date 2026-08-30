"""
Ambil data fase pertanaman padi DIY dari SIMOTANDI (Kementerian Pertanian),
sumber: citra satelit Sentinel-1, update per dasarian (~10-12 hari).

Situs: https://simotandi.pertanian.go.id/front-dashboard
Halaman data: https://simotandi.pertanian.go.id/data-tabular (tombol "Export Excel"
mengarah ke https://simotandi.pertanian.go.id/front/data-tabular/export)

Sama seperti PIHPS, kita belum tahu persis parameter query yang dibutuhkan
endpoint export (periode/provinsi/kabupaten) tanpa mengeksplorasi UI-nya
langsung dengan koneksi internet asli. Jalankan --inspect dulu.
"""

import argparse
import os
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

URL = "https://simotandi.pertanian.go.id/data-tabular"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(HERE, "..", "..", "data")


def inspect_mode():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250)
        page = browser.new_page()

        def on_request(req):
            if req.resource_type in ("xhr", "fetch", "document") and "export" in req.url.lower():
                print(">> EXPORT REQUEST:", req.method, req.url)

        page.on("request", on_request)
        page.goto(URL, wait_until="networkidle", timeout=60000)

        print("\n=== MODE INSPEKSI SIMOTANDI ===")
        print("1. Di jendela browser, filter Provinsi = DI Yogyakarta")
        print("2. Pilih kabupaten/kota satu per satu (atau semua kalau bisa)")
        print("3. Klik tombol 'Export Excel'")
        print("4. Catat URL yang tercetak di terminal -- itu pattern query")
        print("   yang perlu ditiru di download_export() di bawah.\n")
        print("Tekan Enter setelah selesai...")
        input()

        browser.close()


def download_export(page, provinsi="DI Yogyakarta", kabupaten=None, periode=None):
    """TODO: isi setelah tahu pattern URL dari --inspect. Contoh pola umum:

    export_url = (
        f"https://simotandi.pertanian.go.id/front/data-tabular/export"
        f"?provinsi={provinsi}&kabupaten={kabupaten}&periode={periode}"
    )
    with page.expect_download() as dl_info:
        page.goto(export_url)
    download = dl_info.value
    fname = f"simotandi_{kabupaten}_{periode}.xlsx".replace(" ", "_")
    path = os.path.join(OUTPUT_DIR, "simotandi_raw", fname)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    download.save_as(path)
    return path
    """
    raise NotImplementedError(
        "Pattern URL export belum dikonfirmasi. Jalankan --inspect dulu."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()

    if args.inspect:
        inspect_mode()
    else:
        print("Isi dulu download_export() berdasarkan hasil --inspect.")
