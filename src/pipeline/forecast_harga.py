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
DATA = os.path.join(HERE, "..", "..", "data")
# Urutan sumber: SP2KP lebih padat (614 pencatatan/deret, berubah pada 6,3% hari
# kalender) sehingga volatilitas terealisasi dapat dihitung; DPKP dipakai bila
# SP2KP belum tersedia.
SUMBER_URUT = [
    (os.path.join(DATA, "harga_sp2kp_diy.csv"), "SP2KP Kemendag", ("tanggal", "varian", "harga")),
    (os.path.join(DATA, "harga_pangan_dpkp_diy.csv"), "DPKP DIY", ("Tanggal", "Komoditas", "Harga")),
]
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "prakiraan_harga.csv")

# Horizon dalam satuan MINGGU (deret diresample mingguan; DPKP terbit ~2x/minggu)
HORIZON = {"1 minggu": 1, "1 bulan": 4}

MIN_OBS = 60          # minimal observasi mingguan agar komoditas layak diprakirakan
MIN_SAMPEL_RASIO = 30  # minimal sampel perubahan h-langkah untuk kuantil empiris

AMBANG = {"prob_naik_5": 1.05, "prob_naik_10": 1.10, "prob_turun_5": 0.95}


def layak_diprakirakan(y: pd.Series) -> bool:
    """Saring deret yang memang bergerak cukup untuk diprakirakan.

    Uji kelayakan atas ketiga sumber (lihat Subbab 4.9 naskah) memberi hasil
    yang tegas: dari SP2KP 31 dari 52 deret lolos, dari DPKP 14 dari 19, dan
    dari PIHPS NOL dari 20. Deret PIHPS pada median hanya memiliki 15 nilai
    berbeda dalam 52 minggu, sehingga yang akan diramalkan bukan harga pasar
    melainkan jadwal pembaruan angkanya. Karena itu PIHPS sengaja tidak
    diprakirakan, dan deret lain yang terlalu datar juga dilewati.
    """
    v = y.values.astype(float)
    if len(v) < MIN_OBS:
        return False
    bergerak = float((np.abs(np.diff(v)) > 1e-9).mean() * 100)
    return bergerak >= 25.0 and len(np.unique(v)) >= 25


def muat_deret(jalur: str, nama_sumber: str, awalan: tuple) -> dict:
    """Baca CSV DPKP & ubah jadi deret mingguan per komoditas.

    Nama kolom hasil scraping bisa panjang/berubah, jadi dideteksi dari
    AWALAN namanya (pola yang sama dipakai di seluruh proyek ini).
    """
    AWALAN = awalan
    df = pd.read_csv(jalur)
    kol = [str(c) for c in df.columns]
    cT = next((c for c in kol if c.startswith(AWALAN[0])), None)
    cK = next((c for c in kol if c.startswith(AWALAN[1])), None)
    cH = next((c for c in kol if c.startswith(AWALAN[2])), None)
    if not (cT and cK and cH):
        raise RuntimeError(f"Kolom {nama_sumber} tidak dikenali: {kol[:6]}")

    # Buang kolom yang namanya akan bentrok setelah rename (SP2KP punya kolom
    # "komoditas" untuk kelompok DAN "varian" untuk jenis; yang dipakai varian).
    for c in list(df.columns):
        if c not in (cT, cK, cH) and str(c) in ("tanggal", "komoditas", "harga"):
            df = df.drop(columns=[c])
    df = df.rename(columns={cT: "tanggal", cK: "komoditas", cH: "harga"})
    df["tanggal"] = pd.to_datetime(df["tanggal"], errors="coerce")
    df["harga"] = pd.to_numeric(df["harga"], errors="coerce")
    df = df.dropna(subset=["tanggal", "komoditas", "harga"])
    df = df[df["harga"] > 0]

    # SP2KP berisi satu baris per pasar: rata-ratakan dulu ke tingkat provinsi.
    df = df.groupby(["tanggal", "komoditas"], as_index=False)["harga"].mean()

    deret = {}
    for kom, s in df.groupby("komoditas"):
        y = (s.sort_values("tanggal").drop_duplicates("tanggal")
              .set_index("tanggal")["harga"].resample("W").last().ffill().dropna())
        if layak_diprakirakan(y):
            deret[str(kom)] = y
    return deret


