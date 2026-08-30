"""
Ambil data Cadangan Pangan Pemerintah DIY (stok penyangga per komoditas
per kabupaten/kota), sumber: Dinas Pertanian dan Ketahanan Pangan DIY.

URL: https://cadanganpangan.jogjaprov.go.id/site/index?y=<tahun>
Halaman statis HTML. Catatan: parameter `?y=` tampaknya tidak selalu
mengganti tahun yang ditampilkan pada pengujian awal (kemungkinan filter
tahun butuh interaksi JS) -- verifikasi manual perlu dilakukan sebelum
mengandalkan otomasi penuh untuk data historis multi-tahun.

Data ini penting untuk menilai kapasitas buffer stock DIY, bukan cuma
harga/cuaca -- relevan langsung untuk narasi "ketahanan pangan".

Cara pakai:
    pip install requests pandas lxml
    python cadangan_pangan_scraper.py --tahun 2026
"""

import argparse
import os
from datetime import datetime, timezone

import pandas as pd
import requests

BASE_URL = "https://cadanganpangan.jogjaprov.go.id/site/index"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "cadangan_pangan_diy.csv")

HEADERS = {"User-Agent": "Mozilla/5.0 (research bot - YES2026 food security paper)"}


def fetch_tahun(tahun: int) -> list:
    try:
        resp = requests.get(BASE_URL, params={"y": tahun}, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"Tahun {tahun}: GAGAL request ({str(e)[:200]})")
        return []
    try:
        tables = pd.read_html(resp.text)
    except Exception as e:
        print(f"Tahun {tahun}: gagal parse tabel -- {type(e).__name__}: {str(e)[:200]}")
        return []
    frames = []
    for i, df in enumerate(tables):
        if df.empty or len(df.columns) < 2:
            continue  # lewati tabel kosong/tabel layout (bukan tabel data)
        df = df.copy()
        df["tahun_filter"] = tahun
        df["diambil_pada_utc"] = datetime.now(timezone.utc).isoformat()
        frames.append(df)
    if not frames:
        print(f"Tahun {tahun}: tabel ditemukan tapi tidak ada yang terlihat seperti data asli.")
    return frames


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tahun", type=int, default=datetime.now().year)
    args = parser.parse_args()

    print(f"Mengambil data cadangan pangan tahun {args.tahun}...")
    frames = fetch_tahun(args.tahun)
    if not frames:
        print("Tidak ada data.")
        return

    result = pd.concat(frames, ignore_index=True)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    file_exists = os.path.isfile(OUTPUT_PATH)
    result.to_csv(OUTPUT_PATH, mode="a", header=not file_exists, index=False)
    print(f"{len(result)} baris ditambahkan ke {OUTPUT_PATH}")
    print("PENTING: cek manual apakah --tahun benar-benar mengubah data yang "
          "diambil (lihat catatan di docstring file ini) sebelum dipakai untuk "
          "analisis tren multi-tahun.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"GAGAL total: {type(e).__name__}: {str(e)[:300]}")
        raise SystemExit(1)
