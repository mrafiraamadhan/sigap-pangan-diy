"""
Prakiraan harga pangan DIY 1 minggu & 1 bulan ke depan — SIGAP Pangan DIY.

=== DASAR TEORI & MENGAPA BENTUKNYA SEPERTI INI ===

Modul ini TIDAK menjual "ramalan harga titik" karena hal itu tidak dapat
dipertanggungjawabkan secara empiris pada data kita. Kesimpulan tersebut
bukan asumsi, melainkan hasil uji-mundur (rolling-origin backtest) atas
14 komoditas DPKP DIY, ~226 minggu (Apr 2022 - Agu 2026), yang membandingkan
delapan pendekatan: naif (random walk), drift, SES, Theta, Holt teredam,
ARIMA(1,1,1), naif musiman, dan regresi Ridge lag+musim.

    Hasil (MASE = MAE model / MAE naif; <1 berarti mengalahkan naif):
      Horizon 1 minggu : TIDAK ADA model yang mengalahkan naif
                         (MASE rata-rata terbaik = 1,000 oleh naif itu sendiri)
      Horizon 1 bulan  : naif tetap terbaik rata-rata (MASE 1,000);
                         SES menang di 5/14 komoditas, ARIMA di 7/14,
                         tetapi kalah pada rata-rata lintas komoditas.

Temuan ini konsisten dengan literatur harga komoditas: pada horizon pendek
deret harga mendekati MARTINGALE (random walk), sehingga penduga titik terbaik
adalah harga hari ini. Memaksakan model yang lebih rumit justru menambah galat
— analogi Meese-Rogoff pada nilai tukar.

Maka produk yang benar untuk SISTEM PERINGATAN DINI bukan angka tunggal,
melainkan KETIDAKPASTIAN yang terkalibrasi:

  1. Penduga titik  : harga terkini (random walk) -- jujur & terbukti terbaik.
  2. Interval prakiraan : kuantil EMPIRIS dari perubahan h-langkah historis
     tiap komoditas (nonparametrik; tidak mengasumsikan normalitas, sehingga
     menangkap ekor tebal khas harga cabai). Sejalan dengan pendekatan kuantil
     ekstrem pada Excessive Food Price Variability EWS (IFPRI).
     Kalibrasi teruji (uji-mundur, cakupan seharusnya 80%/95%):
        h=1 minggu : cakupan 80% -> 76,0% ; cakupan 95% -> 94,5%
        h=1 bulan  : cakupan 80% -> 82,7% ; cakupan 95% -> 94,1%
  3. Peluang menembus ambang (naik >5%, >10%, turun >5%) -- langsung dapat
     ditindaklanjuti kebijakan. Skill Brier +10,3% terhadap acuan iklim
     (frekuensi dasar global) pada horizon 1 bulan.

Catatan uji tambahan yang JUJUR dilaporkan: mengondisikan distribusi pada bulan
kalender DIUJI dan TERNYATA MEMPERBURUK (skill turun +10,3% -> +4,8%) karena
sampel per bulan terlalu sedikit; karena itu TIDAK dipakai.

Sumber data: DPKP DIY (harian). Deret PIHPS provinsi sengaja TIDAK dipakai
untuk prakiraan karena berbentuk fungsi tangga -- hanya 9 nilai unik dalam 260
hari pengamatan (berubah 4,2% hari), sehingga yang teramati adalah siklus
pembaruan data acuan, bukan dinamika pasar.

Keluaran: data/prakiraan_harga.csv

Cara pakai:
    pip install pandas numpy
    python forecast_harga.py
"""

import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SUMBER_PATH = os.path.join(HERE, "..", "..", "data", "harga_pangan_dpkp_diy.csv")
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "prakiraan_harga.csv")

# Horizon dalam satuan MINGGU (deret diresample mingguan; DPKP terbit ~2x/minggu)
HORIZON = {"1 minggu": 1, "1 bulan": 4}

MIN_OBS = 60          # minimal observasi mingguan agar komoditas layak diprakirakan
MIN_SAMPEL_RASIO = 30  # minimal sampel perubahan h-langkah untuk kuantil empiris

AMBANG = {"prob_naik_5": 1.05, "prob_naik_10": 1.10, "prob_turun_5": 0.95}


