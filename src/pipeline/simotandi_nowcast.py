"""
Nowcast SIMOTANDI: perkiraan fase pertanaman padi per kecamatan DIY untuk periode 12-harian yang
BELUM diterbitkan Kementan, ditambah prakiraan luas panen beberapa bulan ke depan.

Mengapa bisa:
  1. Fase SIMOTANDI berperilaku seperti buku besar kohort tanam. Luas Vegetatif 1 pada periode t
     ~ tanam(t-1) + tanam(t-2); Vegetatif 2 ~ tanam(t-3..t-4); Generatif 1 ~ tanam(t-5..t-6), dst.
     (galat 4-8% di tingkat kecamatan, Jan 2024 - Okt 2026). Jadi sebagian besar fase periode yang
     belum terbit sudah "tertulis" di data yang sudah terbit.
  2. Satu-satunya yang benar-benar baru adalah luas TANAM pada periode yang belum terbit. Bagian ini
     diperkirakan dari radar Sentinel-1 (porsi sawah tergenang per kecamatan; repo arsip-realtime-indonesia,
     tiap 6 hari, terbit beberapa jam setelah perekaman). SIMOTANDI sendiri juga berbasis Sentinel-1.
  3. Luas panen ~ tanam 9-10 periode sebelumnya, sehingga prakiraan panen bisa dibuat ~3 bulan ke depan.

Galat setiap fase & horizon diukur ulang tiap run dengan uji mundur (rolling origin) dan ikut ditulis.

    python src/pipeline/simotandi_nowcast.py [--s1 PATH_ATAU_URL] [--hari-ini YYYY-MM-DD]
Keluaran:
    data/simotandi_nowcast_diy.csv        fase per kecamatan, periode terbit terakhir+1 .. periode berjalan
    data/simotandi_outlook_panen_diy.csv  prakiraan luas panen per kabupaten & DIY, ~9 periode ke depan
    data/simotandi_nowcast_akurasi.csv    galat uji mundur per fase & horizon (vs memakai angka terakhir apa adanya)
    data/arsip/simotandi_nowcast_terbit.csv  arsip tambah-saja setiap versi prakiraan (untuk diuji saat data resmi terbit)
"""
import argparse, io, os, sys
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

RESMI = "data/simotandi_fase_tanam_diy.csv"
S1_URL = "https://raw.githubusercontent.com/mrafiraamadhan/arsip-realtime-indonesia/main/arsip/sentinel1_padi/kecamatan_deret.csv"
OUT_NOW = "data/simotandi_nowcast_diy.csv"
OUT_PANEN = "data/simotandi_outlook_panen_diy.csv"
OUT_AKURASI = "data/simotandi_nowcast_akurasi.csv"
ARSIP = "data/arsip/simotandi_nowcast_terbit.csv"

KOL = {"bera": "bera_ha", "siap": "persiapan_lahan_ha", "tanam": "tanam_ha", "veg1": "vegetatif_1_ha",
       "veg2": "vegetatif_2_ha", "gen1": "generatif_1_ha", "gen2": "generatif_2_ha", "panen": "panen_ha"}
FASE = list(KOL)
LAG = {"veg1": (1, 2), "veg2": (3, 4), "gen1": (5, 6), "gen2": (7, 8, 9), "panen": (8, 9, 10, 11)}
H_OUTLOOK = 8          # periode setelah periode berjalan untuk prakiraan panen
MIN_SAWAH_S1 = 100     # piksel sawah Sentinel-1 minimum agar sinyal kecamatan dipakai


