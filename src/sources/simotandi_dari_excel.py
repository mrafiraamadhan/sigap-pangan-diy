"""
Masukkan berkas Excel "Data Tabular" hasil unduhan MANUAL dari situs SIMOTANDI
(simotandi.pertanian.go.id -> Data Tabular -> filter Provinsi DI Yogyakarta ->
unduh) ke data/simotandi_fase_tanam_diy.csv.

Dipakai karena server SIMOTANDI menolak akses dari GitHub Actions maupun
sandbox cloud, sedangkan dari peramban biasa unduhannya lancar. Riwayat
2024-2026 yang sudah ada di berkas CSV memang berasal dari unduhan yang sama
(lihat kolom sumber_layer = "simotandi.pertanian.go.id/data-tabular").

Bentuk berkas Excel yang dikenali (satu lembar, ~84 baris untuk DIY):
  baris 0..4 : keterangan (tanggal unduh, jenis data, "Periode Perekaman: ...")
  baris 5..6 : header dua tingkat
  baris 7..  : 1 provinsi + 5 kabupaten + 78 kecamatan
  kolom      : No, KDPR, KDKB, KDKC, Provinsi, Kabupaten, Kecamatan, Bera,
               Penyiapan Lahan, Tanam, Veg 1, Veg 2, Gen 1, Gen 2, Panen,
               Standing Crop, Luas Baku Sawah
Hanya baris KECAMATAN (KDKC terisi) yang disimpan, sama seperti riwayat.
Berkas yang tidak difilter DIY (38 provinsi) ditolak dengan pesan jelas.

Cara pakai:
    python src/sources/simotandi_dari_excel.py "Data_Tabular_17 - 28 Agustus 2026_*.xlsx" ...
    python src/sources/simotandi_dari_excel.py folder_unduhan/*.xlsx
"""

import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import openpyxl
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
KELUARAN = os.path.join(HERE, "..", "..", "data", "simotandi_fase_tanam_diy.csv")

BULAN_ID = {b: i + 1 for i, b in enumerate(
    ["januari", "februari", "maret", "april", "mei", "juni", "juli", "agustus",
     "september", "oktober", "november", "desember"])}

# Kode periode = tahun + nomor urut periode 12-harian dalam tahun itu, mengikuti
# penomoran yang sudah dipakai riwayat (mis. 05-16 Agustus 2026 = 202619).
ACUAN_KODE = (202619, "2026-08-05")

KOLOM_KELUARAN = ["periode", "periode_kode", "periode_mulai", "periode_selesai", "provinsi",
                  "kabupaten_kota", "kecamatan", "kode_wilayah", "bera_ha", "persiapan_lahan_ha",
                  "tanam_ha", "vegetatif_1_ha", "vegetatif_2_ha", "generatif_1_ha", "generatif_2_ha",
                  "panen_ha", "standing_crop_ha", "luas_baku_sawah_ha", "total_luas_ha",
                  "sumber_layer", "diambil_pada_utc"]
KOLOM_FASE = ["bera_ha", "persiapan_lahan_ha", "tanam_ha", "vegetatif_1_ha", "vegetatif_2_ha",
              "generatif_1_ha", "generatif_2_ha", "panen_ha"]


def parse_periode(label):
    """'29 Agustus 2026 - 09 September 2026' / '17 - 28 Agustus 2026' -> (mulai, selesai)."""
    teks = label.lower().replace("–", "-").replace("—", "-")
    kiri, kanan = [s.strip() for s in teks.split("-", 1)]

    def pecah(bagian):
        m = re.match(r"^(\d{1,2})\s*([a-z]+)?\s*(\d{4})?$", bagian)
        if not m:
            raise ValueError(f"format periode tidak dikenali: {label!r}")
        return int(m.group(1)), BULAN_ID.get(m.group(2)) if m.group(2) else None, int(m.group(3)) if m.group(3) else None

    h1, b1, t1 = pecah(kiri)
    h2, b2, t2 = pecah(kanan)
    b1, t1, t2 = b1 or b2, t1 or t2, t2 or t1
    return datetime(t1, b1, h1).date(), datetime(t2, b2, h2).date()


def kode_periode(mulai):
    """Turunkan kode YYYYNN dari tanggal mulai, berpatokan pada ACUAN_KODE."""
    acuan_kode, acuan_tgl = ACUAN_KODE
    acuan = datetime.strptime(acuan_tgl, "%Y-%m-%d").date()
    selisih = (mulai - acuan).days
    if selisih % 12:
        raise ValueError(f"tanggal mulai {mulai} tidak segaris 12 hari dengan acuan {acuan}")
    n = selisih // 12
    tahun, urut = divmod(acuan_kode, 100)
    urut += n
    # Lintas tahun: nomor urut mulai lagi dari 1 tiap tahun kalender
    while urut > 30:
        tahun += 1
        urut -= 30
    while urut < 1:
        tahun -= 1
        urut += 30
    return tahun * 100 + urut


