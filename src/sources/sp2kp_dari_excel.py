"""
Masukkan berkas Excel "Statistik Harian" hasil unduhan MANUAL dari portal SP2KP
Kementerian Perdagangan (sp2kp.kemendag.go.id -> Statistik Harian -> pilih
provinsi DI Yogyakarta, kabupaten/kota, pasar, rentang tanggal -> unduh) ke
data/harga_sp2kp_diy.csv.

Dipakai karena API SP2KP tidak melayani parameter tanggal/wilayah yang kami kirim
(lihat sp2kp_kemendag.py), sedangkan unduhan dari peramban lancar. Riwayat
Feb 2024 - Agu 2026 di berkas CSV berasal dari unduhan yang sama.

Bentuk berkas yang dikenali: satu berkas = satu pasar, lembar "Average Prices":
  baris 0 : "LAPORAN HARGA HARIAN Pasar X, Kab. Y, Daerah Istimewa Yogyakarta"
  baris 1 : "PERIODE: 2026-08-27 s/d 2026-09-26"
  baris 3 : header  Variant | Quantity | Unit | <tanggal> | <tanggal> | ...
  baris 4+: satu baris per varian, harga per tanggal (hanya hari pencatatan)
  baris terakhir: "Sumber Data : SP2KP Kementerian Perdagangan 2026"

Kolom keluaran mengikuti riwayat persis:
  tanggal,kabupaten_kota,pasar,komoditas,varian,satuan,harga,diambil_pada_utc
Nama komoditas (kelompok) diambil dari riwayat berdasarkan nama varian, supaya
ejaannya sama dan deret prakiraan tetap nyambung.

Cara pakai:
    python src/sources/sp2kp_dari_excel.py "folder_unduhan/Statistik Harian*.xlsx"
Lanjutkan dengan:
    python src/pipeline/ringkas_sp2kp.py      # versi ringkas untuk papan pantau
    python src/pipeline/forecast_harga.py     # prakiraan memakai data baru
"""

import glob
import os
import re
import sys
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
KELUARAN = os.path.join(HERE, "..", "..", "data", "harga_sp2kp_diy.csv")
KOLOM = ["tanggal", "kabupaten_kota", "pasar", "komoditas", "varian", "satuan", "harga", "diambil_pada_utc"]

# Satuan di berkas unduhan vs ejaan yang dipakai riwayat.
SATUAN = {"bungkus": "bks", "ekor": "ekor", "kg": "kg", "lt": "lt", "liter": "lt"}


def satuan_rapi(u):
    u = str(u or "").strip()
    return SATUAN.get(u.lower(), u.lower())


def baca_excel(path, peta_komoditas):
    xl = pd.ExcelFile(path)
    nama_lembar = next((s for s in xl.sheet_names if "price" in s.lower()), xl.sheet_names[-1])
    v = xl.parse(nama_lembar, header=None)
    judul = str(v.iloc[0, 0])
    m = re.search(r"HARIAN\s+(.+?),\s*(.+?),\s*Daerah Istimewa Yogyakarta", judul)
    if not m:
        raise ValueError(f"{os.path.basename(path)}: judul tidak dikenali: {judul!r}")
    pasar, kab = m.group(1).strip(), m.group(2).strip()

    # cari baris header (yang kolom pertamanya "Variant")
    idx_hdr = next((i for i in range(min(10, len(v))) if str(v.iloc[i, 0]).strip().lower() == "variant"), None)
    if idx_hdr is None:
        raise ValueError(f"{os.path.basename(path)}: baris header 'Variant' tidak ditemukan")
    hdr = v.iloc[idx_hdr].tolist()
    kolom_tgl = {}
    for j, c in enumerate(hdr[3:], start=3):
        t = pd.to_datetime(c, errors="coerce")
        if pd.notna(t):
            kolom_tgl[j] = t.date().isoformat()
    if not kolom_tgl:
        raise ValueError(f"{os.path.basename(path)}: tidak ada kolom tanggal")

    baris = []
    for i in range(idx_hdr + 1, len(v)):
        varian = v.iloc[i, 0]
        if pd.isna(varian) or str(varian).strip().lower().startswith("sumber"):
            continue
        varian = str(varian).strip()
        satuan = satuan_rapi(v.iloc[i, 2])
        komoditas = peta_komoditas.get(varian) or varian.split()[0]
        for j, tgl in kolom_tgl.items():
            h = pd.to_numeric(v.iloc[i, j], errors="coerce")
            if pd.isna(h) or h <= 0:
                continue
            baris.append({"tanggal": tgl, "kabupaten_kota": kab, "pasar": pasar, "komoditas": komoditas,
                          "varian": varian, "satuan": satuan, "harga": float(h)})
    return pasar, kab, pd.DataFrame(baris)


