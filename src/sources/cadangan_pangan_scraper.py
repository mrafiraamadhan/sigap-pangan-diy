"""
Ambil data Cadangan Pangan DIY dari Sistem Informasi Cadangan Pangan DIY
(cadanganpangan.jogjaprov.go.id, dikelola DPKP DIY). Isinya: stok cadangan
pangan yang DILAPORKAN lembaga pengelola (Gapoktan PUPM, LDPM, instansi
pemerintah) per wilayah dan per jenis komoditas.

=== KOREKSI PENTING (8 Oktober 2026) ===
Versi sebelumnya menarik halaman ini 8 kali dengan parameter ?y=2019..2026 dan
menyimpulkan "angka identik selama 8 tahun anggaran, portal tidak dirawat".
Setelah ditelusuri, dua hal ternyata keliru di sisi KITA, bukan di portal:

1. Parameter ?y= DIABAIKAN server. Halaman selalu menampilkan stok TERKINI
   ("Data per tanggal DD-Mon-YYYY"), berapa pun tahun yang diminta. Jadi
   "8 tahun identik" hanyalah satu angka yang sama dibaca 8 kali. Riwayat
   snapshot harian sejak 30 Agustus 2026 justru menunjukkan angkanya bergerak
   (mis. 251,9 → 24,0 → 116,7 → 198,7 → 880,3 → 140,4 ton).
2. Angka di portal memakai format Indonesia: titik sebagai pemisah RIBUAN
   ("140.350" = 140.350 kg = 140,35 ton). pandas membacanya sebagai 140,35
   sehingga "283,7 ton" (wajar untuk cadangan provinsi; DPKP menyebut 305 ton
   pada 2023) terbaca "283,7 kg" dan dinilai "tidak masuk akal".

Karena itu versi ini:
  * mengambil halaman SEKALI per run (snapshot terkini) dan mencatat tanggal
    data yang tertera di halaman (kolom `tanggal_data`);
  * membaca angka dengan pemisah ribuan "." dan desimal "," sehingga kolom
    "Jumlah Stok (Kg)" benar-benar dalam KILOGRAM;
  * membangun riwayat dari snapshot harian (baris baru hanya ditulis bila
    isinya berubah), bukan dari filter tahun.

Kolom keluaran dijaga sama dengan riwayat lama agar papan pantau tetap bisa
membaca keduanya:
  #, Wilayah, Jumlah Stok (Kg), tahun_filter, diambil_pada_utc,
  Jenis Komoditas, Nama, Alamat, tanggal_data

Cara pakai:
    pip install requests pandas lxml
    python cadangan_pangan_scraper.py
(opsi --tahun / --semua-tahun masih diterima supaya workflow lama tidak gagal,
 tetapi tidak lagi berpengaruh.)
"""

import argparse
import io
import os
import re
from datetime import datetime, timezone

import pandas as pd
import requests

BASE_URL = "https://cadanganpangan.jogjaprov.go.id/site/index"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "cadangan_pangan_diy.csv")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SIGAP-Pangan-DIY/1.0; research bot - "
                  "YES2026 food security paper) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

