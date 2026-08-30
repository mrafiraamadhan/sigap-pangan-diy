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
    """Ambil & parse 1 halaman. Sengaja SANGAT defensif -- kalau ada yang
    aneh di 1 halaman (tabel berubah struktur, dsb), lewati halaman itu
    (return None) daripada bikin seluruh proses berhenti. Semua pesan error
    dipotong pendek supaya log Actions tidak kebanjiran dump HTML mentah."""
    url = f"{BASE_URL}?sort={SORT_PARAM}&page={page}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Halaman {page}: GAGAL request ({str(e)[:200]})")
        return None

    try:
        tables = pd.read_html(resp.text)
    except Exception as e:
        print(f"  Halaman {page}: gagal parse tabel -- {type(e).__name__}: {str(e)[:200]}")
        return None

    if not tables:
        print(f"  Halaman {page}: tidak ada tabel ditemukan di HTML.")
        return None

    df = tables[0]
    # Buang kolom nomor urut kalau ada (biasanya kolom pertama '#')
    if len(df.columns) and df.columns[0] in ("#", "No", "No."):
        df = df.drop(columns=[df.columns[0]])

    # Validasi minimal: tabel harus punya kolom tanggal & harga yang bisa
    # dikenali, kalau tidak berarti ini BUKAN tabel data (mis. tabel
    # navigasi/kalender yang ikut keparse) -- lewati saja.
    ada_kolom_tanggal = any("anggal" in str(c) for c in df.columns)
    ada_kolom_harga = any("arga" in str(c) for c in df.columns)
    if not (ada_kolom_tanggal and ada_kolom_harga):
        print(f"  Halaman {page}: tabel ditemukan tapi kolomnya tidak sesuai "
              f"harapan (kolom: {list(df.columns)[:6]}) -- dilewati.")
        return None

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
    try:
        main()
    except Exception as e:
        # Jaring pengaman terakhir: apapun yang gagal, cetak ringkas saja
        # (bukan traceback penuh/dump HTML) supaya log Actions tetap terbaca.
        print(f"GAGAL total: {type(e).__name__}: {str(e)[:300]}")
        raise SystemExit(1)
