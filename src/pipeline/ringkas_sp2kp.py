"""
Meringkas data SP2KP tingkat pasar menjadi deret harian tingkat provinsi.

Berkas mentah SP2KP berukuran belasan MB karena memuat satu baris per
tanggal x pasar x varian. Ukuran itu wajar untuk arsip, tetapi terlalu berat
diunduh peramban. Modul ini menghasilkan versi ringkas untuk papan pantau:
rata-rata harian lintas pasar, beserta SEBARAN antar pasar yang dipakai
modul prakiraan sebagai penskala volatilitas.

Masukan : data/harga_sp2kp_diy.csv
Keluaran: docs/data/harga_sp2kp_diy.csv
"""

import os
import sys

import pandas as pd

MASUK = os.path.join("data", "harga_sp2kp_diy.csv")
KELUAR = os.path.join("docs", "data", "harga_sp2kp_diy.csv")


def main():
    if not os.path.exists(MASUK):
        print(f"{MASUK} belum ada, tidak ada yang diringkas.", flush=True)
        return 0

    d = pd.read_csv(MASUK)
    if not len(d) or "varian" not in d.columns:
        print("Berkas SP2KP kosong atau kolomnya tidak dikenali.", flush=True)
        return 0

    d["harga"] = pd.to_numeric(d["harga"], errors="coerce")
    d = d.dropna(subset=["harga"]).query("harga > 0")

    r = (d.groupby(["tanggal", "varian"])
           .agg(harga=("harga", "mean"),
                sebaran_pasar=("harga", "std"),
                n_pasar=("harga", "size"),
                satuan=("satuan", "first"),
                komoditas=("komoditas", "first"))
           .reset_index())
    r["harga"] = r["harga"].round(0)
    r["sebaran_pasar"] = r["sebaran_pasar"].round(1)

    os.makedirs(os.path.dirname(KELUAR), exist_ok=True)
    r.to_csv(KELUAR, index=False)
    print(f"Ringkasan SP2KP tersimpan: {len(r)} baris, {r.varian.nunique()} varian, "
          f"{r.tanggal.min()} s.d. {r.tanggal.max()} "
          f"({os.path.getsize(KELUAR)/1e6:.2f} MB)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
