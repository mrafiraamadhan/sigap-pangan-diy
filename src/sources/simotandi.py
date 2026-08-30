"""
Ambil data fase pertanaman padi per KECAMATAN di DIY dari SIMOTANDI Kementan.

=== KENAPA VERSI INI BEDA TOTAL DARI VERSI SEBELUMNYA ===

Versi lama menempuh jalur situs web SIMOTANDI (simotandi.pertanian.go.id):
buka halaman -> ambil CSRF token -> POST minta "export Excel" -> poll status
job -> unduh .xlsx. Jalur itu GAGAL TERUS di GitHub Actions karena situsnya
dilindungi Cloudflare: dari IP datacenter, yang disajikan bukan halaman asli
melainkan halaman tantangan JS ("Just a moment..."), sehingga csrf-token tidak
pernah ketemu dan seluruh alur mati di langkah pertama. Segala trik stealth
browser cuma menunda masalah, bukan menyelesaikannya.

Versi ini memakai SUMBER YANG SAMA tapi lewat PINTU YANG BERBEDA: server
ArcGIS milik Kementan yang menyimpan data aslinya, di host `sig02.pertanian.go.id`.
Server ini:
  - TIDAK di belakang Cloudflare (tidak ada tantangan JS sama sekali)
  - TIDAK butuh sesi, cookie, CSRF token, maupun login
  - TIDAK butuh Playwright/browser (cukup `requests` -> jauh lebih cepat & ringan)
  - Mengembalikan JSON rapi lewat ArcGIS REST API standar

Semua ini sudah DIKONFIRMASI langsung pada 30 Agt 2026 dengan menarik data
sungguhan (mis. 12 kecamatan Kulon Progo periode 5-16 Agustus 2026 keluar
lengkap dengan angka luas per fase).

=== STRUKTUR SUMBER ===

Ada 2 "MapServer" yang relevan, dua-duanya di bawah folder `simotandi`:
  1. simotandi/simotandi_sentinel1  -> memuat periode TERBARU
  2. simotandi/simontadi2            -> memuat beberapa periode sebelumnya
(ejaan "simontadi2" memang typo di sisi Kementan, jangan "dibetulkan")

Di dalam tiap MapServer ada banyak layer. Sebagian besar layer adalah RASTER
peta fase tanam ("Fase Pertanaman Padi ...", fields = null, tidak ada angka
yang bisa diambil). Yang kita mau HANYA layer yang namanya diawali
"Data Tabular Periode ..." -- layer inilah yang berisi tabel agregat per
kecamatan. Karena ID layer & daftar periodenya BERUBAH tiap kali Kementan
menerbitkan periode baru, script ini TIDAK meng-hardcode ID layer: ia selalu
membaca dulu daftar layer, lalu menyaring yang namanya "Data Tabular".

Field pada layer Data Tabular (dikonfirmasi lewat metadata layer):
  WADMPR   = nama provinsi      (mis. "Daerah Istimewa Yogyakarta")
  WADMKK   = nama kabupaten/kota (mis. "Kulon Progo", "Sleman")
  WADMKC   = nama kecamatan      (mis. "Wates", "Godean")
  KDCPUM   = kode wilayah
  Bera     = luas lahan bera / tidak ditanami   (hektar)
  P_Lahan  = luas fase persiapan lahan          (hektar)
  Tanam    = luas fase tanam                    (hektar)
  Veg_1    = luas fase vegetatif 1              (hektar)
  Veg_2    = luas fase vegetatif 2              (hektar)
  Gen_1    = luas fase generatif 1              (hektar)
  Gen_2    = luas fase generatif 2              (hektar)
  Panen    = luas fase panen                    (hektar)

CATATAN PENTING soal filter provinsi: nilai WADMPR ditulis Title Case
("Daerah Istimewa Yogyakarta"), dan klausa WHERE di ArcGIS ini
CASE-SENSITIVE. Menulis LIKE '%YOGYAKARTA%' (huruf besar) mengembalikan 0
baris -- sempat bikin salah sangka bahwa DIY tidak ada datanya. Karena itu
filternya memakai '%Yogya%' persis seperti di bawah. Jangan diubah jadi
huruf besar.

Batas teknis: server ini ArcGIS 10.51 yang TIDAK mendukung paginasi
(`resultRecordCount` ditolak dengan "Pagination is not supported"), dengan
maxRecordCount 1000 baris per query. DIY hanya 78 kecamatan, jadi satu query
per periode sudah pasti muat -- tidak perlu paginasi sama sekali.

Cara pakai:
    pip install requests pandas
    python simotandi.py                  # ambil semua periode yang tersedia
    python simotandi.py --daftar-layer   # diagnostik: lihat layer apa saja yg ada
"""

import argparse
import os
import re
from datetime import datetime, timezone

import pandas as pd
import requests

BASE_ARCGIS = "https://sig02.pertanian.go.id/server/rest/services/simotandi"