# ---------------------------------------------------------------------------
# Penduga titik berstruktur, dipakai HANYA bila terbukti menolong
# ---------------------------------------------------------------------------
# Uji-mundur 45 komoditas SP2KP (lihat Subbab 4.7 naskah) memberi hasil yang
# tidak seragam. Struktur lag NYATA ada: pada 23 dari 50 deret, selisih log
# mingguan menolak hipotesis derau putih (Ljung-Box p < 0,05). Namun
# memanfaatkannya hanya menguntungkan pada komoditas yang harganya benar-benar
# terbentuk di pasar. Pada beras, gula, dan minyak goreng yang harganya banyak
# dipengaruhi kebijakan, model berstruktur justru jauh memperburuk.
#
# Karena itu modul ini TIDAK memakai satu model untuk semua. Untuk tiap
# komoditas dijalankan uji-mundur kecil di dalam data latih; model berstruktur
# hanya dipakai bila di situ ia mengalahkan penduga naif dengan margin yang
# cukup. Selebihnya penduga naif dipertahankan.
#
# Tiga pagar wajib (pelajaran dari versi tanpa pagar yang menghasilkan MASE
# miliaran pada deret nyaris konstan):
#   1. deret degeneratif langsung memakai naif;
#   2. ramalan dipotong ke rentang perubahan yang pernah benar-benar terjadi;
#   3. margin kemenangan diminta, bukan sekadar unggul tipis.

MIN_MARGIN = 0.03      # model harus >=3% lebih baik daripada naif di data latih
JENDELA_UJI = 20       # banyak titik uji dalam data latih


def _degeneratif(v: np.ndarray) -> bool:
    dl = np.diff(np.log(v))
    return (len(np.unique(v)) < 12 or np.std(dl) < 1e-4
            or (np.abs(dl) > 1e-9).mean() < 0.15)


def _pagar(f: float, v: np.ndarray, h: int, naif: float) -> float:
    """Potong ramalan ke rentang perubahan h-langkah yang pernah terjadi."""
    if not np.isfinite(f) or f <= 0 or len(v) <= h:
        return naif
    r = np.log(v[h:] / v[:-h])
    r = r[np.isfinite(r)]
    if len(r) < 10:
        return naif
    lo, hi = np.quantile(r, [0.01, 0.99])
    m = (hi - lo) * 0.2
    return float(naif * np.exp(np.clip(np.log(f / naif), lo - m, hi + m)))


def _ar_selisih(v: np.ndarray, h: int) -> float:
    """AR(p) pada selisih log; p dipilih lewat AIC pada data yang diberikan."""
    naif = float(v[-1])
    if _degeneratif(v):
        return naif
    ly = np.log(v)
    dy = np.diff(ly)
    ba, bf = np.inf, None
    for p in range(1, 7):
        if len(dy) < p + 30:
            break
        try:
            X = np.column_stack([dy[p - i - 1:len(dy) - i - 1] for i in range(p)])
            yv = dy[p:]
            X = np.column_stack([np.ones(len(yv)), X])
            beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
            s2 = float(np.mean((yv - X @ beta) ** 2))
            aic = len(yv) * np.log(s2 + 1e-12) + 2 * (p + 1)
            if aic < ba and np.all(np.isfinite(beta)):
                hist = list(dy)
                for _ in range(h):
                    xs = np.concatenate([[1.0], [hist[-i - 1] for i in range(p)]])
                    hist.append(float(np.clip(xs @ beta, -.5, .5)))
                ba, bf = aic, float(np.exp(ly[-1] + np.sum(hist[len(dy):])))
        except Exception:
            pass
    return _pagar(bf if bf else naif, v, h, naif)


