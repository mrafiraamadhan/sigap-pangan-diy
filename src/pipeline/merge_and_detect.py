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


PROVINSI_LABEL = "DI Yogyakarta"


def load_harga() -> pd.DataFrame:
    """Baca harga_pangan_diy.csv. File ini bisa datang dari 2 sumber dengan
    skema beda: dpkp_diy_scraper.py (per kabupaten/kota, kolom 'harga') atau
    pihps_scraper.py versi API baru (tingkat provinsi, kolom 'harga_rp',
    tanpa 'kabupaten_kota'). Normalisasi supaya keduanya bisa diproses sama."""
    df = pd.read_csv(HARGA_PATH, parse_dates=["tanggal"])

    if "harga" not in df.columns and "harga_rp" in df.columns:
        df = df.rename(columns={"harga_rp": "harga"})

    if "kabupaten_kota" not in df.columns:
        # data PIHPS versi API baru: tingkat provinsi, bukan per kab/kota.
        df["kabupaten_kota"] = df["provinsi"] if "provinsi" in df.columns else PROVINSI_LABEL

    sort_cols = [c for c in ["kabupaten_kota", "komoditas", "tanggal"] if c in df.columns]
    return df.sort_values(sort_cols)


def load_cuaca_harian() -> pd.DataFrame:
    """Ringkas data cuaca per-jam BMKG jadi agregat harian per kab/kota:
    curah hujan total & suhu rata-rata -- supaya bisa di-join dengan harga
    yang granularitasnya harian. Juga tambahkan 1 baris agregat TINGKAT
    PROVINSI per tanggal (rata-rata/OR dari semua kab/kota), supaya data
    harga yang tingkat provinsi (lihat load_harga) tetap bisa di-join
    dengan konteks cuaca, bukan cuma yang datanya per kab/kota."""
    df = pd.read_csv(CUACA_PATH, parse_dates=["waktu_lokal"])
    df["tanggal"] = df["waktu_lokal"].dt.date
    agg = df.groupby(["kabupaten_kota", "tanggal"]).agg(
        curah_hujan_mm_total=("curah_hujan_mm", "sum"),
        suhu_c_rata2=("suhu_c", "mean"),
    ).reset_index()

    provinsi_agg = agg.groupby("tanggal").agg(
        curah_hujan_mm_total=("curah_hujan_mm_total", "mean"),
        suhu_c_rata2=("suhu_c_rata2", "mean"),
    ).reset_index()
    provinsi_agg["kabupaten_kota"] = PROVINSI_LABEL

    agg = pd.concat([agg, provinsi_agg], ignore_index=True)
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

    # PENTING: index kolom secara eksplisit -- kalau tidak, pandas diam-diam
    # membuang kolom "kabupaten_kota"/"komoditas" dari hasil groupby().apply()
    # ini (perilaku default DataFrameGroupBy.apply utk kolom yang dipakai
    # sebagai kunci group), bikin hasil akhir kehilangan identitas grupnya.
    kolom = df_harga.columns.tolist()
    return df_harga.groupby(["kabupaten_kota", "komoditas"], group_keys=False)[kolom].apply(_per_group)


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
