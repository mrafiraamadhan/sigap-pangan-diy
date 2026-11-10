"""
Ambil data fase pertanaman padi per KECAMATAN di DIY dari SIMOTANDI Kementan.

=== ASAL ENDPOINT (ditemukan 10 Nov 2026) ===
Dibaca langsung dari permintaan halaman resmi simotandi.pertanian.go.id/data-tabular:

  GET https://simotandi.pertanian.go.id/data-tabular            -> <select id="periode">:
      value = ID internal periode, teks = "202624 | 04 - 15 Oktober 2026"
  GET https://simotandi.pertanian.go.id/data-tabular/data?periode=<id>&provinsi=34&kabupaten=&kecamatan=
      header X-Requested-With: XMLHttpRequest -> JSON DataTables {"data": [...]}
      kolom: level, name, wadmkk, wadmkc, kdkc, bera, penyiapan_lahan, tanam_1_12_hst,
             veg_1_13_36_hst, veg_2_37_60_hst, gen_1_61_84_hst, gen_2_85_110_hst, panen,
             standing_crop, lsb (luas baku sawah)

Untuk DIY balasannya 84 baris (agregat + 78 kecamatan), angka sudah berdesimal benar --
masalah "1.43 = 1.430" pada tabel web/Excel tidak terjadi. Periode tersedia sejak
202401 (Jan 2024). SIMOTANDI terbit per 12 hari DENGAN JEDA: per 10 Nov 2026 periode
terbaru masih 202624 (4-15 Okt 2026).

=== CATATAN AKSES ===
Situs ini dilindungi Cloudflare. Dari IP pusat data (GitHub Actions) Cloudflare bisa
menyajikan halaman tantangan "Just a moment..." alih-alih data. Skrip ini MENDETEKSI
hal itu dan berhenti dengan pesan jelas tanpa mengubah riwayat. Jalur lama lewat
server ArcGIS (src/sources/simotandi_arcgis.py) menjawab 403 dari GitHub sejak Okt 2026.

Cara pakai:
    python src/sources/simotandi.py            # periode yang belum ada + 2 periode terakhir
    python src/sources/simotandi.py --semua    # isi ulang semua periode sejak 2024
Keluaran: data/simotandi_fase_tanam_diy.csv
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simotandi_dari_excel import KOLOM_FASE, KOLOM_KELUARAN, parse_periode  # noqa: E402

BASE = "https://simotandi.pertanian.go.id"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "simotandi_fase_tanam_diy.csv")
LUAS_BAKU_DIY = 67025.0
HEAD = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}
PETA = {"bera": "bera_ha", "penyiapan_lahan": "persiapan_lahan_ha", "tanam_1_12_hst": "tanam_ha",
        "veg_1_13_36_hst": "vegetatif_1_ha", "veg_2_37_60_hst": "vegetatif_2_ha",
        "gen_1_61_84_hst": "generatif_1_ha", "gen_2_85_110_hst": "generatif_2_ha", "panen": "panen_ha",
        "standing_crop": "standing_crop_ha", "lsb": "luas_baku_sawah_ha"}


def catat(*a):
    print(*a, flush=True)


def terblokir(r) -> bool:
    t = r.text[:3000].lower()
    return r.status_code in (403, 503) or "just a moment" in t or "cf-chl" in t or "challenge-platform" in t


def daftar_periode(s):
    r = s.get(f"{BASE}/data-tabular", headers=HEAD, timeout=60)
    if terblokir(r):
        raise RuntimeError(f"halaman data-tabular diblokir (HTTP {r.status_code}, kemungkinan Cloudflare)")
    blok = re.search(r'<select[^>]*id="periode".*?</select>', r.text, flags=re.S)
    if not blok:
        raise RuntimeError("daftar periode (<select id=periode>) tidak ditemukan di halaman")
    out = []
    for pid, kode, label in re.findall(r'<option[^>]*value="(\d+)"[^>]*>\s*(\d{6})\s*\|\s*([^<]+?)\s*</option>',
                                        blok.group(0)):
        out.append((pid, int(kode), " ".join(label.split())))
    return out


def ambil_periode(s, pid):
    r = s.get(f"{BASE}/data-tabular/data", timeout=90,
              params={"periode": pid, "provinsi": "34", "kabupaten": "", "kecamatan": ""},
              headers={**HEAD, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
                       "Referer": f"{BASE}/data-tabular"})
    if terblokir(r):
        raise RuntimeError(f"data periode diblokir (HTTP {r.status_code})")
    r.raise_for_status()
    return r.json().get("data") or []


def ke_baris(rows, kode, label, diambil):
    kec = [x for x in rows if str(x.get("level", "")).lower().startswith("kec")]
    if not kec:  # cadangan bila penamaan level berubah: baris dengan kode kecamatan 6 digit & unik
        kec = [x for x in rows if len(str(x.get("kdkc") or "")) == 6]
    mulai, selesai = parse_periode(label)
    out = []
    for x in kec:
        b = {"periode": label, "periode_kode": kode, "periode_mulai": mulai.isoformat(),
             "periode_selesai": selesai.isoformat(), "provinsi": "Daerah Istimewa Yogyakarta",
             "kabupaten_kota": str(x.get("wadmkk", "")).strip(), "kecamatan": str(x.get("wadmkc", "")).strip(),
             "kode_wilayah": str(x.get("kdkc", "")).strip(),
             "sumber_layer": "simotandi.pertanian.go.id/data-tabular/data (JSON)", "diambil_pada_utc": diambil}
        for k_api, k_kita in PETA.items():
            b[k_kita] = round(float(x.get(k_api) or 0), 2)
        b["total_luas_ha"] = round(sum(b[k] for k in KOLOM_FASE), 2)
        out.append(b)
    return out, {str(x.get("level")) for x in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--semua", action="store_true", help="isi ulang semua periode sejak 2024")
    ap.add_argument("--daftar-layer", action="store_true", help="(kompatibilitas versi lama; diabaikan)")
    a = ap.parse_args()

    lama = pd.read_csv(OUTPUT_PATH) if os.path.exists(OUTPUT_PATH) else pd.DataFrame(columns=KOLOM_KELUARAN)
    sudah = set(lama.periode_kode.astype(int)) if len(lama) else set()
    s = requests.Session()
    try:
        periode = daftar_periode(s)
    except Exception as e:
        catat(f"SIMOTANDI GAGAL: {e}. Riwayat TIDAK diubah.")
        return 0
    if not periode:
        catat("SIMOTANDI: daftar periode kosong. Riwayat TIDAK diubah.")
        return 0
    terbaru = sorted(periode, key=lambda p: p[1])
    catat(f"SIMOTANDI: {len(periode)} periode tersedia, {terbaru[0][1]} s.d. {terbaru[-1][1]} "
          f"(terbaru: {terbaru[-1][2]})")
    target = terbaru if a.semua else [p for p in terbaru if p[1] not in sudah] + \
        [p for p in terbaru[-2:] if p[1] in sudah]
    if not target:
        catat("Tidak ada periode baru.")
        return 0

    diambil = datetime.now(timezone.utc).isoformat(timespec="seconds")
    baru = []
    for pid, kode, label in target:
        try:
            rows = ambil_periode(s, pid)
            b, level = ke_baris(rows, kode, label, diambil)
        except Exception as e:
            catat(f"  {kode} {label}: GAGAL {type(e).__name__}: {str(e)[:150]}")
            continue
        lsb = sum(x["luas_baku_sawah_ha"] for x in b)
        ok = 70 <= len(b) <= 90 and abs(lsb - LUAS_BAKU_DIY) / LUAS_BAKU_DIY < 0.03
        catat(f"  {kode} {label}: {len(b)} kecamatan, luas baku {lsb:,.0f} ha, level={sorted(level)}"
              + ("" if ok else "  -> DITOLAK (jumlah kecamatan/luas baku tidak wajar)"))
        if ok:
            baru.extend(b)
        time.sleep(1)
    if not baru:
        catat("Tidak ada periode yang lolos. Riwayat TIDAK diubah.")
        return 0

    df = pd.DataFrame(baru)[KOLOM_KELUARAN]
    if len(lama):
        # Periode yang sudah ada hanya diganti bila ada fase yang berubah > 1 ha (riwayat
        # lama sebagian dari tabel web yang dibulatkan ke ha). Urutan baris lama dipertahankan
        # dan periode baru ditambahkan di akhir, supaya berkas tidak berubah tanpa alasan.
        lama["kode_wilayah"] = lama["kode_wilayah"].astype(str)
        ganti = []
        for kode, g in df.groupby("periode_kode"):
            g_lama = lama[lama.periode_kode.astype(int) == int(kode)]
            if g_lama.empty:
                ganti.append(kode)
                continue
            m = g_lama.merge(g, on="kode_wilayah", suffixes=("_lama", "_baru"))
            maks = max((m[f"{k}_lama"] - m[f"{k}_baru"]).abs().max() for k in KOLOM_FASE) if len(m) else 999
            catat(f"  {kode}: sudah ada, selisih fase maks {maks:.1f} ha"
                  + (" -> diganti" if maks > 1 or len(m) != len(g) else " -> dibiarkan"))
            if maks > 1 or len(m) != len(g):
                ganti.append(kode)
        if not ganti:
            catat("Tidak ada periode baru atau berubah; berkas tidak ditulis ulang.")
            return 0
        df = df[df.periode_kode.isin(ganti)]
        gab = pd.concat([lama[~lama.periode_kode.astype(int).isin([int(k) for k in ganti])], df], ignore_index=True)
    else:
        gab = df
    gab.to_csv(OUTPUT_PATH, index=False)
    catat(f"Tersimpan {OUTPUT_PATH}: {len(gab)} baris, {gab.periode_kode.nunique()} periode "
          f"({int(gab.periode_kode.min())} s.d. {int(gab.periode_kode.max())})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