# Urutan sengaja: sentinel1 dulu (periode terbaru), lalu simontadi2 (periode
# lama). Ejaan "simontadi2" memang begitu di servernya -- bukan salah ketik kita.
SERVICES = [
    "simotandi_sentinel1",
    "simontadi2",
]

# WAJIB Title Case -- WHERE di ArcGIS ini case-sensitive (lihat catatan di atas)
FILTER_PROVINSI = "WADMPR LIKE '%Yogya%'"

KOLOM_FASE = ["Bera", "P_Lahan", "Tanam", "Veg_1", "Veg_2", "Gen_1", "Gen_2", "Panen"]

NAMA_FASE_RAPI = {
    "Bera": "bera_ha",
    "P_Lahan": "persiapan_lahan_ha",
    "Tanam": "tanam_ha",
    "Veg_1": "vegetatif_1_ha",
    "Veg_2": "vegetatif_2_ha",
    "Gen_1": "generatif_1_ha",
    "Gen_2": "generatif_2_ha",
    "Panen": "panen_ha",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SIGAP-Pangan-DIY/1.0; research bot - "
                  "YES2026 food security paper) AppleWebKit/537.36",
    "Accept": "application/json,text/plain,*/*",
}

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "simotandi_fase_tanam_diy.csv")

TIMEOUT = 60

BULAN_ID = {
    "januari": 1, "februari": 2, "febuari": 2, "maret": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "agustus": 8, "september": 9,
    "oktober": 10, "november": 11, "nopember": 11, "desember": 12,
}


def parse_periode(nama_layer: str):
    """Ubah nama layer jadi (label, tanggal_mulai, tanggal_selesai).

    Nama layer punya beberapa bentuk yang harus ditangani semua:
        "Data Tabular Periode 5 - 16 Agustus 2026"          -> 1 bulan, 1 tahun
        "Data Tabular Periode 26 Maret - 6 April 2026"      -> beda bulan, 1 tahun
        "Data Tabular Periode 28 Desember 2025 - 8 Januari 2026" -> beda tahun

    Kalau formatnya tidak dikenali, kembalikan tanggal None saja (baris data
    tetap disimpan dengan labelnya) -- lebih baik data masuk tanpa tanggal
    daripada seluruh periode dibuang cuma karena penamaan berubah sedikit.
    """
    label = re.sub(r"^Data Tabular Periode\s*", "", nama_layer).strip()
    label = re.sub(r"\.tif$", "", label).strip()

    teks = label.lower().replace("–", "-").replace("—", "-")
    if "-" not in teks:
        return label, None, None

    kiri, kanan = teks.split("-", 1)
    kiri, kanan = kiri.strip(), kanan.strip()

    def pecah(bagian):
        m = re.match(r"^(\d{1,2})\s*([a-z]+)?\s*(\d{4})?$", bagian.strip())
        if not m:
            return None, None, None
        hari = int(m.group(1))
        bulan = BULAN_ID.get(m.group(2)) if m.group(2) else None
        tahun = int(m.group(3)) if m.group(3) else None
        return hari, bulan, tahun

    h1, b1, t1 = pecah(kiri)
    h2, b2, t2 = pecah(kanan)
    if h1 is None or h2 is None:
        return label, None, None

    # Sisi kanan selalu paling lengkap -> jadi acuan untuk mengisi sisi kiri
    b1 = b1 or b2
    t1 = t1 or t2
    t2 = t2 or t1
    if not (b1 and b2 and t1 and t2):
        return label, None, None

    try:
        mulai = datetime(t1, b1, h1).date().isoformat()
        selesai = datetime(t2, b2, h2).date().isoformat()
    except ValueError:
        return label, None, None

    return label, mulai, selesai


