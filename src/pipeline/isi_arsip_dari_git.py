"""
Isi mundur data/arsip/ dari riwayat git (cukup dijalankan SEKALI).

Setiap run pipeline sejak 30 Agt 2026 meng-commit berkas data, jadi versi lama
berkas yang ditimpa (prakiraan, status cek, Google Trends, berita) masih ada di
riwayat git. Skrip ini membaca semua versi itu secara berurutan dan memasukkannya
ke arsip memakai aturan yang sama dengan arsip_harian.py, sehingga arsip dimulai
dari awal pipeline, bukan dari hari arsip dipasang.

Butuh klon PENUH (bukan --depth 1):
    git clone https://github.com/mrafiraamadhan/sigap-pangan-diy
    cd sigap-pangan-diy && python src/pipeline/isi_arsip_dari_git.py
Aman dijalankan ulang: baris yang sudah ada tidak digandakan.
"""

import io
import os
import subprocess
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arsip_harian as A  # noqa: E402


def versi(path):
    out = subprocess.run(["git", "log", "--reverse", "--format=%H %cI", "--", path],
                         capture_output=True, text=True, cwd=A.ROOT).stdout
    return [baris.split() for baris in out.splitlines() if baris.strip()]


def isi(path_rel, fungsi):
    total = 0
    vs = versi(path_rel)
    for h, waktu in vs:
        teks = subprocess.run(["git", "show", f"{h}:{path_rel}"], capture_output=True,
                              text=True, cwd=A.ROOT).stdout
        if not teks.strip():
            continue
        try:
            df = pd.read_csv(io.StringIO(teks), dtype=str, keep_default_na=False)
        except Exception:
            continue
        total += fungsi(df, h[:7], waktu)
    print(f"{path_rel}: {len(vs)} versi dibaca, +{total} baris arsip")


def main():
    isi("data/prakiraan_harga.csv", lambda df, h, w: A.arsip_prakiraan(df, asal=h, waktu=w))
    isi("data/google_trends_panik.csv", lambda df, h, w: A.arsip_trends(df, asal=h, waktu=w))
    isi("data/validasi_berita.csv", lambda df, h, w: A.arsip_berita(df, asal=h, waktu=w))
    # status_pipeline: waktu run = kolom dicek_pada_utc (sama untuk semua pilar otomatis)
    def _log(df, h, w):
        dicek = [v for v in df.get("dicek_pada_utc", pd.Series(dtype=str)) if v]
        return A.arsip_log_cek(df, dicek[0] if dicek else w, asal=h)
    isi("data/status_pipeline.csv", _log)


if __name__ == "__main__":
    main()
