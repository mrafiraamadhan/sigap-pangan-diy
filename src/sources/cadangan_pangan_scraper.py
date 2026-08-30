"""
Ambil data Cadangan Pangan Pemerintah DIY (stok penyangga per komoditas
per kabupaten/kota), sumber: Dinas Pertanian dan Ketahanan Pangan DIY.

URL: https://cadanganpangan.jogjaprov.go.id/site/index?y=<tahun>
Halaman statis HTML. DIKONFIRMASI 30 Agt 2026 (lewat pengecekan langsung):
parameter `?y=` BEKERJA dengan benar (mengganti tahun yang ditampilkan), dan
selector tahun di halaman menawarkan opsi 2019-2026. Data di sini adalah
SNAPSHOT TAHUNAN (bukan deret harian) per wilayah & per komoditas -- jadi
"histori" untuk sumber ini artinya mengumpulkan ke-8 snapshot tahun itu
(pakai --semua-tahun), bukan rentang tanggal harian.

Data ini penting untuk menilai kapasitas buffer stock DIY, bukan cuma
harga/cuaca -- relevan langsung untuk narasi "ketahanan pangan".

Cara pakai:
    pip install requests pandas lxml
    python cadangan_pangan_scraper.py --tahun 2026       # 1 tahun saja
    python cadangan_pangan_scraper.py --semua-tahun      # semua tahun 2019-2026
"""

import argparse
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests

BASE_URL = "https://cadanganpangan.jogjaprov.go.id/site/index"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "cadangan_pangan_diy.csv")

# Dikonfirmasi 30 Agt 2026 lewat WebFetch: selector tahun di halaman ini
# menawarkan opsi 2019-2026 (ini snapshot TAHUNAN per wilayah/komoditas,
# BUKAN deret harian) -- jadi "histori panjang" di sini artinya ambil semua
# 8 tahun yang tersedia, bukan rentang tanggal.
TAHUN_TERSEDIA_MULAI = 2019

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SIGAP-Pangan-DIY/1.0; research bot - "
                  "YES2026 food security paper) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}


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
        print(f"Tahun {tahun}: gagal parse tabel -- {type(e).__name__}: {str(e)[:150]} "
              f"(HTTP {resp.status_code}, {len(resp.text)} char)")
        return []
    if not tables:
        cuplikan = resp.text[:150].replace("\n", " ")
        print(f"Tahun {tahun}: tidak ada tabel di HTML (HTTP {resp.status_code}, "
              f"{len(resp.text)} char, cuplikan: {cuplikan!r})")
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


def simpan_hasil(frames):
    """Simpan hasil fetch ke CSV DENGAN DEDUP -- versi lama menambahkan baris
    baru mentah-mentah tiap run (mode='a' tanpa cek duplikat), padahal script
    ini dipanggil berkali-kali sehari oleh pipeline terjadwal, jadi tahun yang
    sama akan diambil ulang & baris lama dobel terus tiap run kalau tidak
    di-dedup. Bandingkan sebagai string dulu (kecuali kolom timestamp
    pengambilan) supaya baris yang datanya identik tapi beda waktu ambil tetap
    dianggap sama."""
    if not frames:
        print("Tidak ada data untuk disimpan.")
        return

    result = pd.concat(frames, ignore_index=True)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    file_exists = os.path.isfile(OUTPUT_PATH)

    if file_exists:
        existing = pd.read_csv(OUTPUT_PATH)
        gabungan = pd.concat([existing, result], ignore_index=True)
        kolom_pembanding = [c for c in gabungan.columns if c != "diambil_pada_utc"]
        kunci_dup = gabungan[kolom_pembanding].astype(str).duplicated(keep="first")
        gabungan = gabungan[~kunci_dup]
        baru = len(gabungan) - len(existing)
        gabungan.to_csv(OUTPUT_PATH, index=False)
    else:
        result.to_csv(OUTPUT_PATH, index=False)
        baru = len(result)

    print(f"{baru} baris baru ditambahkan ke {OUTPUT_PATH} "
          f"(dari {len(result)} baris hasil fetch).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tahun", type=int, default=datetime.now().year)
    parser.add_argument("--semua-tahun", action="store_true",
                         help=f"Ambil semua tahun yang tersedia ({TAHUN_TERSEDIA_MULAI}-tahun "
                              "sekarang) sekaligus, bukan cuma 1 tahun.")
    args = parser.parse_args()

    daftar_tahun = (list(range(TAHUN_TERSEDIA_MULAI, datetime.now().year + 1))
                     if args.semua_tahun else [args.tahun])

    semua_frame = []
    for tahun in daftar_tahun:
        print(f"Mengambil data cadangan pangan tahun {tahun}...")
        semua_frame.extend(fetch_tahun(tahun))
        if len(daftar_tahun) > 1:
            time.sleep(1)  # jeda sopan antar-tahun

    simpan_hasil(semua_frame)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"GAGAL total: {type(e).__name__}: {str(e)[:300]}")
        raise SystemExit(1)
