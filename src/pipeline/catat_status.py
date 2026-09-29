"""
Catatan status tiap langkah pipeline, ditulis di akhir run ke
data/status_pipeline.csv (ikut disalin ke docs/data/ oleh langkah "Commit hasil").

Kenapa perlu: papan pantau menampilkan "terakhir diambil" dari kolom jam ambil di
tiap berkas. Tetapi bila sebuah sumber TIDAK punya baris baru (mis. DPKP yang
berhenti di 11 September), skripnya tidak menulis apa pun, sehingga tidak ada
jejak bahwa sumber itu sebenarnya sudah dicek hari ini. Berkas status ini
mencatat: kapan pipeline terakhir mengecek tiap sumber, berapa baris berkasnya,
dan apakah berkasnya berubah pada run ini (dibandingkan dengan commit sebelumnya).

Dijalankan tanpa argumen dari root repo, SETELAH semua langkah lain.
"""

import csv
import os
import subprocess
import sys
from datetime import datetime, timezone

DATA = "data"
KELUARAN = os.path.join(DATA, "status_pipeline.csv")

# (pilar, berkas, otomatis?) -- SP2KP & SIMOTANDI dicoba pipeline tetapi sumbernya
# menolak akses otomatis, jadi "dicek" tidak berarti apa-apa bagi keduanya.
PILAR = [
    ("PIHPS Bank Indonesia", "harga_pangan_diy.csv", True),
    ("SP2KP Kemendag", "harga_sp2kp_diy.csv", False),
    ("DPKP DIY", "harga_pangan_dpkp_diy.csv", True),
    ("SIMOTANDI Kementan", "simotandi_fase_tanam_diy.csv", False),
    ("Cadangan Pangan DIY", "cadangan_pangan_diy.csv", True),
    ("BMKG", "cuaca_diy.csv", True),
    ("NOAA (ENSO/ONI)", "enso_oni.csv", True),
    ("Google Trends", "google_trends_panik.csv", True),
    ("Sinyal Media", "validasi_berita.csv", True),
    ("Gabungan & deteksi anomali", "gabungan_anomali.csv", True),
    ("Prakiraan harga", "prakiraan_harga.csv", True),
]


def jumlah_baris(path):
    try:
        with open(path, "rb") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


def berubah_sejak_commit(path):
    """True bila berkas berbeda dari versi di commit terakhir (atau belum pernah di-commit)."""
    if not os.path.exists(path):
        return False
    try:
        dilacak = subprocess.run(["git", "ls-files", "--error-unmatch", path],
                                 capture_output=True).returncode == 0
        if not dilacak:
            return True
        return subprocess.run(["git", "diff", "--quiet", "HEAD", "--", path],
                              capture_output=True).returncode != 0
    except OSError:
        return False


def main():
    kini = datetime.now(timezone.utc).isoformat(timespec="seconds")
    baris = []
    for pilar, berkas, otomatis in PILAR:
        path = os.path.join(DATA, berkas)
        ada = os.path.exists(path)
        baris.append({
            "pilar": pilar,
            "berkas": berkas,
            "otomatis": otomatis,
            "dicek_pada_utc": kini if otomatis else "",
            "baris": jumlah_baris(path) if ada else 0,
            "berubah": berubah_sejak_commit(path) if ada else False,
        })
    os.makedirs(DATA, exist_ok=True)
    with open(KELUARAN, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(baris[0].keys()))
        w.writeheader()
        w.writerows(baris)
    for b in baris:
        print(f"  {b['pilar']:28s} {b['baris']:7d} baris  {'BERUBAH' if b['berubah'] else 'tetap  '}  "
              f"{'dicek ' + kini if b['otomatis'] else 'manual'}", flush=True)
    print(f"Status pipeline disimpan ke {KELUARAN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
