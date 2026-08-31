"""
Penarik harga pangan SP2KP Kementerian Perdagangan untuk DIY.

=== KENAPA SUMBER INI PENTING ===
SP2KP adalah basis resmi penghitungan Indeks Perkembangan Harga (IPH) yang dipakai
Kemendag dan dirujuk dalam rapat pengendalian inflasi. Dibandingkan dua sumber lain
yang sudah ditarik pipeline ini, SP2KP unggul pada tiga hal:

  1. Kepadatan waktu. Harga berubah pada 6,3% hari kalender, dibanding DPKP 5,5%
     dan PIHPS hanya 2,8%. Perubahannya juga menyebar sepanjang bulan, sedangkan
     PIHPS hanya berubah pada tanggal 25 sampai 27.
  2. Rincian sampai titik pasar. Satu baris = satu tanggal x satu pasar x satu
     varian, sehingga sebaran harga ANTAR PASAR dapat dihitung. Sebaran itu
     dipakai sebagai penskala volatilitas pada modul prakiraan.
  3. Rincian varian. 84 varian, jauh lebih halus daripada 20 deret PIHPS.

=== CATATAN JUJUR TENTANG KODE INI ===
Endpoint di bawah diambil dari dokumentasi berkas unduhan resmi SP2KP. Nama
PARAMETER kueri tidak dapat diverifikasi dari lingkungan pengembangan (domain
kemendag.go.id diblokir dari sana), sehingga modul ini dirancang untuk
MENEMUKAN SENDIRI bentuk balasan API saat pertama kali berjalan:

  - mencoba beberapa kemungkinan nama parameter sampai ada yang mengembalikan data
  - mengenali bentuk balasan (list polos, {"data": [...]}, atau {"result": [...]})
  - memetakan nama kolom secara lentur (tanggal/date/tgl, harga/price/nilai, dst.)
  - MENCETAK satu record mentah ke log bila pemetaan gagal, supaya bentuk aslinya
    dapat dibaca dan modul ini diperbaiki tanpa menebak lagi

Jadi bila run pertama gagal, lihat log langkah ini: di sana tercetak contoh
balasan mentah yang dibutuhkan untuk membetulkan pemetaan.

Keluaran: data/harga_sp2kp_diy.csv
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

BASE = "https://api-sp2kp.kemendag.go.id"
EP_HARGA = f"{BASE}/report/api/average-price-public"
EP_PASAR = f"{BASE}/master/api/pasar"
EP_KOMODITAS = f"{BASE}/master/api/komoditas"

KODE_PROVINSI = "34"                                  # DI Yogyakarta
KODE_KABKOT = ["3401", "3402", "3403", "3404", "3471"]

KELUARAN = os.path.join("data", "harga_sp2kp_diy.csv")

# Peramban sungguhan. Beberapa portal kementerian menolak User-Agent yang
# menyebut kata "bot" atau "scraper" (pelajaran dari SIMOTANDI).
HEAD = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": "https://sp2kp.kemendag.go.id/",
    "Origin": "https://sp2kp.kemendag.go.id",
}

# Kemungkinan nama parameter tanggal & wilayah, dicoba berurutan.
POLA_PARAM = [
    {"tgl_awal": "start_date", "tgl_akhir": "end_date", "prov": "province_code", "kab": "regency_code"},
    {"tgl_awal": "tanggal_awal", "tgl_akhir": "tanggal_akhir", "prov": "kode_provinsi", "kab": "kode_kabkot"},
    {"tgl_awal": "startDate", "tgl_akhir": "endDate", "prov": "provinceId", "kab": "regencyId"},
    {"tgl_awal": "from", "tgl_akhir": "to", "prov": "provinsi", "kab": "kabkot"},
    {"tgl_awal": "date_start", "tgl_akhir": "date_end", "prov": "prov_id", "kab": "kab_id"},
]

# Pemetaan lentur nama kolom balasan -> nama kolom keluaran kita.
SINONIM = {
    "tanggal":       ["tanggal", "date", "tgl", "tanggal_harga", "price_date", "periode"],
    "kabupaten_kota":["kabupaten_kota", "kabupaten", "kab_kota", "regency", "regency_name", "nama_kabkot", "kabkot"],
    "pasar":         ["nama_pasar", "pasar", "market", "market_name", "nama_market"],
    "komoditas":     ["komoditas", "commodity", "nama_komoditas", "commodity_name"],
    "varian":        ["varian", "variant", "nama_varian", "variant_name", "jenis"],
    "satuan":        ["satuan", "unit", "uom"],
    "harga":         ["harga", "price", "average_price", "harga_rata_rata", "nilai", "avg_price"],
}


def catat(*a):
    print(*a, flush=True)


def ambil(url, params=None, percobaan=3):
    """GET dengan jeda bertambah. Mengembalikan objek JSON atau None."""
    for i in range(percobaan):
        try:
            r = requests.get(url, params=params, headers=HEAD, timeout=60)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    catat(f"    balasan 200 tetapi bukan JSON: {r.text[:200]}")
                    return None
            catat(f"    HTTP {r.status_code} untuk {r.url[:150]}")
            if r.status_code in (401, 403):
                catat(f"    isi balasan: {r.text[:300]}")
                return None                      # ditolak, tidak perlu diulang
        except requests.RequestException as e:
            catat(f"    galat jaringan: {type(e).__name__}: {str(e)[:160]}")
        time.sleep(3 * (i + 1))
    return None


def daftar_dari(obj):
    """Cari list record di dalam balasan yang bentuknya bermacam-macam."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for kunci in ("data", "result", "results", "items", "rows", "records", "content"):
            v = obj.get(kunci)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):                       # mis. {"data": {"rows": [...]}}
                dalam = daftar_dari(v)
                if dalam:
                    return dalam
        for v in obj.values():                            # usaha terakhir
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def petakan(rec):
    """Petakan satu record ke kolom baku kita. None bila tidak dikenali."""
    kecil = {str(k).lower().strip(): v for k, v in rec.items()}
    out = {}
    for baku, kandidat in SINONIM.items():
        for c in kandidat:
            if c in kecil and kecil[c] not in (None, ""):
                out[baku] = kecil[c]
                break
    return out if ("tanggal" in out and "harga" in out) else None


