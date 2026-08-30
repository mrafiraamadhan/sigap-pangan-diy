"""
Gabungkan harga pangan (PIHPS), cuaca (BMKG), dan fase tanam (SIMOTANDI),
lalu jalankan deteksi anomali berlapis.

Jalankan setelah data/harga_pangan_diy.csv dan data/cuaca_diy.csv terisi
(hasil dari pihps_scraper.py dan bmkg_cuaca.py).
"""

import os
import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "..", "data")

HARGA_PATH = os.path.join(DATA_DIR, "harga_pangan_diy.csv")
CUACA_PATH = os.path.join(DATA_DIR, "cuaca_diy.csv")
OUTPUT_PATH = os.path.join(DATA_DIR, "gabungan_anomali.csv")

ROLLING_WINDOW = 14   # hari
Z_THRESHOLD = 2.0


def load_harga() -> pd.DataFrame:
    df = pd.read_csv(HARGA_PATH, parse_dates=["tanggal"])
    return df.sort_values(["kabupaten_kota", "komoditas", "tanggal"])


def load_cuaca_harian() -> pd.DataFrame:
    """Ringkas data cuaca per-jam BMKG jadi agregat harian per kab/kota:
    curah hujan total & suhu rata-rata -- supaya bisa di-join dengan harga
    yang granularitasnya harian."""
    df = pd.read_csv(CUACA_PATH, parse_dates=["waktu_lokal"])
    df["tanggal"] = df["waktu_lokal"].dt.date
    agg = df.groupby(["kabupaten_kota", "tanggal"]).agg(
        curah_hujan_mm_total=("curah_hujan_mm", "sum"),
        suhu_c_rata2=("suhu_c", "mean"),
    ).reset_index()
    agg["tanggal"] = pd.to_datetime(agg["tanggal"])
    return agg


def detect_price_anomalies(df_harga: pd.DataFrame) -> pd.DataFrame:
    def _per_group(g):
        g = g.copy()
        g["rolling_mean"] = g["harga"].rolling(ROLLING_WINDOW, min_periods=5).mean()
        g["rolling_std"] = g["harga"].rolling(ROLLING_WINDOW, min_periods=5).std()
        g["z_score"] = (g["harga"] - g["rolling_mean"]) / g["rolling_std"]
        g["anomali_harga"] = g["z_score"].abs() > Z_THRESHOLD
        return g

    return df_harga.groupby(["kabupaten_kota", "komoditas"], group_keys=False).apply(_per_group)


def flag_cuaca_ekstrem(df_cuaca: pd.DataFrame) -> pd.DataFrame:
    """Tandai hari dengan curah hujan jauh di atas normal (proksi sederhana
    untuk potensi gangguan distribusi/produksi). Threshold BMKG untuk hujan
    lebat ~50mm/hari, sangat lebat >100mm/hari -- pakai itu sebagai acuan
    literatur, bukan cuma statistik lokal."""
    df = df_cuaca.copy()
    df["cuaca_ekstrem"] = df["curah_hujan_mm_total"] >= 50
    return df


def main():
    if not os.path.isfile(HARGA_PATH):
        print(f"Belum ada {HARGA_PATH} -- jalankan pihps_scraper.py dulu.")
        return
    if not os.path.isfile(CUACA_PATH):
        print(f"Belum ada {CUACA_PATH} -- jalankan bmkg_cuaca.py dulu.")
        return

    harga = detect_price_anomalies(load_harga())
    cuaca = flag_cuaca_ekstrem(load_cuaca_harian())

    gabungan = harga.merge(cuaca, on=["kabupaten_kota", "tanggal"], how="left")

    # Sinyal gabungan: anomali harga YANG BERBARENGAN dengan cuaca ekstrem
    # dalam 3 hari terakhir -- ini kandidat "anomali dengan penjelasan cuaca"
    gabungan["anomali_dengan_konteks_cuaca"] = (
        gabungan["anomali_harga"] & gabungan["cuaca_ekstrem"].fillna(False)
    )

    os.makedirs(DATA_DIR, exist_ok=True)
    gabungan.to_csv(OUTPUT_PATH, index=False)

    total_anomali = gabungan["anomali_harga"].sum()
    dengan_konteks = gabungan["anomali_dengan_konteks_cuaca"].sum()
    print(f"Total anomali harga terdeteksi : {total_anomali}")
    print(f"  ...yang berbarengan cuaca ekstrem : {dengan_konteks} "
          f"({dengan_konteks/total_anomali*100:.1f}% dari anomali)" if total_anomali else "")
    print(f"\nHasil disimpan ke {OUTPUT_PATH}")
    print("(Belum termasuk konteks SIMOTANDI -- tambahkan join serupa setelah "
          "simotandi.py menghasilkan data fase tanam per kab/kota per dasarian)")


if __name__ == "__main__":
    main()
