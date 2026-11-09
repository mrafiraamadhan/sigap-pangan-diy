"""
Arsip TAMBAH-SAJA (append-only) untuk data yang tidak punya riwayat di sumbernya,
atau yang DITIMPA habis setiap run pipeline.

Kenapa perlu
------------
Sebagian besar sumber SIGAP menyimpan riwayatnya sendiri (PIHPS, SP2KP, DPKP harga,
ENSO, Open-Meteo), jadi bisa diunduh ulang kapan saja. Tetapi ada yang hanya
menampilkan "angka hari ini", atau yang keluarannya kita timpa tiap run:

  * prakiraan_harga.csv  -> tiap run menimpa prakiraan lama. Padahal prakiraan
    yang TERBIT SEBELUM harga aktual diketahui adalah satu-satunya bukti uji
    jujur (out-of-sample) bahwa model berguna. Arsip ini menyimpan prakiraan
    PERTAMA yang terbit untuk tiap (sumber, komoditas, horizon, tanggal data).
  * status_pipeline.csv  -> hanya berisi run terakhir. Arsip menyimpan semua cek,
    sehingga jeda pembaruan tiap sumber bisa dihitung (mis. "PIHPS DIY hanya
    diperbarui sekali sebulan").
  * google_trends_panik.csv -> Google menskala ulang angka di setiap pengambilan.
    Arsip menyimpan angka per tanggal SETIAP KALI berubah.
  * validasi_berita.csv  -> hasil pencarian berita berubah/hilang seiring waktu.
    Arsip menyimpan setiap hit unik saat pertama kali terlihat.
  * Cadangan pangan DIY  -> portal hanya menampilkan stok terkini. Snapshot
    perubahan sudah disimpan di data/cadangan_pangan_diy.csv sejak 30 Agt 2026;
    log setiap cek (berhasil/gagal, tanggal data, total) ditulis langsung oleh
    src/sources/cadangan_pangan_scraper.py ke data/arsip/log_cadangan.csv.

Berkas arsip ada di data/arsip/. Folder ini TIDAK ikut disalin ke docs/data/
(langkah commit hanya menyalin data/*.csv, tidak rekursif), jadi papan pantau
tetap ringan, tetapi arsip ikut ter-commit karena `git add data/`.

Dipanggil otomatis di akhir src/pipeline/catat_status.py, atau manual:
    python src/pipeline/arsip_harian.py
Isi mundur dari riwayat git (sekali saja): src/pipeline/isi_arsip_dari_git.py
"""

import os
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, "data")
ARSIP = os.path.join(DATA, "arsip")

P_PRAKIRAAN = os.path.join(ARSIP, "prakiraan_terbit.csv")
P_LOG_CEK = os.path.join(ARSIP, "log_cek_sumber.csv")
P_TRENDS = os.path.join(ARSIP, "trends_terbit.csv")
P_BERITA = os.path.join(ARSIP, "berita_terbit.csv")

KUNCI_PRAKIRAAN = ["sumber", "komoditas", "horizon", "tanggal_data_terakhir"]
KUNCI_BERITA = ["tanggal", "komoditas", "query", "url_teratas"]


def _s(v) -> str:
    return "" if pd.isna(v) else str(v).strip()


def _baca(path) -> pd.DataFrame:
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    return pd.DataFrame()


def _tulis(path, lama: pd.DataFrame, tambah: pd.DataFrame) -> int:
    if tambah.empty:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    gab = pd.concat([lama, tambah], ignore_index=True) if not lama.empty else tambah
    gab.to_csv(path, index=False)
    return len(tambah)


def _kunci_set(df: pd.DataFrame, kunci) -> set:
    if df.empty:
        return set()
    for k in kunci:
        if k not in df.columns:
            df[k] = ""
    return set(map(tuple, df[kunci].astype(str).apply(lambda s: s.str.strip()).values))


def tambah_kunci_baru(path, baru: pd.DataFrame, kunci, asal: str, waktu_cadangan: str) -> int:
    """Tambahkan baris yang kuncinya belum pernah ada (yang pertama terlihat menang)."""
    if baru is None or baru.empty:
        return 0
    baru = baru.astype(str).replace({"nan": ""}).copy()
    for k in kunci:
        if k not in baru.columns:
            baru[k] = ""
    lama = _baca(path)
    ada = _kunci_set(lama, kunci)
    baru = baru.drop_duplicates(subset=kunci, keep="first")
    pilih = [tuple(_s(r[k]) for k in kunci) not in ada for _, r in baru.iterrows()]
    tambah = baru.loc[pilih].copy()
    if tambah.empty:
        return 0
    tambah["pertama_terlihat_utc"] = (tambah["diambil_pada_utc"]
                                      if "diambil_pada_utc" in tambah.columns else waktu_cadangan)
    tambah.loc[tambah["pertama_terlihat_utc"].eq(""), "pertama_terlihat_utc"] = waktu_cadangan
    tambah["asal_arsip"] = asal
    return _tulis(path, lama, tambah)


