"""
Migrasi SEKALI JALAN untuk data/cadangan_pangan_diy.csv (8 Oktober 2026).

Riwayat lama dibaca dengan pemisah ribuan yang salah: "140.350" (= 140.350 kg)
tersimpan sebagai 140.35. Skrip ini mengembalikan skala kilogram, dengan aturan:

  * Baris TOTAL dan baris per WILAYAH: selalu x1000. Di portal, angka-angka ini
    selalu ditulis dengan pemisah ribuan (puluhan sampai ratusan ton).
  * Baris per JENIS KOMODITAS:
      - punya pecahan desimal (mis. 11.6, 94.37)  -> x1000 (pasti dari "11.600")
      - bilangan bulat (mis. 40, 120, 15)         -> dibiarkan; ini kg kecil yang
        di portal memang ditulis tanpa pemisah ("40", "120"). Ada sedikit
        kemungkinan nilai seperti 15 sebenarnya "15.000" (= 15 ton), tetapi tidak
        bisa dibedakan lagi dari riwayat; dampaknya kecil karena papan pantau
        memakai baris Total untuk status dan grafik.
  * Baris daftar lumbung (ada kolom Nama/Alamat) tidak punya stok; dibiarkan.

Selain itu, kolom tahun_filter 2019-2025 yang berasal dari parameter ?y= yang
diabaikan server DIBUANG (duplikat dari snapshot yang sama), dan ditambah kolom
tanggal_data (kosong untuk riwayat lama, karena tidak sempat direkam).

Jalankan dari root repo:  python src/pipeline/migrasi_cadangan_kg.py
Aman dijalankan dua kali: baris yang sudah bermigrasi (ditandai kolom
`skala_kg_terkoreksi`) tidak disentuh lagi.
"""

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "..", "data", "cadangan_pangan_diy.csv")


# Tanggal data portal ("Data per tanggal ...") untuk snapshot lama yang sempat
# diverifikasi manual (8 Okt 2026: portal menampilkan 140.350 kg "per 05-Oct-2026",
# sama persis dengan snapshot 7 Okt; snapshot 5 Okt 07:50 UTC = 73.540 kg = angka
# Kota Yogyakarta pada tanggal data yang sama). Snapshot lebih lama dibiarkan kosong.
BACKFILL_TANGGAL_DATA = {
    "2026-10-05T07:50": "2026-10-05",
    "2026-10-07T02:32": "2026-10-05",
}


# Snapshot LENGKAP portal yang diverifikasi manual 8 Okt 2026 (halaman
# cadanganpangan.jogjaprov.go.id/site/index, "Data per tanggal 05-Oct-2026").
# Riwayat lama disimpan dengan dedup global, sehingga nilai yang kembali ke angka
# lama (mis. Kulon Progo kembali 0) tidak tercatat dan "nilai terakhir per kunci"
# menjadi basi. Satu snapshot utuh ini menjadi jangkar: sejak titik ini skrip
# pengambil hanya menulis perubahan terhadap nilai terakhir per kunci.
JANGKAR_UTC = "2026-10-08T10:55:00+00:00"
JANGKAR_TGL = "2026-10-05"
JANGKAR_WILAYAH = [("Total", 140350), ("Provinsi DI Yogyakarta", 0), ("Kabupaten Kulon Progo", 0),
                   ("Kabupaten Bantul", 0), ("Kabupaten Gunung Kidul", 0), ("Kota Yogyakarta", 73540),
                   ("Kabupaten Sleman", 66810), ("Kecamatan Sleman", 0)]
JANGKAR_JENIS = [("Total", 140350), ("Gabah Kering Giling (GKG)", 11600), ("Beras Medium", 94370),
                 ("Beras Premium", 34220), ("Jagung", 0), ("Kedelai", 40), ("Kacang Tanah", 120),
                 ("Kacang Hijau", 0)]


def tambah_jangkar(d: pd.DataFrame) -> pd.DataFrame:
    if d["diambil_pada_utc"].astype(str).eq(JANGKAR_UTC).any():
        return d
    baris = []
    for i, (w, kg) in enumerate(JANGKAR_WILAYAH):
        baris.append({"#": None if w == "Total" else i, "Wilayah": w, "Jumlah Stok (Kg)": float(kg)})
    for i, (j, kg) in enumerate(JANGKAR_JENIS):
        baris.append({"#": None if j == "Total" else i, "Jenis Komoditas": j, "Jumlah Stok (Kg)": float(kg)})
    j = pd.DataFrame(baris)
    j["tahun_filter"] = 2026
    j["diambil_pada_utc"] = JANGKAR_UTC
    j["tanggal_data"] = JANGKAR_TGL
    j["skala_kg_terkoreksi"] = True
    print(f"Snapshot jangkar {JANGKAR_UTC} ditambahkan: {len(j)} baris")
    return pd.concat([d, j], ignore_index=True)


