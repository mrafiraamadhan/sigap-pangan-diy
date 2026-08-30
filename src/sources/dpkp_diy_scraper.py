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
import io
import os
import time

import pandas as pd
import requests

BASE_URL = "https://dpkp.jogjaprov.go.id/harga-pangan/list"
# CATATAN (30 Agt 2026): sebelumnya di sini ada parameter `sort=-tanggal`,
# TERNYATA JUSTRU BIKIN SALAH -- dites manual (WebFetch): dengan parameter itu,
# "page=1" malah menampilkan baris 9.961-9.980 dari 10.035 (bukan yang
# terbaru!), sementara TANPA parameter sort sama sekali, "page=1" sudah benar
# menampilkan 20 baris data TERBARU (per 2026-08-13 saat dites). Jadi urutan
# default situs ini SUDAH kronologis terbaru->terlama, tidak perlu dipaksa.
HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "harga_pangan_dpkp_diy.csv")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SIGAP-Pangan-DIY/1.0; research bot - "
                  "YES2026 food security paper) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}
DELAY_SECONDS = 1.5  # jeda antar-request, jangan diturunkan


def fetch_page(page: int) -> pd.DataFrame | None:
    """Ambil & parse 1 halaman. Sengaja SANGAT defensif -- kalau ada yang
    aneh di 1 halaman (tabel berubah struktur, dsb), lewati halaman itu
    (return None) daripada bikin seluruh proses berhenti. Pesan error utama
    dipotong pendek supaya log Actions tidak kebanjiran dump HTML mentah,
    TAPI tetap menyertakan status HTTP + panjang respons + cuplikan singkat
    supaya kalau gagal lagi, penyebabnya (mis. situs memblokir bot) langsung
    ketahuan dari log tanpa perlu tebak-tebakan lagi."""
    url = f"{BASE_URL}?page={page}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Halaman {page}: GAGAL request ({str(e)[:200]})")
        return None

    try:
        # PENTING: pandas versi baru (3.x) TIDAK LAGI menerima string HTML
        # mentah langsung di read_html() -- harus dibungkus io.StringIO(),
        # kalau tidak akan salah dikira nama file & gagal dgn FileNotFoundError
        # yang isinya seluruh HTML (ini bug nyata yang bikin scraper ini
        # gagal total sebelumnya, BUKAN soal situs diblokir).
        tables = pd.read_html(io.StringIO(resp.text))
    except Exception as e:
        print(f"  Halaman {page}: gagal parse tabel -- {type(e).__name__}: {str(e)[:150]} "
              f"(HTTP {resp.status_code}, {len(resp.text)} char)")
        return None

    if not tables:
        cuplikan = resp.text[:150].replace("\n", " ")
        print(f"  Halaman {page}: tidak ada tabel di HTML (HTTP {resp.status_code}, "
              f"{len(resp.text)} char, cuplikan: {cuplikan!r})")
        return None

    df = tables[0]

    # NORMALISASI NAMA KOLOM -- WAJIB sebelum apapun. Run 30 Agt 2026 (365
    # halaman, 16 menit) GAGAL TOTAL di tahap pembersihan dengan IndexError
    # karena nama kolom hasil read_html tidak selalu string polos (bisa
    # tuple/MultiIndex/angka tergantung struktur header yang keparse), padahal
    # kode pembersihan mencari kolom dengan `"anggal" in c` yang diam-diam
    # tidak cocok pada kolom non-string. Setelah baris ini, SEMUA nama kolom
    # dijamin string rata & bersih spasi.
    if isinstance(df.columns, pd.MultiIndex):
        # dict.fromkeys = buang bagian yang berulang TAPI pertahankan urutan,
        # supaya header bertingkat ("Tanggal","Tanggal") jadi "Tanggal" polos
        # (bukan "Tanggal Tanggal" yang tidak nyambung dgn halaman lain).
        df.columns = [" ".join(dict.fromkeys(
                          str(bagian) for bagian in tup
                          if str(bagian) not in ("nan", "None", "")))
                       for tup in df.columns]
    df.columns = [str(c).strip() for c in df.columns]

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
              f"harapan (kolom: {list(df.columns)[:6]}) -- dilewati. "
              f"(HTTP {resp.status_code}, {len(resp.text)} char)")
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
    # PRINSIP BARU setelah insiden 30 Agt 2026 (16 menit fetch dibuang gara-gara
    # crash di tahap ini): pembersihan TIDAK BOLEH menggagalkan penyimpanan.
    # Kalau ada yang aneh, cetak diagnosis + simpan data mentah apa adanya --
    # data kotor yang tersimpan jauh lebih baik daripada data hilang total.
    tanggal_col = next((c for c in result.columns if "anggal" in str(c).lower()), None)
    harga_col = next((c for c in result.columns if "arga" in str(c).lower()), None)

    if tanggal_col and harga_col:
        try:
            before = len(result)
            result = result[~result[tanggal_col].astype(str).str.startswith("0000")]
            result[harga_col] = pd.to_numeric(result[harga_col], errors="coerce")
            result = result[result[harga_col] > 0]
            print(f"Pembersihan data: {before} baris -> {len(result)} baris "
                  f"(buang {before - len(result)} baris dgn tanggal tidak valid / harga = 0)")
        except Exception as e:
            print(f"PERINGATAN: pembersihan gagal ({type(e).__name__}: {str(e)[:150]}) "
                  f"-- data disimpan APA ADANYA tanpa dibersihkan.")
    else:
        print(f"PERINGATAN: kolom tanggal/harga tidak dikenali "
              f"(kolom yang ada: {list(result.columns)[:8]}) "
              f"-- data disimpan APA ADANYA tanpa dibersihkan.")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    # Simpan DENGAN DEDUP antar-run (versi lama pakai mode append mentah, jadi
    # run terjadwal harian akan menumpuk baris yang sama terus-menerus).
    # Identitas 1 observasi = tanggal + komoditas (situs mencatat 1 harga per
    # komoditas per hari); fallback ke semua kolom kalau kolom tak dikenali.
    if os.path.isfile(OUTPUT_PATH):
        lama = pd.read_csv(OUTPUT_PATH)
        lama.columns = [str(c).strip() for c in lama.columns]
        gabungan = pd.concat([lama, result], ignore_index=True)
        komoditas_col = next((c for c in gabungan.columns if "omoditas" in str(c).lower()), None)
        kunci = ([tanggal_col, komoditas_col] if (tanggal_col and komoditas_col
                  and tanggal_col in gabungan.columns and komoditas_col in gabungan.columns)
                 else list(gabungan.columns))
        gabungan = gabungan[~gabungan[kunci].astype(str).duplicated(keep="first")]
        baru = len(gabungan) - len(lama)
        gabungan.to_csv(OUTPUT_PATH, index=False)
    else:
        result.to_csv(OUTPUT_PATH, index=False)
        baru = len(result)

    print(f"\n{baru} baris baru ditambahkan ke {OUTPUT_PATH} "
          f"(dari {len(result)} baris hasil fetch).")
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