BULAN_EN = {b: i + 1 for i, b in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
BULAN_ID = {"mei": 5, "agu": 8, "agt": 8, "okt": 10, "des": 12, "jan": 1, "feb": 2, "mar": 3,
            "apr": 4, "jun": 6, "jul": 7, "sep": 9, "nov": 11}


def tanggal_data_dari(html: str):
    """Cari 'Data per tanggal 05-Oct-2026' (atau varian Indonesia) -> '2026-10-05'."""
    m = re.search(r"[Dd]ata per tanggal\s*:?\s*(\d{1,2})[-/ ]([A-Za-z]{3,9})[-/ ](\d{4})", html)
    if not m:
        m = re.search(r"(\d{1,2})[-/]([A-Za-z]{3})[-/](\d{4})", html)
        if not m:
            return None
    hari, bln, thn = int(m.group(1)), m.group(2).lower()[:3], int(m.group(3))
    b = BULAN_EN.get(bln) or BULAN_ID.get(bln)
    if not b:
        return None
    try:
        return datetime(thn, b, hari).date().isoformat()
    except ValueError:
        return None


def fetch_snapshot() -> list:
    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"GAGAL request ({str(e)[:200]})")
        return []
    html = resp.text
    try:
        # thousands="." & decimal="," : format angka Indonesia ("140.350" = 140350 kg).
        tables = pd.read_html(io.StringIO(html), thousands=".", decimal=",")
    except Exception as e:
        print(f"Gagal parse tabel -- {type(e).__name__}: {str(e)[:150]} "
              f"(HTTP {resp.status_code}, {len(html)} char)")
        return []
    if not tables:
        print(f"Tidak ada tabel di HTML (HTTP {resp.status_code}, {len(html)} char)")
        return []

    tgl_data = tanggal_data_dari(html)
    diambil = datetime.now(timezone.utc).isoformat()
    frames = []
    for df in tables:
        if df.empty or len(df.columns) < 2:
            continue
        df = df.copy()
        kol_stok = next((c for c in df.columns if "stok" in str(c).lower()), None)
        if kol_stok is not None:
            df[kol_stok] = pd.to_numeric(df[kol_stok], errors="coerce")
            df = df.rename(columns={kol_stok: "Jumlah Stok (Kg)"})
        df["tahun_filter"] = int(tgl_data[:4]) if tgl_data else datetime.now().year
        df["diambil_pada_utc"] = diambil
        df["tanggal_data"] = tgl_data or ""
        frames.append(df)
    if frames:
        tot = next((f for f in frames if "Wilayah" in f.columns), None)
        if tot is not None:
            baris_total = tot[tot["Wilayah"].astype(str).str.strip() == "Total"]
            if len(baris_total):
                kg = float(baris_total["Jumlah Stok (Kg)"].iloc[0])
                print(f"Snapshot portal per {tgl_data or '?'}: total {kg:,.0f} kg = {kg/1000:,.1f} ton")
    else:
        print("Tabel ditemukan tapi tidak ada yang terlihat seperti data asli.")
    return frames


KUNCI = ["Wilayah", "Jenis Komoditas", "Nama", "Alamat"]
NILAI = ["Jumlah Stok (Kg)", "tanggal_data", "tahun_filter"]


def _kunci(r) -> tuple:
    return tuple("" if pd.isna(r.get(c, "")) else str(r.get(c, "")).strip() for c in KUNCI)


def _nilai(r) -> tuple:
    out = []
    for c in NILAI:
        v = r.get(c, "")
        if c == "Jumlah Stok (Kg)":
            v = pd.to_numeric(v, errors="coerce")
            out.append("" if pd.isna(v) else f"{float(v):.1f}")
        else:
            out.append("" if pd.isna(v) else str(v).strip())
    return tuple(out)


def simpan_hasil(frames):
    """Simpan riwayat 'hanya perubahan': tiap baris (per wilayah / per jenis /
    per lembaga) ditulis ulang HANYA bila nilainya berbeda dari baris TERAKHIR
    dengan kunci yang sama. Dibandingkan dengan dedup global (versi lama), cara
    ini tidak kehilangan titik ketika angka kembali ke nilai lama, sehingga papan
    pantau bisa merekonstruksi keadaan terkini dengan 'nilai terakhir per kunci'."""
    if not frames:
        print("Tidak ada data untuk disimpan.")
        return
    result = pd.concat(frames, ignore_index=True)
    # Penanda bahwa baris ini sudah dalam kilogram yang benar (riwayat lama
    # dimigrasi oleh src/pipeline/migrasi_cadangan_kg.py dengan penanda yang sama).
    result["skala_kg_terkoreksi"] = True
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    if os.path.isfile(OUTPUT_PATH):
        existing = pd.read_csv(OUTPUT_PATH)
        if "tanggal_data" not in existing.columns:
            existing["tanggal_data"] = ""
        terakhir = {}
        for _, r in existing.sort_values("diambil_pada_utc").iterrows():
            terakhir[_kunci(r)] = _nilai(r)
        pilih = [i for i, r in result.iterrows() if terakhir.get(_kunci(r)) != _nilai(r)]
        baru_df = result.loc[pilih]
        gabungan = pd.concat([existing, baru_df], ignore_index=True)
        baru = len(baru_df)
        gabungan.to_csv(OUTPUT_PATH, index=False)
    else:
        result.to_csv(OUTPUT_PATH, index=False)
        baru = len(result)
    print(f"{baru} baris baru ditambahkan ke {OUTPUT_PATH} (dari {len(result)} baris hasil fetch).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tahun", type=int, default=None, help="(tidak dipakai lagi; server mengabaikan filter tahun)")
    parser.add_argument("--semua-tahun", action="store_true", help="(tidak dipakai lagi)")
    parser.parse_args()
    print("Mengambil snapshot cadangan pangan DIY...")
    simpan_hasil(fetch_snapshot())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"GAGAL total: {type(e).__name__}: {str(e)[:300]}")
        raise SystemExit(1)
