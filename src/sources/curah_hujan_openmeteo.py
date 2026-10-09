"""
Ambil riwayat curah hujan BULANAN untuk lima kabupaten/kota DIY dari Open-Meteo
Historical Weather API (reanalisis ERA5/ERA5-Land milik ECMWF), 1991 sampai
hari ini. Dipakai papan pantau untuk menghubungkan hujan, El Nino (ONI), dan
panen padi (SIMOTANDI).

Kenapa bukan BMKG? API publik BMKG hanya memberi PRAKIRAAN 3 hari ke depan
(yang sudah dipakai pilar Cuaca), bukan arsip pengamatan; arsip stasiun BMKG
(dataonline.bmkg.go.id) memerlukan akun dan tidak bisa ditarik mesin. Open-Meteo
gratis, tanpa kunci, dan datanya reanalisis: perkiraan model yang diasimilasi
dengan pengamatan, bukan penakar hujan. Untuk total bulanan pada skala
kabupaten, akurasinya memadai; untuk hari-per-hari tidak dipakai.

Sumber: https://archive-api.open-meteo.com/v1/archive  (lisensi CC-BY 4.0)
Keluaran: data/curah_hujan_bulanan_diy.csv
    tahun, bulan, kabupaten_kota, hujan_mm, hari_hujan, hari_data, sumber, diambil_pada_utc

Penarikan bersifat tambahan: kalau berkas sudah ada, hanya dua bulan terakhir
yang ditarik ulang (arsip Open-Meteo terlambat sekitar 5 hari dari hari ini).

Cara pakai:
    pip install requests pandas
    python curah_hujan_openmeteo.py            # tambahan
    python curah_hujan_openmeteo.py --penuh    # tarik ulang sejak 1991
"""

import argparse
import os
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

URL = "https://archive-api.open-meteo.com/v1/archive"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "curah_hujan_bulanan_diy.csv")
TAHUN_AWAL = 1991

# Titik perwakilan tiap kabupaten/kota (pusat wilayah pertanian, bukan kantor bupati).
TITIK = [
    ("Kota Yogyakarta", -7.80, 110.37),
    ("Sleman", -7.72, 110.36),
    ("Bantul", -7.89, 110.33),
    ("Kulon Progo", -7.83, 110.16),
    ("Gunungkidul", -7.97, 110.60),
]
HEADERS = {"User-Agent": "Mozilla/5.0 (SIGAP-Pangan-DIY research bot; YES2026 food security paper)"}


def tarik_harian(lat: float, lon: float, mulai: date, selesai: date) -> pd.DataFrame:
    """Hujan harian (mm) untuk satu titik, dipotong per 10 tahun supaya respons ringan."""
    potongan = []
    a = mulai
    while a <= selesai:
        b = min(date(a.year + 9, 12, 31), selesai)
        params = {"latitude": lat, "longitude": lon, "start_date": a.isoformat(), "end_date": b.isoformat(),
                  "daily": "precipitation_sum", "timezone": "Asia/Jakarta"}
        for percobaan in range(3):
            try:
                r = requests.get(URL, params=params, headers=HEADERS, timeout=60)
                r.raise_for_status()
                j = r.json()
                d = j.get("daily", {})
                potongan.append(pd.DataFrame({"tanggal": d.get("time", []), "mm": d.get("precipitation_sum", [])}))
                break
            except Exception as e:
                if percobaan == 2:
                    raise
                print(f"  ulangi ({type(e).__name__}: {str(e)[:120]})")
                time.sleep(3 * (percobaan + 1))
        a = date(b.year + 1, 1, 1)
        time.sleep(0.6)  # sopan ke server
    if not potongan:
        return pd.DataFrame(columns=["tanggal", "mm"])
    return pd.concat(potongan, ignore_index=True)


def ke_bulanan(harian: pd.DataFrame, nama: str) -> pd.DataFrame:
    h = harian.copy()
    h["tanggal"] = pd.to_datetime(h["tanggal"], errors="coerce")
    h["mm"] = pd.to_numeric(h["mm"], errors="coerce")
    h = h.dropna(subset=["tanggal"])
    h["tahun"], h["bulan"] = h["tanggal"].dt.year, h["tanggal"].dt.month
    g = h.groupby(["tahun", "bulan"]).agg(hujan_mm=("mm", "sum"), hari_hujan=("mm", lambda x: int((x >= 1).sum())),
                                          hari_data=("mm", lambda x: int(x.notna().sum()))).reset_index()
    g["hujan_mm"] = g["hujan_mm"].round(1)
    g.insert(2, "kabupaten_kota", nama)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--penuh", action="store_true", help="tarik ulang semua sejak 1991")
    args = ap.parse_args()

    selesai = date.today() - timedelta(days=6)   # arsip Open-Meteo tertinggal ~5 hari
    lama = None
    if os.path.isfile(OUTPUT_PATH) and not args.penuh:
        lama = pd.read_csv(OUTPUT_PATH)
        if len(lama):
            th, bl = int(lama["tahun"].max()), int(lama[lama["tahun"] == lama["tahun"].max()]["bulan"].max())
            # mundur satu bulan dari bulan terakhir, lalu ke tanggal 1
            mulai = (date(th, bl, 1) - timedelta(days=1)).replace(day=1)
        else:
            mulai = date(TAHUN_AWAL, 1, 1)
    else:
        mulai = date(TAHUN_AWAL, 1, 1)
    if mulai > selesai:
        print("Belum ada bulan baru untuk ditarik."); return 0
    print(f"Menarik hujan harian {mulai} s.d. {selesai} untuk {len(TITIK)} titik...")

    diambil = datetime.now(timezone.utc).isoformat()
    hasil = []
    for nama, lat, lon in TITIK:
        harian = tarik_harian(lat, lon, mulai, selesai)
        bln = ke_bulanan(harian, nama)
        print(f"  {nama:16s} {len(harian):6d} hari -> {len(bln)} bulan")
        hasil.append(bln)
    baru = pd.concat(hasil, ignore_index=True)
    baru["sumber"] = "Open-Meteo ERA5 (ECMWF) reanalisis"
    baru["diambil_pada_utc"] = diambil

    if lama is not None and len(lama):
        kunci = ["tahun", "bulan", "kabupaten_kota"]
        lama = lama[~lama.set_index(kunci).index.isin(baru.set_index(kunci).index)]
        gabung = pd.concat([lama, baru], ignore_index=True)
    else:
        gabung = baru
    gabung = gabung.sort_values(["kabupaten_kota", "tahun", "bulan"]).reset_index(drop=True)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    gabung.to_csv(OUTPUT_PATH, index=False)
    print(f"Tersimpan {os.path.relpath(OUTPUT_PATH)}: {len(gabung)} baris "
          f"({int(gabung['tahun'].min())}-{int(gabung['tahun'].max())}).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"GAGAL: {type(e).__name__}: {str(e)[:300]}")
        raise SystemExit(1)
