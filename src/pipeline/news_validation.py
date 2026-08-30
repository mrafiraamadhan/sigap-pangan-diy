"""
Lapisan validasi tekstual: untuk tiap anomali harga yang terdeteksi
(dari merge_and_detect.py), cari berita independen yang mengonfirmasi
kejadiannya -- mengikuti evidensi Bakry dkk. (2025, arXiv:2508.06497)
bahwa fusi sinyal harga+teks jauh mengungguli model harga semata
(AUC 0,94 vs 0,46 tanpa komponen berita).

Butuh FIRECRAWL_API_KEY (daftar gratis di firecrawl.dev) karena script
ini dijalankan di luar sesi Claude (mis. di GitHub Actions), jadi tidak
bisa memakai tool MCP Firecrawl yang tersambung di akun Claude-mu.

Cara pakai:
    export FIRECRAWL_API_KEY="fc-xxxxxxxx"
    python news_validation.py
"""

import os
import csv
import requests

API_KEY = os.environ.get("FIRECRAWL_API_KEY")
SEARCH_URL = "https://api.firecrawl.dev/v1/search"

HERE = os.path.dirname(os.path.abspath(__file__))
ANOMALI_PATH = os.path.join(HERE, "..", "..", "data", "gabungan_anomali.csv")
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "validasi_berita.csv")


def cari_berita(query: str, limit: int = 5) -> list:
    if not API_KEY:
        raise RuntimeError("FIRECRAWL_API_KEY belum diset di environment variable.")
    resp = requests.post(
        SEARCH_URL,
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"query": query, "limit": limit},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", {}).get("web", []) or data.get("data", [])


def validasi_anomali(tanggal: str, komoditas: str, kabupaten: str) -> dict:
    """Bangun query pencarian dari konteks anomali, kembalikan hasil berita
    teratas sebagai bukti pendukung (atau list kosong kalau tidak ada)."""
    bulan_tahun = tanggal[:7]  # 'YYYY-MM'
    query = f"harga {komoditas} {kabupaten} {bulan_tahun} naik turun"
    hasil = cari_berita(query, limit=3)
    return {
        "tanggal": tanggal,
        "komoditas": komoditas,
        "kabupaten_kota": kabupaten,
        "query": query,
        "jumlah_berita_ditemukan": len(hasil),
        "judul_teratas": hasil[0]["title"] if hasil else "",
        "url_teratas": hasil[0]["url"] if hasil else "",
        "tervalidasi": len(hasil) > 0,
    }


# Batasan supaya ramah kuota API gratis Firecrawl. Run 30 Agt 2026 mencoba
# memvalidasi SEMUA 360 anomali sekaligus tanpa jeda -> hampir semua ditolak
# "429 Too Many Requests" -> nol hasil tersimpan. Untuk sistem peringatan
# dini, yang paling bernilai divalidasi adalah anomali TERBARU -- histori lama
# tidak perlu diverifikasi ulang tiap run.
MAKS_VALIDASI = 20        # maksimal query pencarian per run
JEDA_ANTAR_QUERY = 7      # detik -- di bawah ~10 request/menit (batas tier gratis)
MAKS_429_BERUNTUN = 3     # kalau tetap ditolak berkali-kali, berhenti sopan


def main():
    if not os.path.isfile(ANOMALI_PATH):
        print(f"Belum ada {ANOMALI_PATH} -- jalankan merge_and_detect.py dulu.")
        return

    import time
    import pandas as pd
    df = pd.read_csv(ANOMALI_PATH)
    anomali = df[df.get("anomali_harga", False) == True]

    if anomali.empty:
        print("Tidak ada anomali untuk divalidasi.")
        return

    # Prioritaskan anomali TERBARU, dan jangan mengulang query yang identik
    # (banyak anomali jatuh di komoditas & bulan yang sama -> 1 pencarian
    # berita cukup mewakili semuanya).
    anomali = anomali.sort_values("tanggal", ascending=False)
    total_kandidat = len(anomali)

    hasil_semua = []
    query_terpakai = set()
    beruntun_429 = 0
    for _, row in anomali.iterrows():
        if len(hasil_semua) >= MAKS_VALIDASI:
            print(f"Batas {MAKS_VALIDASI} validasi per run tercapai "
                  f"(dari {total_kandidat} kandidat) -- sisanya dilewati, "
                  f"run berikutnya akan memvalidasi anomali baru lagi.")
            break
        kunci_query = f"{row['komoditas']}|{str(row['tanggal'])[:7]}"
        if kunci_query in query_terpakai:
            continue
        print(f"Validasi: {row['tanggal']} - {row['komoditas']} - {row['kabupaten_kota']}")
        try:
            hasil = validasi_anomali(row["tanggal"], row["komoditas"], row["kabupaten_kota"])
            hasil_semua.append(hasil)
            query_terpakai.add(kunci_query)
            beruntun_429 = 0
        except Exception as e:
            print(f"  GAGAL: {e}")
            if "429" in str(e):
                beruntun_429 += 1
                if beruntun_429 >= MAKS_429_BERUNTUN:
                    print(f"{MAKS_429_BERUNTUN}x ditolak rate-limit beruntun -- "
                          "berhenti untuk run ini, hasil yang sudah ada tetap disimpan.")
                    break
        time.sleep(JEDA_ANTAR_QUERY)

    if hasil_semua:
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(hasil_semua[0].keys()))
            writer.writeheader()
            writer.writerows(hasil_semua)
        tervalidasi = sum(1 for h in hasil_semua if h["tervalidasi"])
        print(f"\n{tervalidasi}/{len(hasil_semua)} anomali menemukan berita pendukung.")
        print(f"Hasil disimpan ke {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