# ---------------- kalender periode 12-harian SIMOTANDI ----------------
def t_dari_kode(kode):
    kode = int(kode); return (kode // 100 - 2024) * 30 + kode % 100 - 1


def kode_dari_t(t):
    return (2024 + t // 30) * 100 + t % 30 + 1


def rentang_t(t):
    th, j = 2024 + t // 30, t % 30 + 1
    a = date(th, 1, 1) + timedelta(days=12 * (j - 1))
    return a, a + timedelta(days=11)


def t_dari_tanggal(d):
    j = (d.timetuple().tm_yday - 1) // 12 + 1
    return (d.year - 2024) * 30 + min(j, 30) - 1


# ---------------- data ----------------
def muat_resmi():
    d = pd.read_csv(RESMI)
    d["t"] = d.periode_kode.map(t_dari_kode)
    meta = d.drop_duplicates("kode_wilayah").set_index("kode_wilayah")[["kabupaten_kota", "kecamatan", "luas_baku_sawah_ha"]]
    P = {f: d.pivot_table(index="t", columns="kode_wilayah", values=c, aggfunc="sum").sort_index() for f, c in KOL.items()}
    idx = range(P["tanam"].index.min(), P["tanam"].index.max() + 1)
    P = {f: v.reindex(idx).reindex(columns=meta.index) for f, v in P.items()}
    return P, meta


def muat_s1(sumber, kode):
    try:
        if sumber.startswith("http"):
            r = requests.get(sumber, timeout=60); r.raise_for_status()
            s1 = pd.read_csv(io.StringIO(r.text))
        else:
            s1 = pd.read_csv(sumber)
    except Exception as e:
        print(f"Sentinel-1 tidak terbaca ({type(e).__name__}: {e}); nowcast tanam memakai pola musiman saja.")
        return None, None
    s1["t"] = pd.to_datetime(s1.tanggal).dt.date.map(t_dari_tanggal)
    # buang tanggal di sela akhir tahun (26-31 Des) yang tidak masuk periode mana pun
    s1 = s1[pd.to_datetime(s1.tanggal).dt.day_of_year <= 360]
    fb = s1.groupby(["t", "kode_wilayah"]).f_basah.mean().unstack().reindex(columns=kode)
    nsaw = s1.groupby("kode_wilayah").n_sawah.max().reindex(kode).fillna(0)
    fb.loc[:, nsaw < MIN_SAWAH_S1] = np.nan
    return fb, s1.tanggal.max()


# ---------------- model ----------------
def fit_lag(P, t_max):
    """Koefisien buku besar kohort: fase(t) = sum_l a_l * tanam(t-l), dipadankan (gabungan kecamatan) s.d. t_max."""
    koef = {}
    T = P["tanam"]
    for f, lags in LAG.items():
        Y = P[f].loc[:t_max]
        X = [T.shift(l).loc[:t_max] for l in lags]
        A = np.c_[[x.values.ravel() for x in X]].T; y = Y.values.ravel()
        ok = np.isfinite(A).all(1) & np.isfinite(y)
        a = np.linalg.lstsq(A[ok], y[ok], rcond=None)[0]
        koef[f] = dict(zip(lags, np.clip(a, 0, None)))
    return koef


def fit_tanam(P, LBS, fb, t_max):
    """Porsi tanam(t) = mu_k + b*(f_basah - rerata_k) + c*(porsi tanam t-30 - rerata_k) ; plus model musiman saja."""
    S = P["tanam"].loc[:t_max] / LBS
    rows = []
    for t in S.index:
        if t - 30 not in S.index:
            continue
        for k in S.columns:
            y, sea = S.at[t, k], S.at[t - 30, k]
            x = fb.at[t, k] if (fb is not None and t in fb.index) else np.nan
            if np.isfinite(y) and np.isfinite(sea):
                rows.append((t, k, y, sea, x))
    D = pd.DataFrame(rows, columns=["t", "k", "y", "sea", "fb"])
    mu = D.groupby("k")[["y", "sea"]].mean()
    D["yd"] = D.y - D.k.map(mu.y); D["sd"] = D.sea - D.k.map(mu.sea)
    c_sea = float((D.sd @ D.yd) / (D.sd @ D.sd)) if len(D) else 0.0
    m = {"mu": mu, "c_sea": c_sea, "b": None, "c": None, "fbmu": None}
    Ds = D.dropna(subset=["fb"])
    if len(Ds) > 200:
        fbmu = Ds.groupby("k").fb.mean()
        Ds = Ds.assign(fd=Ds.fb - Ds.k.map(fbmu))
        A = np.c_[Ds.fd, Ds.sd]; b, c = np.linalg.lstsq(A, Ds.yd.values, rcond=None)[0]
        m.update(b=float(b), c=float(c), fbmu=fbmu)
    return m


def tanam_perkiraan(m, P, LBS, fb, t, t0, T_est):
    """Luas tanam periode t (> t0) per kecamatan + label metodenya."""
    sea = (T_est.loc[t - 30] if t - 30 in T_est.index else P["tanam"].loc[t - 30]) / LBS
    mu = m["mu"].reindex(LBS.index)
    dasar = mu.y + m["c_sea"] * (sea - mu.sea)
    metode = pd.Series("musiman", index=LBS.index)
    if m["b"] is not None and fb is not None and t in fb.index:
        x = fb.loc[t]
        fbmu = m["fbmu"].reindex(LBS.index)
        s1 = mu.y + m["b"] * (x - fbmu) + m["c"] * (sea - mu.sea)
        pakai = s1.notna()
        dasar = dasar.where(~pakai, s1); metode[pakai] = "sentinel-1"
    dasar = dasar.fillna(P["tanam"].loc[t0] / LBS)
    return (dasar.clip(lower=0) * LBS), metode


def proyeksi(P, LBS, fb, t0, t_akhir, m_tanam, koef):
    """Fase per kecamatan untuk t0+1..t_akhir hanya dari data resmi <= t0 (+ Sentinel-1)."""
    T_est = P["tanam"].loc[:t0].copy()
    metode = {}
    for t in range(t0 + 1, t_akhir + 1):
        T_est.loc[t], metode[t] = tanam_perkiraan(m_tanam, P, LBS, fb, t, t0, T_est)
    hasil = {}
    for t in range(t0 + 1, t_akhir + 1):
        o = {"tanam": T_est.loc[t]}
        for f, a in koef.items():
            o[f] = sum(c * T_est.loc[t - l] for l, c in a.items() if t - l in T_est.index)
        tetap = sum(o[f] for f in ["tanam", "veg1", "veg2", "gen1", "gen2", "panen"])
        lebih = (tetap / LBS).clip(lower=1)                       # jangan melebihi luas baku
        for f in o:
            o[f] = o[f] / lebih
        sisa = (LBS - sum(o.values())).clip(lower=0)
        acuan = t - 30 if t - 30 <= t0 else t0
        r = (P["siap"].loc[acuan] / (P["siap"].loc[acuan] + P["bera"].loc[acuan])).fillna(0.2)
        o["siap"], o["bera"] = sisa * r, sisa * (1 - r)
        hasil[t] = (o, metode[t])
    return hasil, T_est


def uji_mundur(P, LBS, fb, t_akhir_resmi, n_asal=24, k_maks=11):
    rows = []
    for t0 in range(t_akhir_resmi - n_asal, t_akhir_resmi):
        koef = fit_lag(P, t0); m = fit_tanam(P, LBS, fb, t0)
        hasil, _ = proyeksi(P, LBS, fb, t0, min(t_akhir_resmi, t0 + k_maks), m, koef)
        for t, (o, _) in hasil.items():
            for f in FASE:
                akt = P[f].loc[t]; basi = P[f].loc[t0]
                rows.append((f, t - t0, o[f].sum(), akt.sum(), basi.sum(), (o[f] - akt).abs().sum(), (basi - akt).abs().sum()))
    D = pd.DataFrame(rows, columns=["fase", "horizon", "pred", "akt", "basi", "ae_kec", "ae_kec_basi"])
    g = D.groupby(["fase", "horizon"])
    A = pd.DataFrame({
        "mape_diy_pct": g.apply(lambda x: 100 * (x.pred - x.akt).abs().sum() / x.akt.sum()),
        "mape_diy_basi_pct": g.apply(lambda x: 100 * (x.basi - x.akt).abs().sum() / x.akt.sum()),
        "mape_kec_pct": g.apply(lambda x: 100 * x.ae_kec.sum() / x.akt.sum()),
        "mape_kec_basi_pct": g.apply(lambda x: 100 * x.ae_kec_basi.sum() / x.akt.sum()),
        "n_asal": g.size()}).round(1).reset_index()
    return A


def arsipkan(df_diy, dibuat, t0):
    os.makedirs(os.path.dirname(ARSIP), exist_ok=True)
    baru = df_diy.assign(dibuat_utc=dibuat, periode_terbit_terakhir=kode_dari_t(t0))
    if os.path.exists(ARSIP):
        lama = pd.read_csv(ARSIP)
        kunci = ["periode_kode"] + [KOL[f] for f in FASE]
        akhir = lama.sort_values("dibuat_utc").groupby("periode_kode").tail(1)
        gab = baru.merge(akhir[kunci], on="periode_kode", how="left", suffixes=("", "_lama"))
        beda = np.zeros(len(gab), bool)
        for f in FASE:
            c = KOL[f]; beda |= ~np.isclose(gab[c], gab[c + "_lama"], atol=1.0) | gab[c + "_lama"].isna()
        baru = baru[beda.values]
        if baru.empty:
            return 0
        pd.concat([lama, baru]).to_csv(ARSIP, index=False)
    else:
        baru.to_csv(ARSIP, index=False)
    return len(baru)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--s1", default=os.environ.get("SIGAP_S1_DERET", S1_URL))
    ap.add_argument("--hari-ini", default=None)
    a = ap.parse_args()
    kini = datetime.now(timezone.utc)
    hari_ini = date.fromisoformat(a.hari_ini) if a.hari_ini else (kini + timedelta(hours=8)).date()   # WITA
    P, meta = muat_resmi()
    LBS = meta.luas_baku_sawah_ha
    t0 = int(P["tanam"].dropna(how="all").index.max())
    t_now = t_dari_tanggal(hari_ini)
    fb, s1_akhir = muat_s1(a.s1, meta.index)
    print(f"SIMOTANDI terbit s.d. {kode_dari_t(t0)} ({rentang_t(t0)[1]}); hari ini {hari_ini} = periode {kode_dari_t(t_now)}; "
          f"Sentinel-1 s.d. {s1_akhir}")
    if t_now <= t0:
        print("Data resmi sudah mencakup periode berjalan; nowcast tidak diperlukan.")
    koef = fit_lag(P, t0); m = fit_tanam(P, LBS, fb, t0)
    print("koef kohort:", {f: {l: round(c, 2) for l, c in v.items()} for f, v in koef.items()})
    print(f"model tanam: b_sentinel1={m['b']}, c_musiman={m['c'] if m['c'] is not None else m['c_sea']}")
    t_akhir = max(t_now, t0) + H_OUTLOOK
    hasil, T_est = proyeksi(P, LBS, fb, t0, t_akhir, m, koef)

    # --- uji mundur
    akurasi = uji_mundur(P, LBS, fb, t0)
    akurasi.to_csv(OUT_AKURASI, index=False)
    mape = akurasi.set_index(["fase", "horizon"]).mape_diy_pct

    # --- nowcast per kecamatan: t0+1 .. t_now
    dibuat = kini.isoformat(timespec="seconds")
    rows = []
    for t in range(t0 + 1, max(t_now, t0) + 1):
        o, met = hasil[t]; a0, a1 = rentang_t(t)
        for k in meta.index:
            r = {"periode_kode": kode_dari_t(t), "periode_mulai": a0.isoformat(), "periode_selesai": a1.isoformat(),
                 "kabupaten_kota": meta.at[k, "kabupaten_kota"], "kecamatan": meta.at[k, "kecamatan"], "kode_wilayah": k}
            for f in FASE:
                r[KOL[f]] = round(float(o[f][k]), 2)
            r.update(luas_baku_sawah_ha=LBS[k], horizon_periode=t - t0, metode_tanam=met[k],
                     periode_terbit_terakhir=kode_dari_t(t0), dibuat_utc=dibuat)
            rows.append(r)
    now = pd.DataFrame(rows)
    now.to_csv(OUT_NOW, index=False)

    # --- prakiraan panen per kabupaten & DIY
    rp = []
    for t in range(t0 + 1, t_akhir + 1):
        o, met = hasil[t]; a0, a1 = rentang_t(t)
        lag_t = [t - l for l in LAG["panen"]]
        sumber = "resmi (kohort tanam)" if max(lag_t) <= t0 else ("sentinel-1" if (fb is not None and max(lag_t) <= fb.index.max()) else "pola musiman")
        e = mape.get(("panen", min(t - t0, int(akurasi.horizon.max()))), np.nan)
        for kab, g in list(meta.groupby("kabupaten_kota").groups.items()) + [("DIY", meta.index)]:
            v = float(o["panen"][g].sum())
            rp.append({"periode_kode": kode_dari_t(t), "periode_mulai": a0.isoformat(), "periode_selesai": a1.isoformat(),
                       "wilayah": kab, "panen_ha_perkiraan": round(v, 1),
                       "batas_bawah_ha": round(v * (1 - e / 100), 1) if np.isfinite(e) else None,
                       "batas_atas_ha": round(v * (1 + e / 100), 1) if np.isfinite(e) else None,
                       "horizon_periode": t - t0, "sumber_tanam": sumber, "galat_tipikal_pct": e,
                       "periode_terbit_terakhir": kode_dari_t(t0), "dibuat_utc": dibuat})
    pd.DataFrame(rp).to_csv(OUT_PANEN, index=False)

    # --- arsip versi (tingkat DIY)
    diy = now.groupby(["periode_kode", "periode_mulai", "periode_selesai", "horizon_periode"])[[KOL[f] for f in FASE]].sum().round(1).reset_index()
    n = arsipkan(diy, dibuat, t0)

    # --- ringkasan
    ak = now[now.periode_kode == kode_dari_t(max(t_now, t0))]
    print(f"\nNowcast DIY periode {kode_dari_t(max(t_now, t0))} (horizon {max(t_now, t0) - t0}):")
    for f in FASE:
        h = min(max(t_now, t0) - t0, int(akurasi.horizon.max()))
        print(f"  {f:6s} {ak[KOL[f]].sum():9,.0f} ha  (galat tipikal ±{mape.get((f, h), np.nan):.0f}%; "
              f"bila pakai angka terakhir apa adanya ±{akurasi.set_index(['fase','horizon']).mape_diy_basi_pct.get((f, h), np.nan):.0f}%)")
    print(f"metode tanam: {now.groupby('periode_kode').metode_tanam.value_counts().to_dict()}")
    print(f"arsip versi: +{n} baris")


if __name__ == "__main__":
    main()