def baca_excel(path):
    ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    meta = "\n".join(str(r[0]) for r in rows[:5] if r and r[0])
    m = re.search(r"Periode Perekaman:\s*([^\n]+)", meta)
    if not m:
        raise ValueError(f"{os.path.basename(path)}: tidak menemukan 'Periode Perekaman'")
    label = m.group(1).strip()
    mu = re.search(r"Tanggal Pengunduhan:\s*([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4} [0-9:]+)", meta)
    diambil = None
    if mu:
        try:  # jam unduh tercatat WIB (UTC+7)
            diambil = (datetime.strptime(mu.group(1), "%d-%b-%Y %H:%M") - timedelta(hours=7)).replace(tzinfo=timezone.utc)
        except ValueError:
            diambil = None

    data = [r for r in rows[7:] if r and r[0] is not None]
    # Berkas berbentuk hierarki: baris provinsi (KDPR terisi), lalu baris kabupaten
    # (KDKB terisi), lalu baris-baris kecamatan (KDKC terisi) yang kolom provinsi/
    # kabupatennya KOSONG. Nama induk dibawa turun ke tiap kecamatan.
    kec, provinsi, kabupaten = [], None, None
    for r in data:
        if r[1] is not None:
            provinsi = str(r[4]).strip()
        elif r[2] is not None:
            kabupaten = str(r[5]).strip()
        elif r[3] is not None:
            kec.append((provinsi, kabupaten, r))
    if not kec:
        prov = {str(r[4]) for r in data if r[4]}
        raise ValueError(f"{os.path.basename(path)}: tidak ada baris kecamatan. Berkas ini tampaknya "
                         f"belum difilter ke DI Yogyakarta ({len(prov)} provinsi). Unduh ulang dengan filter provinsi.")
    mulai, selesai = parse_periode(label)
    out = []
    for provinsi, kabupaten, r in kec:
        out.append({
            "periode": label, "periode_kode": kode_periode(mulai),
            "periode_mulai": mulai.isoformat(), "periode_selesai": selesai.isoformat(),
            "provinsi": provinsi, "kabupaten_kota": kabupaten, "kecamatan": str(r[6]).strip(),
            "kode_wilayah": int(r[3]),
            "bera_ha": float(r[7] or 0), "persiapan_lahan_ha": float(r[8] or 0), "tanam_ha": float(r[9] or 0),
            "vegetatif_1_ha": float(r[10] or 0), "vegetatif_2_ha": float(r[11] or 0),
            "generatif_1_ha": float(r[12] or 0), "generatif_2_ha": float(r[13] or 0), "panen_ha": float(r[14] or 0),
            "standing_crop_ha": float(r[15] or 0), "luas_baku_sawah_ha": float(r[16] or 0),
            "sumber_layer": "simotandi.pertanian.go.id/data-tabular",
            "diambil_pada_utc": (diambil or datetime.now(timezone.utc)).isoformat(),
        })
    df = pd.DataFrame(out)
    df["total_luas_ha"] = df[KOLOM_FASE].sum(axis=1).round(2)

    # Pemeriksaan: jumlah kecamatan harus sama dengan baris provinsi di berkas yang sama.
    prov_row = next((r for r in data if r[2] is None and r[3] is None), None)
    if prov_row is not None:
        selisih = abs(df.panen_ha.sum() - float(prov_row[14] or 0))
        if selisih > 1:
            print(f"  PERINGATAN {label}: jumlah panen kecamatan {df.panen_ha.sum():.2f} ha "
                  f"vs baris provinsi {float(prov_row[14]):.2f} ha")
    return df[KOLOM_KELUARAN]


def main(argv):
    berkas = []
    for pola in argv:
        berkas.extend(glob.glob(pola) or [pola])
    if not berkas:
        print(__doc__); return 1

    lama = pd.read_csv(KELUARAN) if os.path.exists(KELUARAN) else pd.DataFrame(columns=KOLOM_KELUARAN)
    kec_dikenal = set(lama.kecamatan.astype(str)) if len(lama) else set()
    baru = []
    for f in sorted(berkas):
        try:
            df = baca_excel(f)
        except ValueError as e:
            print(f"DILEWATI: {e}"); continue
        asing = sorted(set(df.kecamatan) - kec_dikenal) if kec_dikenal else []
        if asing:
            print(f"  PERINGATAN: nama kecamatan belum dikenal riwayat/peta: {asing}")
        print(f"OK  {df.periode.iloc[0]}  kode {df.periode_kode.iloc[0]}  {len(df)} kecamatan  "
              f"panen {df.panen_ha.sum():,.0f} ha  standing crop {df.standing_crop_ha.sum():,.0f} ha")
        baru.append(df)
    if not baru:
        print("Tidak ada berkas yang bisa dimuat."); return 1

    gab = pd.concat([lama] + baru, ignore_index=True)
    sebelum = len(gab)
    # Periode yang sama diunduh ulang -> pakai versi terbaru (SIMOTANDI merevisi angka lama).
    # Kunci memakai kode_wilayah, BUKAN nama: "Jetis" ada di Bantul dan di Kota Yogyakarta.
    gab["kode_wilayah"] = pd.to_numeric(gab["kode_wilayah"], errors="coerce")
    gab = gab.drop_duplicates(subset=["periode_kode", "kode_wilayah"], keep="last")
    gab = gab.sort_values(["periode_kode", "kabupaten_kota", "kecamatan"]).reset_index(drop=True)
    gab.to_csv(KELUARAN, index=False)
    print(f"Tersimpan {os.path.relpath(KELUARAN)}: {len(lama)} -> {len(gab)} baris "
          f"({sebelum - len(gab)} baris periode lama diganti versi baru), periode terakhir {gab.periode.iloc[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