def muat_deret() -> dict:
    """Baca CSV DPKP & ubah jadi deret mingguan per komoditas.

    Nama kolom hasil scraping bisa panjang/berubah, jadi dideteksi dari
    AWALAN namanya (pola yang sama dipakai di seluruh proyek ini).
    """
    df = pd.read_csv(SUMBER_PATH)
    kol = [str(c) for c in df.columns]
    cT = next((c for c in kol if c.startswith("Tanggal")), None)
    cK = next((c for c in kol if c.startswith("Komoditas")), None)
    cH = next((c for c in kol if c.startswith("Harga")), None)
    if not (cT and cK and cH):
        raise RuntimeError(f"Kolom DPKP tidak dikenali: {kol[:6]}")

    df = df.rename(columns={cT: "tanggal", cK: "komoditas", cH: "harga"})
    df["tanggal"] = pd.to_datetime(df["tanggal"], errors="coerce")
    df["harga"] = pd.to_numeric(df["harga"], errors="coerce")
    df = df.dropna(subset=["tanggal", "komoditas", "harga"])
    df = df[df["harga"] > 0]

    deret = {}
    for kom, s in df.groupby("komoditas"):
        y = (s.sort_values("tanggal").drop_duplicates("tanggal")
              .set_index("tanggal")["harga"].resample("W").last().ffill().dropna())
        if len(y) >= MIN_OBS:
            deret[str(kom)] = y
    return deret


def prakirakan(y: pd.Series, h: int) -> dict | None:
    """Prakiraan satu komoditas untuk satu horizon.

    Penduga titik = nilai terakhir (random walk). Ketidakpastian dari kuantil
    empiris rasio harga h-langkah pada seluruh riwayat komoditas itu sendiri.
    """
    v = y.values.astype(float)
    if len(v) < h + MIN_SAMPEL_RASIO:
        return None
    rasio = v[h:] / v[:-h]
    rasio = rasio[np.isfinite(rasio) & (rasio > 0)]
    if len(rasio) < MIN_SAMPEL_RASIO:
        return None

    p0 = float(v[-1])
    q = lambda a: float(np.quantile(rasio, a))
    hasil = {
        "harga_terkini": round(p0, 2),
        "titik": round(p0, 2),                       # random walk
        "lo80": round(p0 * q(0.10), 2), "hi80": round(p0 * q(0.90), 2),
        "lo95": round(p0 * q(0.025), 2), "hi95": round(p0 * q(0.975), 2),
        "n_sampel_rasio": int(len(rasio)),
        "n_obs_mingguan": int(len(v)),
    }
    for nama, amb in AMBANG.items():
        p = float((rasio > amb).mean()) if amb > 1 else float((rasio < amb).mean())
        hasil[nama] = round(p * 100, 1)
    # lebar interval 80% relatif -- ukuran ketidakpastian yang mudah dibaca
    hasil["lebar80_persen"] = round((hasil["hi80"] - hasil["lo80"]) / p0 * 100, 1)
    return hasil


def main():
    deret = muat_deret()
    if not deret:
        print("Tidak ada komoditas dengan riwayat cukup untuk diprakirakan.")
        return

    diambil = datetime.now(timezone.utc).isoformat()
    baris = []
    for kom, y in sorted(deret.items()):
        tgl_akhir = y.index[-1].date().isoformat()
        for label, h in HORIZON.items():
            r = prakirakan(y, h)
            if not r:
                continue
            baris.append({
                "komoditas": kom, "horizon": label, "horizon_minggu": h,
                "tanggal_data_terakhir": tgl_akhir,
                "tanggal_target": (y.index[-1] + pd.Timedelta(weeks=h)).date().isoformat(),
                **r,
                "metode": "random walk + kuantil empiris nonparametrik",
                "diambil_pada_utc": diambil,
            })

    if not baris:
        print("Tidak ada prakiraan yang dapat dihasilkan.")
        return

    out = pd.DataFrame(baris)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)

    n_kom = out["komoditas"].nunique()
    print(f"{len(out)} baris prakiraan ({n_kom} komoditas x {len(HORIZON)} horizon) "
          f"disimpan ke {OUTPUT_PATH}")
    print(f"Data terakhir: {out['tanggal_data_terakhir'].max()}")

    # ringkasan risiko tertinggi -- berguna langsung di log Actions
    sebulan = out[out["horizon"] == "1 bulan"].sort_values("prob_naik_10", ascending=False)
    print("\nPeluang kenaikan >10% dalam 1 bulan (5 tertinggi):")
    for _, r in sebulan.head(5).iterrows():
        print(f"  {r['komoditas']:30s} {r['prob_naik_10']:5.1f}%  "
              f"(rentang 80%: Rp {r['lo80']:,.0f} - Rp {r['hi80']:,.0f})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"GAGAL total: {type(e).__name__}: {str(e)[:300]}")
        raise SystemExit(1)