def arsip_prakiraan(df: pd.DataFrame, asal="pipeline", waktu=None) -> int:
    if df is None or df.empty:
        return 0
    df = df.copy()
    if "sumber" not in df.columns:          # 3 versi awal (31 Agt 2026) belum punya kolom sumber
        df["sumber"] = "(tidak tercatat)"
    return tambah_kunci_baru(P_PRAKIRAAN, df, KUNCI_PRAKIRAAN, asal, waktu or _kini())


def arsip_berita(df: pd.DataFrame, asal="pipeline", waktu=None) -> int:
    return tambah_kunci_baru(P_BERITA, df, KUNCI_BERITA, asal, waktu or _kini())


def arsip_log_cek(df: pd.DataFrame, run_utc: str, asal="pipeline") -> int:
    """Satu baris per pilar per run. Kunci (run_utc, pilar) supaya aman dijalankan ulang."""
    if df is None or df.empty:
        return 0
    df = df.astype(str).replace({"nan": ""}).copy()
    df.insert(0, "run_utc", run_utc)
    df["asal_arsip"] = asal
    lama = _baca(P_LOG_CEK)
    ada = _kunci_set(lama, ["run_utc", "pilar"])
    tambah = df[[(run_utc, _s(p)) not in ada for p in df["pilar"]]]
    return _tulis(P_LOG_CEK, lama, tambah)


def _waktu(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")


def arsip_trends(df: pd.DataFrame, asal="pipeline", waktu=None) -> int:
    """Simpan (date, skor_minat) per seri bila berbeda dari nilai yang berlaku
    SEBELUM pengambilan itu, sehingga deret 'seperti yang diketahui pada hari X'
    bisa direkonstruksi. Seri = keyword; versi lama berkas tidak menyimpan keyword
    (kolomnya terbuang oleh groupby.apply), jadi dipakai urutan pengambilan
    ('urutan-1', 'urutan-2', ...) dalam satu run sebagai labelnya."""
    if df is None or df.empty or "date" not in df.columns:
        return 0
    df = df.astype(str).replace({"nan": ""}).copy()
    if "diambil_pada_utc" not in df.columns:
        df["diambil_pada_utc"] = waktu or _kini()
    if "keyword" not in df.columns or df["keyword"].eq("").all():
        urut = {t: i + 1 for i, t in enumerate(sorted(df["diambil_pada_utc"].unique()))}
        df["seri"] = df["diambil_pada_utc"].map(lambda t: f"urutan-{urut[t]}")
        df["keyword"] = ""
    else:
        df["seri"] = df["keyword"]
    df = df[["seri", "keyword", "date", "skor_minat", "diambil_pada_utc"]]
    df = df.drop_duplicates(subset=["seri", "date", "diambil_pada_utc"], keep="last")

    lama = _baca(P_TRENDS)
    if lama.empty:
        lama_w = pd.Series(dtype="datetime64[ns, UTC]")
        sudah = set()
    else:
        lama_w = _waktu(lama["diambil_pada_utc"])
        sudah = set(zip(lama["seri"], lama["date"], lama["diambil_pada_utc"]))
    pilih = []
    for (seri, t), g in df.groupby(["seri", "diambil_pada_utc"]):
        tw = _waktu(pd.Series([t])).iloc[0]
        terakhir = {}
        if not lama.empty:
            sebelum = lama[(lama["seri"] == seri) & (lama_w < tw)].assign(_w=lama_w)
            for _, r in sebelum.sort_values("_w").iterrows():
                terakhir[r["date"]] = r["skor_minat"]
        for i, r in g.iterrows():
            if terakhir.get(r["date"]) != r["skor_minat"] and (seri, r["date"], t) not in sudah:
                pilih.append(i)
    tambah = df.loc[pilih].copy()
    tambah["asal_arsip"] = asal
    return _tulis(P_TRENDS, lama, tambah)


def _kini() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _baca_data(nama) -> pd.DataFrame:
    p = os.path.join(DATA, nama)
    try:
        return pd.read_csv(p, dtype=str, keep_default_na=False) if os.path.isfile(p) else pd.DataFrame()
    except Exception as e:
        print(f"  (lewati {nama}: {type(e).__name__}: {str(e)[:120]})")
        return pd.DataFrame()


def main(run_utc=None) -> int:
    run_utc = run_utc or _kini()
    hasil = {}
    for label, fungsi in [
        ("prakiraan", lambda: arsip_prakiraan(_baca_data("prakiraan_harga.csv"), waktu=run_utc)),
        ("log cek", lambda: arsip_log_cek(_baca_data("status_pipeline.csv"), run_utc)),
        ("trends", lambda: arsip_trends(_baca_data("google_trends_panik.csv"), waktu=run_utc)),
        ("berita", lambda: arsip_berita(_baca_data("validasi_berita.csv"), waktu=run_utc)),
    ]:
        try:
            hasil[label] = fungsi()
        except Exception as e:                      # arsip tidak boleh menggagalkan pipeline
            hasil[label] = f"GAGAL {type(e).__name__}: {str(e)[:120]}"
    print("Arsip harian:", ", ".join(f"{k} +{v}" for k, v in hasil.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