def daftar_layer(service: str):
    """Baca daftar layer sebuah MapServer. ID layer TIDAK di-hardcode karena
    berubah tiap Kementan menerbitkan periode baru."""
    url = f"{BASE_ARCGIS}/{service}/MapServer"
    resp = requests.get(url, params={"f": "json"}, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS menolak: {data['error']}")
    return data.get("layers", [])


def ambil_layer_tabular(service: str, layer_id: int):
    """Tarik baris DIY dari satu layer Data Tabular.

    returnGeometry=false itu penting: tanpa itu, tiap baris ikut membawa
    poligon batas kecamatan (ratusan KB per baris) -- lambat & tidak kita pakai.
    """
    url = f"{BASE_ARCGIS}/{service}/MapServer/{layer_id}/query"
    params = {
        "where": FILTER_PROVINSI,
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS menolak query: {data['error']}")
    return [f.get("attributes", {}) for f in data.get("features", [])]


def ambil_data() -> pd.DataFrame:
    semua = []
    diambil_pada = datetime.now(timezone.utc).isoformat()
    sudah_diambil = set()   # anti-dobel: layer periode yang sama muncul di 2 service

    for service in SERVICES:
        try:
            layers = daftar_layer(service)
        except Exception as e:
            print(f"[{service}] GAGAL baca daftar layer: {type(e).__name__}: {str(e)[:200]}")
            continue

        tabular = [l for l in layers
                   if str(l.get("name", "")).strip().lower().startswith("data tabular")]
        print(f"[{service}] {len(layers)} layer, {len(tabular)} di antaranya Data Tabular.")

        for l in tabular:
            nama = str(l.get("name", ""))
            label, mulai, selesai = parse_periode(nama)

            if label in sudah_diambil:
                print(f"  - '{label}' sudah diambil dari service lain -- dilewati.")
                continue

            try:
                baris = ambil_layer_tabular(service, l["id"])
            except Exception as e:
                print(f"  - '{label}' GAGAL: {type(e).__name__}: {str(e)[:200]}")
                continue

            if not baris:
                print(f"  - '{label}': 0 baris DIY (layer mungkin belum lengkap terisi).")
                continue

            df = pd.DataFrame(baris)
            df = df.rename(columns={
                "WADMPR": "provinsi",
                "WADMKK": "kabupaten_kota",
                "WADMKC": "kecamatan",
                "KDCPUM": "kode_wilayah",
                **NAMA_FASE_RAPI,
            })

            kolom_fase_ada = [NAMA_FASE_RAPI[k] for k in KOLOM_FASE
                              if NAMA_FASE_RAPI[k] in df.columns]
            for k in kolom_fase_ada:
                df[k] = pd.to_numeric(df[k], errors="coerce")

            # Total luas sawah terpantau = jumlah semua fase. Berguna sebagai
            # penyebut waktu menghitung proporsi tiap fase per kecamatan.
            if kolom_fase_ada:
                df["total_luas_ha"] = df[kolom_fase_ada].sum(axis=1)

            df["periode"] = label
            df["periode_mulai"] = mulai
            df["periode_selesai"] = selesai
            df["sumber_layer"] = f"{service}/{l['id']}"
            df["diambil_pada_utc"] = diambil_pada

            kolom_inti = ["periode", "periode_mulai", "periode_selesai", "provinsi",
                          "kabupaten_kota", "kecamatan", "kode_wilayah"]
            urutan = ([c for c in kolom_inti if c in df.columns]
                      + kolom_fase_ada
                      + [c for c in ["total_luas_ha", "sumber_layer", "diambil_pada_utc"]
                         if c in df.columns])
            df = df[urutan]

            semua.append(df)
            sudah_diambil.add(label)
            print(f"  - '{label}': {len(df)} kecamatan DIY berhasil diambil.")

    if not semua:
        return pd.DataFrame()

    return pd.concat(semua, ignore_index=True)


def simpan(df: pd.DataFrame):
    """Simpan dengan dedup. Pipeline jalan berkali-kali seminggu sementara
    periode SIMOTANDI cuma berganti ~12 hari sekali, jadi tanpa dedup baris
    yang sama persis akan menumpuk terus tiap run."""
    if df.empty:
        print("Tidak ada data SIMOTANDI untuk disimpan pada run ini.")
        return

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    if os.path.isfile(OUTPUT_PATH):
        lama = pd.read_csv(OUTPUT_PATH)
        gabungan = pd.concat([lama, df], ignore_index=True)
        # Kunci identitas satu observasi = periode + kecamatan. Kolom waktu
        # pengambilan sengaja tidak ikut dibandingkan (pasti beda tiap run).
        kunci = [c for c in ["periode", "kabupaten_kota", "kecamatan"] if c in gabungan.columns]
        gabungan = gabungan[~gabungan[kunci].astype(str).duplicated(keep="first")]
        baru = len(gabungan) - len(lama)
        gabungan.to_csv(OUTPUT_PATH, index=False)
    else:
        df.to_csv(OUTPUT_PATH, index=False)
        baru = len(df)

    print(f"\n{baru} baris baru ditambahkan ke {OUTPUT_PATH} "
          f"(dari {len(df)} baris hasil tarik).")
    print("Sumber: SIMOTANDI Kementerian Pertanian RI (ArcGIS REST) -- "
          f"{BASE_ARCGIS}")


def mode_daftar_layer():
    """Diagnostik: cetak semua layer di kedua service. Pakai ini kalau suatu
    saat penamaan layer berubah & penyaring 'Data Tabular' berhenti cocok."""
    for service in SERVICES:
        print(f"\n=== {service} ===")
        try:
            for l in daftar_layer(service):
                tanda = "<-- TABULAR" if str(l.get("name", "")).lower().startswith("data tabular") else ""
                print(f"  id={l.get('id'):>3}  {l.get('name')} {tanda}")
        except Exception as e:
            print(f"  GAGAL: {type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--daftar-layer", action="store_true",
                        help="Diagnostik: cetak daftar layer di server ArcGIS "
                             "SIMOTANDI (pakai kalau penamaan layer berubah).")
    args = parser.parse_args()

    try:
        if args.daftar_layer:
            mode_daftar_layer()
        else:
            simpan(ambil_data())
    except Exception as e:
        print(f"GAGAL total: {type(e).__name__}: {str(e)[:300]}")
        raise SystemExit(1)
