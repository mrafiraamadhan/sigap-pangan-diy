"""
Pembaruan data dari LAPTOP SENDIRI -- untuk sumber yang tidak bisa ditarik
dari server (GitHub Actions maupun sandbox cloud):

  * DPKP DIY        dpkp.jogjaprov.go.id         (berhenti di 11 Sep 2026)
  * SIMOTANDI       sig02.pertanian.go.id         (berhenti di periode 5 Agu 2026)
  * SP2KP Kemendag  api-sp2kp.kemendag.go.id      (berhenti di 28 Agu 2026)
  * Kliping berita  Google News / Bing RSS        (berhenti di 26 Agu 2026, kredit Firecrawl habis)

Skrip ini menjalankan penarik-penarik itu dari koneksi rumah/kampus (yang
tidak diblokir), lalu langkah pipeline lanjutannya, lalu menyalin hasil ke
docs/data/ persis seperti yang dilakukan workflow di GitHub. Setelah selesai,
unggah berkas yang berubah (daftarnya dicetak di akhir) ke GitHub, atau:

    git add data docs/data && git commit -m "data: pembaruan lokal" && git push

Cara pakai (dari folder repo, sekali saja siapkan: pip install -r requirements.txt):

    python update_lokal.py                  # semua langkah
    python update_lokal.py --hanya dpkp simotandi     # sebagian saja
    python update_lokal.py --hanya berita --maks-berita 200   # isi arsip kliping lebih banyak

Tiap langkah yang gagal TIDAK menghentikan langkah lain (sama seperti di
workflow); ringkasan sukses/gagal dicetak di akhir.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

LANGKAH = [
    # (kunci, keterangan, perintah)
    ("dpkp",      "Harga DPKP DIY (30 halaman = 30 hari terakhir)",
                  [PY, "src/sources/dpkp_diy_scraper.py", "--pages", "1-30"]),
    ("simotandi", "Fase tanam padi SIMOTANDI (ArcGIS Kementan)",
                  [PY, "src/sources/simotandi.py"]),
    ("sp2kp",     "Harga pasar SP2KP Kemendag (45 hari terakhir)",
                  [PY, "src/sources/sp2kp_kemendag.py", "--hari-mundur", "45"]),
    ("ringkas",   "Ringkas SP2KP untuk papan pantau",
                  [PY, "src/pipeline/ringkas_sp2kp.py"]),
    ("gabung",    "Gabungkan & deteksi anomali",
                  [PY, "src/pipeline/merge_and_detect.py"]),
    ("prakiraan", "Prakiraan harga & risiko lonjakan",
                  [PY, "src/pipeline/forecast_harga.py"]),
    ("berita",    "Kliping berita untuk lonjakan yang belum dicarikan beritanya",
                  [PY, "src/pipeline/news_validation.py"]),
]


def cuplik_tanggal(path):
    """Tanggal terbesar di kolom tanggal/periode, untuk ringkasan akhir."""
    try:
        import pandas as pd
        d = pd.read_csv(path, nrows=200000, low_memory=False)
        for c in d.columns:
            if str(c).lower().startswith(("tanggal", "periode_mulai", "date")):
                v = d[c].dropna().astype(str)
                if len(v):
                    return f"s.d. {v.max()[:10]}"
    except Exception:
        pass
    return ""


def main():
    ap = argparse.ArgumentParser(description="Pembaruan data SIGAP Pangan DIY dari laptop sendiri")
    ap.add_argument("--hanya", nargs="+", choices=[k for k, _, _ in LANGKAH],
                    help="jalankan langkah tertentu saja")
    ap.add_argument("--maks-berita", type=int, default=60,
                    help="maksimal pencarian berita dalam sekali jalan (default 60)")
    args = ap.parse_args()

    os.chdir(HERE)
    sebelum = {f: os.path.getmtime(f) for f in glob.glob("data/*.csv")}

    hasil = []
    for kunci, ket, cmd in LANGKAH:
        if args.hanya and kunci not in args.hanya:
            continue
        if kunci == "berita":
            cmd = cmd + ["--maks", str(args.maks_berita)]
        print(f"\n===== {ket} =====", flush=True)
        t0 = time.time()
        try:
            rc = subprocess.call(cmd)
        except KeyboardInterrupt:
            print("dihentikan pengguna"); rc = 130
        hasil.append((ket, rc == 0, time.time() - t0))

    # Salin ke docs/data seperti langkah "Commit hasil" di workflow:
    # semua CSV kecuali SP2KP mentah (terlalu besar untuk peramban), plus geojson.
    os.makedirs("docs/data", exist_ok=True)
    for f in glob.glob("data/*.csv"):
        if os.path.basename(f) != "harga_sp2kp_diy.csv":
            shutil.copy2(f, "docs/data/")
    for f in glob.glob("data/*.geojson"):
        shutil.copy2(f, "docs/data/")

    print("\n===== RINGKASAN =====")
    for ket, ok, dur in hasil:
        print(f"  {'OK    ' if ok else 'GAGAL '} {ket}  ({dur:.0f} dtk)")
    berubah = [f for f in glob.glob("data/*.csv") if os.path.getmtime(f) != sebelum.get(f)]
    if berubah:
        print("\nBerkas yang berubah (unggah ini, beserta salinannya di docs/data/):")
        for f in sorted(berubah):
            print(f"  {f}  {cuplik_tanggal(f)}")
        print("  (harga_sp2kp_diy.csv hanya di data/, versi ringkasnya di docs/data/)")
    else:
        print("\nTidak ada berkas data yang berubah.")
    print("\nLangkah berikutnya: git add data docs/data && git commit -m \"data: pembaruan lokal\" && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