def tarik_rentang(tgl_awal, tgl_akhir):
    """Coba tiap pola parameter sampai ada yang mengembalikan record."""
    for i, p in enumerate(POLA_PARAM, 1):
        params = {
            p["tgl_awal"]: tgl_awal.isoformat(),
            p["tgl_akhir"]: tgl_akhir.isoformat(),
            p["prov"]: KODE_PROVINSI,
        }
        catat(f"  pola parameter {i}/{len(POLA_PARAM)}: {list(params)}")
        js = ambil(EP_HARGA, params)
        rows = daftar_dari(js) if js else []
        if rows:
            catat(f"    -> {len(rows)} record diterima")
            return rows, p
    return [], None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hari-mundur", type=int, default=30,
                    help="tarik berapa hari ke belakang (isi mis. 900 untuk mengisi histori)")
    ap.add_argument("--potong-hari", type=int, default=90,
                    help="ukuran potongan permintaan agar tidak menembus batas API")
    args = ap.parse_args()

    akhir = date.today()
    awal = akhir - timedelta(days=args.hari_mundur)
    catat(f"SP2KP: menarik {awal} s.d. {akhir} ({args.hari_mundur} hari)")

    semua, pola = [], None
    t = awal
    while t <= akhir:
        t2 = min(t + timedelta(days=args.potong_hari - 1), akhir)
        catat(f"  potongan {t} s.d. {t2}")
        if pola is None:
            rows, pola = tarik_rentang(t, t2)
        else:
            params = {pola["tgl_awal"]: t.isoformat(), pola["tgl_akhir"]: t2.isoformat(),
                      pola["prov"]: KODE_PROVINSI}
            rows = daftar_dari(ambil(EP_HARGA, params) or {})
            catat(f"    -> {len(rows)} record")
        semua.extend(rows)
        t = t2 + timedelta(days=1)
        time.sleep(2)

    if not semua:
        catat("TIDAK ADA DATA. Kemungkinan penyebab: nama parameter berbeda, "
              "API menuntut kunci akses, atau alamat IP runner diblokir.")
        catat("Langkah ini sengaja TIDAK menggagalkan pipeline. Pilar lain tetap jalan.")
        return 0

    catat(f"Total {len(semua)} record mentah.")
    catat("Contoh record mentah (untuk memperbaiki pemetaan bila perlu):")
    catat("  " + json.dumps(semua[0], ensure_ascii=False)[:600])

    baris = [b for b in (petakan(r) for r in semua) if b]
    if not baris:
        catat("PEMETAAN GAGAL: tidak ada record yang punya kolom tanggal + harga.")
        catat(f"  kunci yang tersedia: {sorted(semua[0].keys())}")
        return 0

    df = pd.DataFrame(baris)
    df["tanggal"] = pd.to_datetime(df["tanggal"], errors="coerce").dt.date
    df["harga"] = pd.to_numeric(df["harga"], errors="coerce")
    df = df.dropna(subset=["tanggal", "harga"]).query("harga > 0")
    df["diambil_pada_utc"] = datetime.now(timezone.utc).isoformat()

    kunci = [k for k in ("tanggal", "pasar", "komoditas", "varian") if k in df.columns]
    os.makedirs("data", exist_ok=True)
    if os.path.exists(KELUARAN):
        lama = pd.read_csv(KELUARAN)
        lama["tanggal"] = pd.to_datetime(lama["tanggal"], errors="coerce").dt.date
        df = pd.concat([lama, df], ignore_index=True)
    df = df.drop_duplicates(subset=kunci, keep="last").sort_values(kunci)
    df.to_csv(KELUARAN, index=False)

    catat(f"Tersimpan {KELUARAN}: {len(df)} baris, "
          f"{df['komoditas'].nunique() if 'komoditas' in df else '?'} komoditas, "
          f"{df.tanggal.min()} s.d. {df.tanggal.max()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