def penduga_titik(v: np.ndarray, h: int) -> tuple[float, str]:
    """Kembalikan (ramalan, nama metode) untuk satu komoditas."""
    naif = float(v[-1])
    if len(v) < 70 or _degeneratif(v):
        return naif, "naif"

    # uji-mundur kecil DI DALAM data latih
    gal_n, gal_m = [], []
    mulai = max(50, len(v) - JENDELA_UJI - h)
    for t in range(mulai, len(v) - h + 1):
        aktual = v[t + h - 1]
        gal_n.append(abs(v[t - 1] - aktual))
        gal_m.append(abs(_ar_selisih(v[:t], h) - aktual))
    if len(gal_n) < 8:
        return naif, "naif"
    mn, mm = float(np.mean(gal_n)), float(np.mean(gal_m))
    if mm < mn * (1 - MIN_MARGIN):
        return _ar_selisih(v, h), "AR(p) selisih-log"
    return naif, "naif"


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

    p0 = float(v[-1])                      # jangkar interval tetap harga terkini
    titik, metode = penduga_titik(v, h)    # penduga titik bisa berstruktur

    # ---- Penskalaan lebar interval mengikuti volatilitas terkini ----
    # Uji-mundur 20 komoditas SP2KP: menskalakan lebar interval dengan rasio
    # volatilitas 4 minggu terakhir terhadap rata-rata historisnya memperbaiki
    # skor Winkler 2,0% (h=1) dan 3,8% (h=4), sekaligus menarik cakupan dari
    # 84,7% ke 80,5% pada nominal 80% (lihat Subbab 4.8 naskah).
    # Pembatas 0,5-2,5 mencegah interval meledak saat volatilitas sesaat memuncak.
    skala = 1.0
    try:
        dl = pd.Series(np.log(v)).diff()
        vol = dl.rolling(4).std()
        vkini, vrata = float(vol.iloc[-1]), float(vol.mean())
        if np.isfinite(vkini) and np.isfinite(vrata) and vrata > 1e-9:
            skala = float(np.clip(vkini / vrata, 0.5, 2.5))
    except Exception:
        skala = 1.0

    def q(a):
        """Kuantil empiris, lebarnya diskalakan pada ruang logaritma."""
        r = float(np.quantile(rasio, a))
        return float(np.exp(np.log(max(r, 1e-9)) * skala))
    hasil = {
        "harga_terkini": round(p0, 2),
        "titik": round(titik, 2),
        "metode_titik": metode,
        "lo80": round(p0 * q(0.10), 2), "hi80": round(p0 * q(0.90), 2),
        "lo95": round(p0 * q(0.025), 2), "hi95": round(p0 * q(0.975), 2),
        "n_sampel_rasio": int(len(rasio)),
        "n_obs_mingguan": int(len(v)),
        "skala_volatilitas": round(skala, 3),
    }
    for nama, amb in AMBANG.items():
        p = float((rasio > amb).mean()) if amb > 1 else float((rasio < amb).mean())
        hasil[nama] = round(p * 100, 1)
    # lebar interval 80% relatif -- ukuran ketidakpastian yang mudah dibaca
    hasil["lebar80_persen"] = round((hasil["hi80"] - hasil["lo80"]) / p0 * 100, 1)
    return hasil


def main():
    diambil = datetime.now(timezone.utc).isoformat()
    baris = []

    # Prakiraan dibuat untuk SETIAP sumber yang tersedia dan lolos uji
    # kelayakan, bukan hanya satu. Dengan begitu papan pantau dapat
    # menyandingkan prakiraan dari dua lembaga yang mengumpulkan datanya
    # secara terpisah: bila keduanya sepakat, keyakinannya jauh lebih kuat.
    for jalur, nama_sumber, awalan in SUMBER_URUT:
        if not os.path.exists(jalur):
            print(f"  {nama_sumber}: berkas belum ada, dilewati.", flush=True)
            continue
        try:
            deret = muat_deret(jalur, nama_sumber, awalan)
        except Exception as e:
            print(f"  {nama_sumber}: gagal dibaca ({e}), dilewati.", flush=True)
            continue
        if not deret:
            print(f"  {nama_sumber}: tidak ada deret yang lolos uji kelayakan.", flush=True)
            continue
        print(f"  {nama_sumber}: {len(deret)} deret layak diprakirakan.", flush=True)

        for kom, y in sorted(deret.items()):
            tgl_akhir = y.index[-1].date().isoformat()
            for label, h in HORIZON.items():
                r = prakirakan(y, h)
                if not r:
                    continue
                baris.append({
                    "sumber": nama_sumber,
                    "komoditas": kom, "horizon": label, "horizon_minggu": h,
                    "tanggal_data_terakhir": tgl_akhir,
                    "tanggal_target": (y.index[-1] + pd.Timedelta(weeks=h)).date().isoformat(),
                    **r,
                    "metode": "penduga bergerbang + kuantil empiris berpenskala volatilitas",
                    "diambil_pada_utc": diambil,
                })

    if not baris:
        print("Tidak ada prakiraan yang dapat dihasilkan dari sumber mana pun.")
        return

    out = pd.DataFrame(baris)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)

    print(f"\n{len(out)} baris prakiraan disimpan ke {OUTPUT_PATH}")
    for sb, g in out.groupby("sumber"):
        print(f"  {sb:16s} {g['komoditas'].nunique():3d} komoditas, "
              f"data s.d. {g['tanggal_data_terakhir'].max()}, "
              f"{(g['metode_titik'] != 'naif').sum()} memakai model berstruktur")

    utama = out[out["sumber"] == out["sumber"].iloc[0]]
    sebulan = utama[utama["horizon"] == "1 bulan"].sort_values("prob_naik_10", ascending=False)
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
