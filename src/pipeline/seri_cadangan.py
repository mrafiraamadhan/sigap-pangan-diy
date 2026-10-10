"""
Susun DERET WAKTU per komoditas dan per wilayah dari riwayat cadangan pangan
(data/cadangan_pangan_diy.csv) -> data/cadangan_pangan_seri.csv (format panjang).

Kenapa perlu: riwayat disimpan "hanya perubahan" per baris, jadi pada satu waktu
pengambilan tidak semua komoditas/wilayah tercatat. Nilai yang tidak tercatat
diturunkan dari nilai terakhir yang diketahui (carry-forward). Riwayat lama
(sebelum 8 Okt 2026) memakai dedup global, sehingga nilai yang KEMBALI ke angka
lama (biasanya kembali ke 0 ketika tanggal data portal berganti) tidak tercatat.
Untuk itu dipakai satu kendala struktural: di tiap waktu pengambilan,
    jumlah semua wilayah  = Total,   dan   jumlah semua komoditas = Total.
Bila jumlah nilai yang diturunkan melebihi Total, sebagian nilai yang hanya
diturunkan (bukan tercatat) dinolkan dengan pencarian subset sehingga jumlahnya
tepat sama dengan Total. Hasil tiap nilai diberi label asalnya:
    tercatat        = ada di riwayat pada waktu itu
    diturunkan      = carry-forward dari nilai terakhir
    nol_diturunkan  = dinolkan agar jumlah = Total (menandai pergantian tanggal data)

Komoditas tidak dijumlahkan lintas jenis (beras tidak ditambah kacang). Yang
dijumlahkan hanya yang setara: setara beras = Beras Medium + Beras Premium +
0,6274 x GKG (rendemen GKG ke beras menurut BPS). Nilai ini juga ditulis sebagai
seri sendiri (jenis="turunan", nama="Setara beras").

Jalankan dari root repo: python src/pipeline/seri_cadangan.py
"""

import itertools
import os
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
P_IN = os.path.join(HERE, "..", "..", "data", "cadangan_pangan_diy.csv")
P_OUT = os.path.join(HERE, "..", "..", "data", "cadangan_pangan_seri.csv")
RENDEMEN_GKG = 0.6274   # BPS: konversi GKG -> beras
TOLERANSI_DETIK = 90    # dua tabel dalam satu run berbeda beberapa milidetik


def muat():
    d = pd.read_csv(P_IN)
    d["t"] = pd.to_datetime(d["diambil_pada_utc"], utc=True, format="ISO8601", errors="coerce")
    d = d.dropna(subset=["t"]).sort_values("t")
    d["kg"] = pd.to_numeric(d["Jumlah Stok (Kg)"], errors="coerce")
    d["tanggal_data"] = d.get("tanggal_data", "").fillna("").astype(str).replace({"nan": ""})
    wil = d[d["Wilayah"].notna() & d["Nama"].isna() & d["kg"].notna()].copy()
    wil["jenis"], wil["nama"] = "wilayah", wil["Wilayah"].astype(str).str.strip()
    kom = d[d["Jenis Komoditas"].notna() & d["Wilayah"].isna() & d["kg"].notna()].copy()
    kom["jenis"], kom["nama"] = "komoditas", kom["Jenis Komoditas"].astype(str).str.strip()
    return pd.concat([wil, kom], ignore_index=True)[["t", "tanggal_data", "jenis", "nama", "kg"]]


def kelompokkan_waktu(df):
    """Satukan baris yang jam ambilnya hanya beda milidetik (satu run) ke satu stempel."""
    waktu = sorted(df["t"].unique())
    peta, acuan = {}, None
    for w in waktu:
        if acuan is None or (w - acuan).total_seconds() > TOLERANSI_DETIK:
            acuan = w
        peta[w] = acuan
    df = df.copy(); df["run"] = df["t"].map(peta)
    return df


def nolkan_agar_pas(nilai, tercatat, total):
    """nilai: dict nama->kg (sudah carry-forward); tercatat: set nama yang tercatat di run ini.
    Kembalikan set nama (yang TIDAK tercatat) untuk dinolkan agar jumlah == total."""
    kandidat = [n for n in nilai if n not in tercatat and nilai[n] > 0]
    jumlah = sum(nilai.values())
    if abs(jumlah - total) < 0.5 or not kandidat:
        return set()
    kelebihan = jumlah - total
    if kelebihan < 0:
        return set()
    terbaik, selisih_terbaik = None, None
    for r in range(1, len(kandidat) + 1):
        for komb in itertools.combinations(kandidat, r):
            s = sum(nilai[n] for n in komb)
            sel = abs(s - kelebihan)
            if selisih_terbaik is None or sel < selisih_terbaik:
                terbaik, selisih_terbaik = set(komb), sel
            if sel < 0.5:
                return set(komb)
    # Terima hanya bila sisa selisih kecil relatif (<1% total); kalau tidak, biarkan apa adanya.
    return terbaik if (selisih_terbaik is not None and selisih_terbaik <= max(0.01 * total, 50)) else set()


