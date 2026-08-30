"""
Proksi "kepanikan publik" terhadap harga pangan DIY menggunakan Google
Trends (via pytrends, library tidak resmi yang membalik-rekayasa endpoint
Google Trends -- BUKAN API resmi Google, jadi rawan rate-limit/CAPTCHA;
lihat catatan risiko di bawah).

Ide: lonjakan volume pencarian untuk frasa seperti "harga beras naik" atau
"kelangkaan cabai" sering mendahului atau berbarengan dengan liputan media
soal gejolak harga -- sinyal "perhatian publik" tambahan di luar data
harga & berita, mengikuti gagasan "social attention" dari literatur
(Xu dkk., 2018, ACM TMIS, DOI: 10.1145/3131781) yang menunjukkan ukuran
perhatian publik/sosial membantu memprediksi price shock lebih dini.

Wilayah: geo="ID-YO" (kode ISO 3166-2 untuk Daerah Istimewa Yogyakarta).

PENTING -- keterbatasan yang harus dipahami sebelum dipakai:
  1. pytrends TIDAK didukung resmi oleh Google; endpoint bisa berubah/
     diblokir sewaktu-waktu tanpa pemberitahuan.
  2. Data yang dikembalikan adalah indeks RELATIF (0-100) ternormalisasi
     terhadap titik puncak pada rentang waktu & wilayah yang diminta --
     BUKAN angka volume pencarian absolut, dan tidak bisa dibandingkan
     apa adanya antar-query berbeda tanpa query "anchor" bersama.
  3. Google membatasi rate request (umumnya perlu jeda beberapa detik
     antar-request) dan bisa menampilkan CAPTCHA jika dianggap otomatis
     berlebihan -- jalankan dengan jeda wajar, jangan di-loop rapat.
  4. Granularitas wilayah kabupaten/kota (di bawah level provinsi) sering
     tidak tersedia untuk keyword dengan volume pencarian rendah -- DIY
     level provinsi (ID-YO) lebih dapat diandalkan daripada per-kabupaten.

Cara pakai:
    pip install pytrends pandas
    python google_trends_panik.py
"""

import os
import time
from datetime import datetime, timezone

import pandas as pd

try:
    from pytrends.request import TrendReq
except ImportError:
    TrendReq = None

GEO_DIY = "ID-YO"
KEYWORDS = [
    "harga beras naik",
    "harga cabai naik",
    "kelangkaan pangan",
    "harga sembako naik",
    "harga bawang naik",
]
TIMEFRAME = "today 3-m"  # 3 bulan terakhir; sesuaikan sesuai kebutuhan
JEDA_ANTAR_QUERY_DETIK = 5  # jangan diturunkan -- hindari rate-limit/CAPTCHA

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "google_trends_panik.csv")


def ambil_trend(pytrends: "TrendReq", keyword: str) -> pd.DataFrame:
    pytrends.build_payload([keyword], timeframe=TIMEFRAME, geo=GEO_DIY)
    df = pytrends.interest_over_time()
    if df.empty:
        return df
    df = df.reset_index().rename(columns={keyword: "skor_minat"})
    df["keyword"] = keyword
    df["diambil_pada_utc"] = datetime.now(timezone.utc).isoformat()
    return df[["date", "keyword", "skor_minat", "diambil_pada_utc"]]


def deteksi_lonjakan(df: pd.DataFrame, z_threshold: float = 2.0) -> pd.DataFrame:
    """Tandai lonjakan minat pencarian per keyword pakai rolling z-score,
    pendekatan yang sama dengan deteksi anomali harga di merge_and_detect.py
    supaya kedua sinyal bisa disandingkan langsung."""
    def _per_keyword(g):
        g = g.copy()
        g["rolling_mean"] = g["skor_minat"].rolling(4, min_periods=2).mean()
        g["rolling_std"] = g["skor_minat"].rolling(4, min_periods=2).std()
        g["z_score"] = (g["skor_minat"] - g["rolling_mean"]) / g["rolling_std"]
        g["lonjakan_minat"] = g["z_score"].abs() > z_threshold
        return g
    return df.groupby("keyword", group_keys=False).apply(_per_keyword)


def main():
    if TrendReq is None:
        print("pytrends belum terpasang. Jalankan: pip install pytrends")
        return

    pytrends = TrendReq(hl="id-ID", tz=420)  # tz 420 = UTC+7 (WIB, referensi umum)
    all_data = []

    for kw in KEYWORDS:
        print(f"Mengambil tren untuk: '{kw}' (geo={GEO_DIY})")
        try:
            df = ambil_trend(pytrends, kw)
            if not df.empty:
                all_data.append(df)
            else:
                print(f"  -> kosong (kemungkinan volume pencarian terlalu rendah untuk {GEO_DIY})")
        except Exception as e:
            print(f"  -> GAGAL: {e} (kemungkinan rate-limit/CAPTCHA -- coba lagi nanti)")
        time.sleep(JEDA_ANTAR_QUERY_DETIK)

    if not all_data:
        print("Tidak ada data berhasil diambil.")
        return

    result = pd.concat(all_data, ignore_index=True)
    result = deteksi_lonjakan(result)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    result.to_csv(OUTPUT_PATH, index=False)

    lonjakan = result[result["lonjakan_minat"]]
    print(f"\n{len(lonjakan)} lonjakan minat pencarian terdeteksi dari {len(result)} titik data.")
    print(f"Hasil disimpan ke {OUTPUT_PATH}")
    print("Ingat: ini sinyal indikatif (indeks relatif), gunakan sebagai pelengkap "
          "bukan pengganti data harga & validasi berita.")


if __name__ == "__main__":
    main()