def isi_tanggal_data(d: pd.DataFrame) -> int:
    if "tanggal_data" not in d.columns:
        d["tanggal_data"] = ""
    # Kolom kosong terbaca float (NaN) oleh pandas; paksa jadi teks dulu.
    d["tanggal_data"] = d["tanggal_data"].fillna("").astype(str).replace({"nan": ""})
    n = 0
    for prefix, tgl in BACKFILL_TANGGAL_DATA.items():
        m = d["diambil_pada_utc"].astype(str).str.startswith(prefix) & (d["tanggal_data"] == "")
        d.loc[m, "tanggal_data"] = tgl
        n += int(m.sum())
    return n


def main():
    d = pd.read_csv(PATH)
    if "skala_kg_terkoreksi" in d.columns and d["skala_kg_terkoreksi"].fillna(False).astype(bool).all():
        n0 = len(d)
        n = isi_tanggal_data(d)
        d = tambah_jangkar(d)
        if n or len(d) != n0:
            d = d.sort_values("diambil_pada_utc").reset_index(drop=True)
            d.to_csv(PATH, index=False)
            print(f"Sudah bermigrasi; tanggal_data diisi {n} baris, jangkar {'ditambah' if len(d) != n0 else 'sudah ada'}.")
        else:
            print("Sudah bermigrasi; tidak ada yang diubah.")
        return 0
    kol = "Jumlah Stok (Kg)"
    n0 = len(d)

    # 1) buang duplikat lintas tahun_filter (snapshot sama dibaca 8x)
    thn_maks = d["tahun_filter"].max()
    d = d[d["tahun_filter"] == thn_maks].copy()
    print(f"Baris dengan tahun_filter != {thn_maks} dibuang: {n0 - len(d)} (duplikat filter tahun)")

    # 2) koreksi skala
    if "skala_kg_terkoreksi" not in d.columns:
        d["skala_kg_terkoreksi"] = False
    belum = ~d["skala_kg_terkoreksi"].fillna(False).astype(bool)
    v = pd.to_numeric(d[kol], errors="coerce")
    wilayah = d["Wilayah"].notna() & d.get("Nama", pd.Series(index=d.index, dtype=object)).isna()
    jenis = d["Jenis Komoditas"].notna() if "Jenis Komoditas" in d.columns else pd.Series(False, index=d.index)
    # Baris "Total" pada tabel jenis komoditas = angka yang sama dengan Total wilayah -> ikut aturan x1000.
    jenis_total = jenis & (d["Jenis Komoditas"].astype(str).str.strip() == "Total")
    wilayah = wilayah | jenis_total
    jenis = jenis & ~jenis_total
    pecahan = v.notna() & ((v * 1000).round() % 1000 != 0)
    kali1000 = belum & v.notna() & (wilayah | (jenis & pecahan))
    d.loc[kali1000, kol] = (v[kali1000] * 1000).round(0)
    d.loc[belum, "skala_kg_terkoreksi"] = True
    print(f"Baris dikalikan 1000: {int(kali1000.sum())}; baris jenis bulat dibiarkan: {int((belum & jenis & ~pecahan & v.notna()).sum())}")

    nb = isi_tanggal_data(d)
    print(f"tanggal_data diisi dari verifikasi manual: {nb} baris")
    d = tambah_jangkar(d)
    d = d.sort_values("diambil_pada_utc").reset_index(drop=True)
    d.to_csv(PATH, index=False)

    tot = d[d["Wilayah"].astype(str).str.strip() == "Total"][["diambil_pada_utc", kol]]
    print("Riwayat Total (kg) setelah migrasi:")
    for _, r in tot.iterrows():
        print(f"  {str(r['diambil_pada_utc'])[:10]}  {r[kol]:>12,.0f} kg = {r[kol]/1000:7.1f} ton")
    print(f"Tersimpan {os.path.relpath(PATH)}: {len(d)} baris")
    return 0


if __name__ == "__main__":
    sys.exit(main())