def susun(df):
    df = kelompokkan_waktu(df)
    keluar = []
    for jenis in ("wilayah", "komoditas"):
        sub = df[df["jenis"] == jenis]
        nama_semua = sorted(n for n in sub["nama"].unique() if n != "Total")
        nilai = {n: 0.0 for n in nama_semua}
        for run, g in sub.groupby("run", sort=True):
            tercatat = {}
            total = None
            for _, r in g.iterrows():
                if r["nama"] == "Total":
                    total = float(r["kg"])
                else:
                    tercatat[r["nama"]] = float(r["kg"])
            tgl = next((x for x in g["tanggal_data"] if x), "")
            # Nilai bulat kecil (< 1000) pada riwayat lama bisa berarti "6.000" yang
            # terbaca 6. Pilih skala (x1 atau x1000) yang membuat jumlah == Total.
            ambigu = [n for n, v in tercatat.items() if 0 < v < 1000 and float(v).is_integer()]
            pilihan_terbaik, dinolkan_terbaik, skor_terbaik = None, set(), None
            for komb in itertools.product([1, 1000], repeat=len(ambigu)) if total is not None else [()]:
                coba = dict(nilai)
                for n, v in tercatat.items():
                    coba[n] = v
                diskalakan = set()
                for n, f in zip(ambigu, komb):
                    if f == 1000:
                        coba[n] = tercatat[n] * 1000; diskalakan.add(n)
                dinolkan = nolkan_agar_pas(coba, set(tercatat), total) if total is not None else set()
                for n in dinolkan:
                    coba[n] = 0.0
                selisih = abs(sum(coba.values()) - total) if total is not None else 0.0
                skor = (round(selisih), len(diskalakan))
                if skor_terbaik is None or skor < skor_terbaik:
                    skor_terbaik, pilihan_terbaik, dinolkan_terbaik, diskalakan_terbaik = skor, coba, dinolkan, diskalakan
            nilai.update(pilihan_terbaik)
            for n in nama_semua:
                asal = ("tercatat_diskalakan" if n in diskalakan_terbaik else "tercatat") if n in tercatat \
                    else ("nol_diturunkan" if n in dinolkan_terbaik else "diturunkan")
                keluar.append({"waktu_utc": run.isoformat(), "tanggal_data": tgl, "jenis": jenis, "nama": n,
                               "kg": round(nilai[n], 1), "asal": asal, "total_portal_kg": total})
    out = pd.DataFrame(keluar)
    # seri turunan: setara beras
    kom = out[out["jenis"] == "komoditas"]
    for run, g in kom.groupby("waktu_utc"):
        v = dict(zip(g["nama"], g["kg"]))
        setara = v.get("Beras Medium", 0) + v.get("Beras Premium", 0) + RENDEMEN_GKG * v.get("Gabah Kering Giling (GKG)", 0)
        asal = "tercatat" if (g["asal"] == "tercatat").any() else "diturunkan"
        out = pd.concat([out, pd.DataFrame([{"waktu_utc": run, "tanggal_data": g["tanggal_data"].iloc[0], "jenis": "turunan",
                                             "nama": "Setara beras", "kg": round(setara, 1), "asal": asal,
                                             "total_portal_kg": g["total_portal_kg"].iloc[0]}])], ignore_index=True)
    return out.sort_values(["waktu_utc", "jenis", "nama"]).reset_index(drop=True)


def main():
    if not os.path.isfile(P_IN):
        print("Riwayat cadangan belum ada."); return 0
    df = muat()
    out = susun(df)
    out["dibuat_utc"] = datetime.now(timezone.utc).isoformat()
    out.to_csv(P_OUT, index=False)
    n_run = out["waktu_utc"].nunique()
    for jenis in ("wilayah", "komoditas"):
        cek = out[out["jenis"] == jenis].groupby("waktu_utc").agg(jml=("kg", "sum"), tot=("total_portal_kg", "first"))
        meleset = int(((cek["jml"] - cek["tot"]).abs() > 0.5).sum())
        print(f"{jenis}: jumlah != total pada {meleset} dari {len(cek)} waktu pengambilan.")
    print(f"{n_run} waktu pengambilan, {len(out)} baris seri.")
    sb = out[out["nama"] == "Setara beras"].tail(5)
    for _, r in sb.iterrows():
        print(f"  {r['waktu_utc'][:16]}  setara beras {r['kg']/1000:7.1f} ton  (portal total {r['total_portal_kg']/1000:7.1f} ton)")
    print(f"Tersimpan {os.path.relpath(P_OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