def main(argv):
    berkas = []
    for pola in argv:
        berkas.extend(glob.glob(pola) or [pola])
    if not berkas:
        print(__doc__); return 1

    lama = pd.read_csv(KELUARAN) if os.path.exists(KELUARAN) else pd.DataFrame(columns=KOLOM)
    peta = (lama.drop_duplicates("varian").set_index("varian")["komoditas"].to_dict()) if len(lama) else {}
    varian_lama = set(lama.varian.astype(str)) if len(lama) else set()
    pasar_lama = set(lama.pasar.astype(str)) if len(lama) else set()

    diambil = datetime.now(timezone.utc).isoformat()
    baru = []
    for f in sorted(berkas):
        try:
            pasar, kab, df = baca_excel(f, peta)
        except Exception as e:
            print(f"DILEWATI {os.path.basename(f)}: {e}"); continue
        df["diambil_pada_utc"] = diambil
        asing = sorted(set(df.varian) - varian_lama) if varian_lama else []
        print(f"OK  {pasar:22s} {kab:18s} {df.tanggal.min()} → {df.tanggal.max()}  {df.varian.nunique()} varian  {len(df)} baris"
              + ("" if pasar in pasar_lama or not pasar_lama else "  [pasar baru]")
              + (f"  varian belum dikenal: {asing}" if asing else ""))
        baru.append(df)
    if not baru:
        print("Tidak ada berkas yang bisa dimuat."); return 1

    tambahan = pd.concat(baru, ignore_index=True)
    kunci = ["tanggal", "pasar", "komoditas", "varian"]

    # Pemeriksaan tumpang tindih: tanggal yang ada di riwayat DAN di berkas baru
    # harus memberi harga yang sama. Kalau tidak, ada yang beda sumber/pasar.
    if len(lama):
        irisan = lama.merge(tambahan, on=kunci, suffixes=("_lama", "_baru"))
        if len(irisan):
            beda = irisan[(irisan.harga_lama - irisan.harga_baru).abs() > 0.5]
            print(f"Tumpang tindih dengan riwayat: {len(irisan)} baris, {len(beda)} berbeda harga"
                  + (f" (contoh: {beda[['tanggal','pasar','varian','harga_lama','harga_baru']].head(3).to_dict('records')})" if len(beda) else ""))

    gab = pd.concat([lama, tambahan], ignore_index=True)
    gab["tanggal"] = pd.to_datetime(gab["tanggal"], errors="coerce").dt.date.astype(str)
    for k in ("kabupaten_kota", "pasar", "komoditas", "varian", "satuan"):
        gab[k] = gab[k].fillna("").astype(str).str.strip()
    sebelum = len(gab)
    gab = gab.drop_duplicates(subset=kunci, keep="last").sort_values(kunci).reset_index(drop=True)
    gab = gab[KOLOM]
    gab.to_csv(KELUARAN, index=False)
    print(f"Tersimpan {os.path.relpath(KELUARAN)}: {len(lama)} -> {len(gab)} baris "
          f"(+{len(gab) - len(lama)} baru, {sebelum - len(gab)} duplikat diganti), "
          f"{gab.pasar.nunique()} pasar, {gab.varian.nunique()} varian, s.d. {gab.tanggal.max()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
