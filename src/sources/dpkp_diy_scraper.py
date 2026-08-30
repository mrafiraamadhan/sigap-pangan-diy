"""
Scraper harga pangan harian DIY dari Dinas Pertanian dan Ketahanan Pangan
(DPKP) Provinsi DIY -- SUMBER TERBAIK yang ditemukan sejauh ini karena:

  1. Langsung dari instansi "Ketahanan Pangan" DIY (paling on-theme utk YES 2026)
  2. HTML STATIS, tabel biasa, TIDAK perlu Playwright/browser automation sama
     sekali -- cukup `requests` + `pandas.read_html`
  3. Sudah tercatat ~502 halaman x 20 komoditas/hari = ~1,4 tahun histori harian

URL: https://dpkp.jogjaprov.go.id/harga-pangan/list?page=N
Tiap halaman = 20 baris = 1 tanggal (20 komoditas dicatat per hari).
Sorting default tampak dari tanggal terbaru ke terlama, jadi:
    page 1   -> tanggal paling baru
    page 502 -> tanggal paling lama yang tersedia

Cara pakai:
    pip install requests pandas lxml
    python dpkp_diy_scraper.py                 # ambil SEMUA halaman (~502)
    python dpkp_diy_scraper.py --pages 1-30     # ambil 30 hari terbaru saja
    python dpkp_diy_scraper.py --pages 480-502  # ambil histori paling lama

Catatan sopan-santun scraping: script ini kasih delay antar-request supaya
tidak membebani server DPKP DIY. Jangan hapus/percepat delay-nya.
"""

import argparse
import os
import time

import pandas as pd
import requests

BASE_URL = "https://dpkp.jogjaprov.go.id/harga-pangan/list"
SORT_PARAM = "-tanggal"  # penting: tanpa ini urutan antar-halaman TIDAK kronologis
HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "harga_pangan_dpkp_diy.csv")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (research bot - YES2026 food security paper; "
                  "kontak: isi-email-kamu-di-sini) AppleWebKit/537.36"
}
DELAY_SECONDS = 1.5  # jeda antar-request, jangan diturunkan


def fetch_page(page: int) -> pd.DataFrame | None:
    url = f"{BASE_URL}?sort={SORT_PARAM}&page={page}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Halaman {page}: GAGAL request ({e})")
        return None

    try:
        tables = pd.read_html(resp.text)
    except ValueError:
        print(f"  Halaman {page}: tidak ada tabel ditemukan (mungkin format berubah)")
        return None

    if not tables:
        return None

    df = tables[0]
    # Buang kolom nomor urut kalau ada (biasanya kolom pertama '#')
    if df.columns[0] in ("#", "No", "No."):
        df = df.drop(columns=[df.columns[0]])
    return df


def parse_page_range(spec: str):
    if "-" in spec:
        a, b = spec.split("-")
        return range(int(a), int(b) + 1)
    return range(1, int(spec) + 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", default="1-502",
                         help="Range halaman, mis. '1-30' atau '480-502'. Default: semua (1-502)")
    args = parser.parse_args()

    page_range = parse_page_range(args.pages)
    all_frames = []

    for page in page_range:
        print(f"Mengambil halaman {page}...")
        df = fetch_page(page)
        if df is not None and not df.empty:
            all_frames.append(df)
        time.sleep(DELAY_SECONDS)

    if not all_frames:
        print("Tidak ada data berhasil diambil.")
        return

    result = pd.concat(all_frames, ignore_index=True).drop_duplicates()

    # --- PEMBERSIHAN DATA (penting -- database sumbernya punya masalah kualitas
    # data nyata: sebagian tanggal '0000-00-00' / kosong, sebagian harga = 0) ---
    before = len(result)
    tanggal_col = [c for c in result.columns if "anggal" in c][0]
    harga_col = [c for c in result.columns if "arga" in c][0]

    result = result[~result[tanggal_col].astype(str).str.startswith("0000")]
    result[harga_col] = pd.to_numeric(result[harga_col], errors="coerce")
    result = result[result[harga_col] > 0]
    after = len(result)
    print(f"Pembersihan data: {before} baris -> {after} baris "
          f"(buang {before - after} baris dengan tanggal tidak valid / harga = 0)")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    file_exists = os.path.isfile(OUTPUT_PATH)
    result.to_csv(OUTPUT_PATH, mode="a", header=not file_exists, index=False)
    print(f"\n{len(result)} baris ditambahkan ke {OUTPUT_PATH}")
    print("Sumber: Dinas Pertanian dan Ketahanan Pangan (DPKP) Provinsi DIY -- "
          "https://dpkp.jogjaprov.go.id/harga-pangan/list")


if __name__ == "__main__":
    main()
