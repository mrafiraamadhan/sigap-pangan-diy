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
    # Bentuk respons Firecrawl /v1/search TERBUKTI (run 30 Agt 2026, setelah
    # rate-limit teratasi): {"success": true, "data": [ ...daftar hasil... ]}
    # -- "data" adalah LIST langsung. Kode lama mengira "data" adalah dict
    # berisi kunci "web", sehingga SEMUA query gagal dengan
    # "'list' object has no attribute 'get'". Penanganan di bawah menerima
    # kedua bentuk (list langsung, maupun dict {"web": [...]}), supaya tahan
    # kalau Firecrawl mengubah format lagi.
    isi = data.get("data") if isinstance(data, dict) else data
    if isinstance(isi, dict):
        isi = isi.get("web") or isi.get("results") or []
    return isi if isinstance(isi, list) else []


def validasi_anomali(tanggal: str, komoditas: str, kabupaten: str) -> dict:
    """Bangun query pencarian dari konteks anomali, kembalikan hasil berita
    teratas sebagai bukti pendukung (atau list kosong kalau tidak ada)."""
    bulan_tahun = tanggal[:7]  # 'YYYY-MM'
    query = f"harga {komoditas} {kabupaten} {bulan_tahun} naik turun"
    hasil = cari_berita(query, limit=3)
    teratas = hasil[0] if hasil else {}
    # "description" = cuplikan/ringkasan singkat artikel dari hasil pencarian
    # Firecrawl -- dipakai dashboard untuk menampilkan kliping berita yang
    # layak baca (judul + media + ringkasan), bukan sekadar tautan.
    ringkasan = str(teratas.get("description") or teratas.get("snippet") or "").strip()
    return {
        "tanggal": tanggal,
        "komoditas": komoditas,
        "kabupaten_kota": kabupaten,
        "query": query,
        "jumlah_berita_ditemukan": len(hasil),
        "judul_teratas": teratas.get("title", ""),
        "url_teratas": teratas.get("url", ""),
        "ringkasan": ringkasan[:400],
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

    # Kliping yang SUDAH terkumpul dari run-run sebelumnya. Versi lama membuka
    # berkas keluaran dengan mode "w", jadi tiap run menimpa habis isinya dan
    # jumlah kliping mentok di MAKS_VALIDASI selamanya. Lebih buruk lagi:
    # karena urutannya selalu dari anomali terbaru, kuota pencarian habis untuk
    # MENGULANG komoditas-bulan yang sudah divalidasi kemarin, bukan menambah
    # cakupan. Sekarang hasil lama dibaca dulu, lalu ditumpuk.
    lama = []
    if os.path.isfile(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, newline="", encoding="utf-8") as f:
                lama = list(csv.DictReader(f))
            print(f"{len(lama)} kliping berita sudah terkumpul dari run sebelumnya.")
        except Exception as e:
            print(f"Kliping lama tidak terbaca ({type(e).__name__}), mulai dari kosong.")

    sudah = {f"{r.get('komoditas')}|{str(r.get('tanggal'))[:7]}" for r in lama}

    hasil_semua = []
    query_terpakai = set(sudah)      # jangan ulang yang sudah punya kliping
    beruntun_429 = 0
    percobaan = 0
    for _, row in anomali.iterrows():
        # PENTING: batas dihitung dari PERCOBAAN, bukan dari yang berhasil.
        # Versi sebelumnya menghitung len(hasil_semua) -- kalau semua query
        # gagal (mis. format respons berubah), hitungan tak pernah naik dan
        # loop menggiling SELURUH ratusan anomali x 7 detik (~40 menit sia-sia
        # di run 30 Agt 2026). Sekarang: maksimal MAKS_VALIDASI percobaan,
        # titik, apapun hasilnya.
        if percobaan >= MAKS_VALIDASI:
            print(f"Batas {MAKS_VALIDASI} percobaan validasi per run tercapai "
                  f"(dari {total_kandidat} kandidat) -- sisanya dilewati, "
                  f"run berikutnya akan memvalidasi anomali baru lagi.")
            break
        kunci_query = f"{row['komoditas']}|{str(row['tanggal'])[:7]}"
        if kunci_query in query_terpakai:
            continue
        percobaan += 1
        query_terpakai.add(kunci_query)
        print(f"Validasi: {row['tanggal']} - {row['komoditas']} - {row['kabupaten_kota']}")
        try:
            hasil = validasi_anomali(row["tanggal"], row["komoditas"], row["kabupaten_kota"])
            hasil_semua.append(hasil)
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

    if not hasil_semua:
        print("\nTidak ada kliping baru pada run ini; berkas lama dibiarkan apa adanya.")
        return

    # Arsip lama dipertahankan APA ADANYA; hasil baru hanya ditambahkan.
    #
    # Versi sebelumnya menyaring gabungan lama+baru dengan kunci komoditas+bulan,
    # sehingga baris lama yang kebetulan sekunci ikut terbuang. Akibatnya arsip
    # MENYUSUT: 20 baris jadi 17 pada run 31 Agt, karena run itu cuma berhasil
    # menambah sedikit (kuota Firecrawl gratis membatasi) tetapi membuang lebih
    # banyak. Arsip kliping tidak boleh mengecil karena alasan apa pun.
    kolom = list(hasil_semua[0].keys())
    kunci_lama = {f"{r.get('komoditas')}|{str(r.get('tanggal'))[:7]}" for r in lama}
    tambahan = [r for r in hasil_semua
                if f"{r.get('komoditas')}|{str(r.get('tanggal'))[:7]}" not in kunci_lama]
    gabung = [{c: r.get(c, "") for c in kolom} for r in (tambahan + lama)]
    gabung.sort(key=lambda r: str(r.get("tanggal", "")), reverse=True)
    assert len(gabung) >= len(lama), "arsip kliping tidak boleh menyusut"

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=kolom)
        writer.writeheader()
        writer.writerows(gabung)

    tervalidasi = sum(1 for h in hasil_semua if h["tervalidasi"])
    print(f"\n{tervalidasi}/{len(hasil_semua)} anomali baru menemukan berita pendukung.")
    print(f"Arsip kliping: {len(lama)} + {len(tambahan)} baru = {len(gabung)} baris, "
          f"disimpan ke {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
