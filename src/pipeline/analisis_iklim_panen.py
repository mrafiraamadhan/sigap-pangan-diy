"""
Analisis hubungan HUJAN - EL NINO (ONI) - PANEN PADI untuk DIY.

Masukan (semua sudah ada di pipeline):
  data/curah_hujan_bulanan_diy.csv   hujan bulanan 5 kab/kota, 1991-kini (Open-Meteo ERA5)
  data/enso_oni.csv                  indeks ONI NOAA per musim 3-bulanan, 1950-kini
  data/simotandi_bulanan_diy.csv     luas tanam & panen padi bulanan (SIMOTANDI), 2024-kini

Keluaran: data/analisis_iklim_panen.json, dibaca papan pantau (tab Konteks &
Pasokan, bagian "Hujan, El Nino, dan Panen Padi").

Apa yang dihitung, dan bagaimana membacanya:
  1. Normal hujan bulanan DIY 1991-2020 dan anomali 36 bulan terakhir.
  2. ONI vs hujan DIY per bulan kalender (korelasi Pearson, ~35 tahun). Ini
     hubungan yang mapan secara ilmiah: El Nino (ONI positif) = kemarau lebih
     kering dan hujan datang terlambat di Jawa; nilai r di sini menguji apakah
     pola itu juga tampak pada data DIY.
  3. Komposit: hujan Juni-November pada tahun El Nino vs netral vs La Nina, dan
     bulan datangnya musim hujan (bulan pertama >= 150 mm setelah Agustus).
  4. Hujan dan ONI vs luas panen/tanam SIMOTANDI dengan jeda 0-6 bulan. Deret
     SIMOTANDI masih pendek (~30 bulan), jadi bagian ini DESKRIPTIF: ia
     menunjukkan irama musiman (hujan -> tanam -> panen 3-4 bulan kemudian),
     bukan bukti sebab-akibat.

Jalankan dari root repo: python src/pipeline/analisis_iklim_panen.py
"""

import json
import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "..", "data")
P_HUJAN = os.path.join(DATA, "curah_hujan_bulanan_diy.csv")
P_ONI = os.path.join(DATA, "enso_oni.csv")
P_SIMO = os.path.join(DATA, "simotandi_bulanan_diy.csv")
P_OUT = os.path.join(DATA, "analisis_iklim_panen.json")

NORMAL_AWAL, NORMAL_AKHIR = 1991, 2020
# Musim 3-bulanan ONI -> bulan tengahnya
MUSIM_KE_BULAN = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
                  "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}
NAMA_BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
AMBANG_ONSET_MM = 150.0   # bulan pertama >= 150 mm setelah Agustus = musim hujan mulai


def pearson(x, y):
    """r Pearson + nilai p dua sisi. Memakai scipy bila ada; kalau tidak, pendekatan
    Fisher-z (cukup untuk n >= 10)."""
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
    m = ~(np.isnan(x) | np.isnan(y))
    x, y = x[m], y[m]
    n = int(len(x))
    if n < 4 or np.std(x) == 0 or np.std(y) == 0:
        return None, None, n
    r = float(np.corrcoef(x, y)[0, 1])
    try:
        from scipy import stats
        p = float(stats.pearsonr(x, y)[1])
    except Exception:
        z = math.atanh(max(min(r, 0.999999), -0.999999)) * math.sqrt(max(n - 3, 1))
        p = math.erfc(abs(z) / math.sqrt(2))
    return round(r, 3), round(p, 4), n


def muat_hujan():
    h = pd.read_csv(P_HUJAN)
    h = h[h["hari_data"] >= 25]                       # buang bulan yang datanya tidak utuh
    g = h.groupby(["tahun", "bulan"])["hujan_mm"].mean().reset_index()   # rata-rata 5 kab/kota
    g["hujan_mm"] = g["hujan_mm"].round(1)
    return h, g


def muat_oni():
    o = pd.read_csv(P_ONI)
    o["bulan"] = o["musim"].map(MUSIM_KE_BULAN)
    o = o.dropna(subset=["bulan"])
    o["bulan"] = o["bulan"].astype(int)
    o = o.sort_values("diambil_pada_utc").drop_duplicates(["tahun", "bulan"], keep="last")
    return o[["tahun", "bulan", "anomali_nino34_c", "status_enso", "musim"]].rename(columns={"anomali_nino34_c": "oni"})


def muat_simotandi():
    if not os.path.isfile(P_SIMO):
        return None
    s = pd.read_csv(P_SIMO)
    s = s[s["level"] == "provinsi"].sort_values("diambil_pada_utc").drop_duplicates(["tahun", "bulan"], keep="last")
    return s[["tahun", "bulan", "tanam_ha", "panen_ha"]].sort_values(["tahun", "bulan"]).reset_index(drop=True)


def main():
    if not os.path.isfile(P_HUJAN):
        print("Belum ada data curah hujan; jalankan src/sources/curah_hujan_openmeteo.py dulu."); return 0
    h_kab, hujan = muat_hujan()
    oni = muat_oni()
    simo = muat_simotandi()
    hasil = {"dibuat_utc": datetime.now(timezone.utc).isoformat(),
             "sumber": {"hujan": "Open-Meteo Historical Weather API (reanalisis ERA5/ERA5-Land ECMWF), rata-rata 5 titik kab/kota DIY",
                        "oni": "NOAA CPC Oceanic Nino Index", "panen": "SIMOTANDI Kementan, agregat provinsi DIY"}}

    # ---------- 1. normal & anomali ----------
    norm = hujan[(hujan["tahun"] >= NORMAL_AWAL) & (hujan["tahun"] <= NORMAL_AKHIR)].groupby("bulan")["hujan_mm"]
    normal = norm.mean().round(1).to_dict()
    normal_sd = norm.std().round(1).to_dict()
    hujan = hujan.merge(oni[["tahun", "bulan", "oni", "status_enso"]], on=["tahun", "bulan"], how="left")
    hujan["normal"] = hujan["bulan"].map(normal)
    hujan["anomali_pct"] = ((hujan["hujan_mm"] - hujan["normal"]) / hujan["normal"] * 100).round(0)
    seri36 = hujan.sort_values(["tahun", "bulan"]).tail(36)
    terakhir = seri36.iloc[-1]
    per_kab_terakhir = h_kab[(h_kab["tahun"] == terakhir["tahun"]) & (h_kab["bulan"] == terakhir["bulan"])]
    norm_kab = h_kab[(h_kab["tahun"] >= NORMAL_AWAL) & (h_kab["tahun"] <= NORMAL_AKHIR)].groupby(["kabupaten_kota", "bulan"])["hujan_mm"].mean()
    hasil["hujan"] = {
        "periode_normal": f"{NORMAL_AWAL}-{NORMAL_AKHIR}",
        "normal_bulanan": [{"bulan": b, "nama": NAMA_BULAN[b - 1], "mm": normal.get(b), "sd": normal_sd.get(b)} for b in range(1, 13)],
        "seri": [{"ym": f"{int(r.tahun)}-{int(r.bulan):02d}", "mm": r.hujan_mm, "normal": r.normal,
                  "anomali_pct": None if pd.isna(r.anomali_pct) else int(r.anomali_pct),
                  "oni": None if pd.isna(r.oni) else float(r.oni), "status": None if pd.isna(r.oni) else r.status_enso}
                 for r in seri36.itertuples()],
        "terakhir": {"ym": f"{int(terakhir.tahun)}-{int(terakhir.bulan):02d}", "mm": float(terakhir.hujan_mm),
                     "normal": float(terakhir.normal), "anomali_pct": None if pd.isna(terakhir.anomali_pct) else int(terakhir.anomali_pct)},
        "per_kab_terakhir": [{"kab": r.kabupaten_kota, "mm": float(r.hujan_mm),
                              "normal": round(float(norm_kab.get((r.kabupaten_kota, int(r.bulan)), np.nan)), 1)}
                             for r in per_kab_terakhir.itertuples()],
    }

    # ---------- 2. ONI vs hujan per bulan kalender ----------
    th_akhir_penuh = int(hujan["tahun"].max()) - 1
    basis = hujan[(hujan["tahun"] >= NORMAL_AWAL) & (hujan["tahun"] <= th_akhir_penuh)]
    per_bulan = []
    for b in range(1, 13):
        d = basis[basis["bulan"] == b]
        r, p, n = pearson(d["oni"], d["hujan_mm"])
        per_bulan.append({"bulan": b, "nama": NAMA_BULAN[b - 1], "r": r, "p": p, "n": n})

    # ---------- 3. komposit kemarau & awal musim hujan ----------
    tahunan = []
    for th in range(NORMAL_AWAL, th_akhir_penuh + 1):
        d = hujan[hujan["tahun"] == th].set_index("bulan")
        if not all(b in d.index for b in range(6, 13)):
            continue
        kemarau = float(d.loc[6:11, "hujan_mm"].sum())
        oni_aso = d.loc[9, "oni"] if 9 in d.index else np.nan           # ASO = puncak kemarau
        onset = next((b for b in range(9, 13) if d.loc[b, "hujan_mm"] >= AMBANG_ONSET_MM), 13)  # 13 = baru Januari
        tahunan.append({"tahun": th, "hujan_jun_nov": round(kemarau, 0), "oni_aso": None if pd.isna(oni_aso) else float(oni_aso), "onset_bulan": onset})
    tdf = pd.DataFrame(tahunan)
    r_k, p_k, n_k = pearson(tdf["oni_aso"], tdf["hujan_jun_nov"])
    r_o, p_o, n_o = pearson(tdf["oni_aso"], tdf["onset_bulan"])
    kelas = lambda v: "El Nino" if v >= 0.5 else ("La Nina" if v <= -0.5 else "Netral")  # kunci tanpa diakritik; teks tampilan memakai ñ
    tdf["kelas"] = tdf["oni_aso"].map(lambda v: None if pd.isna(v) else kelas(v))
    komposit = []
    for k in ["El Nino", "Netral", "La Nina"]:
        d = tdf[tdf["kelas"] == k]
        if len(d):
            komposit.append({"kelas": k, "n_tahun": int(len(d)), "hujan_jun_nov_mm": round(float(d["hujan_jun_nov"].mean()), 0),
                             "onset_rata": round(float(d["onset_bulan"].mean()), 1),
                             "tahun": [int(t) for t in d["tahun"]]})
    hasil["oni_vs_hujan"] = {
        "periode": f"{NORMAL_AWAL}-{th_akhir_penuh}", "per_bulan": per_bulan,
        "kemarau": {"r": r_k, "p": p_k, "n": n_k, "keterangan": "ONI Agu-Sep-Okt vs total hujan Juni-November tahun yang sama"},
        "onset": {"r": r_o, "p": p_o, "n": n_o, "ambang_mm": AMBANG_ONSET_MM,
                  "keterangan": "ONI Agu-Sep-Okt vs bulan pertama hujan >= 150 mm (9=Sep ... 12=Des, 13=baru Januari)"},
        "komposit": komposit,
        "tahunan": tahunan,
    }

    # ---------- 4. hujan & ONI vs panen/tanam SIMOTANDI (jeda) ----------
    if simo is not None and len(simo) >= 12:
        hs = hujan[["tahun", "bulan", "hujan_mm", "oni"]].copy()
        hs["idx"] = hs["tahun"] * 12 + hs["bulan"]
        simo["idx"] = simo["tahun"] * 12 + simo["bulan"]
        def jeda_tabel(kolom_x, kolom_y, maks):
            out = []
            for k in range(0, maks + 1):
                g = simo[["idx", kolom_y]].merge(hs[["idx", kolom_x]].assign(idx=lambda d: d["idx"] + k), on="idx", how="inner")
                r, p, n = pearson(g[kolom_x], g[kolom_y])
                out.append({"jeda_bulan": k, "r": r, "p": p, "n": n})
            return out
        hp = jeda_tabel("hujan_mm", "panen_ha", 6)
        ht = jeda_tabel("hujan_mm", "tanam_ha", 4)
        op = jeda_tabel("oni", "panen_ha", 6)
        terbaik = lambda tab: max([t for t in tab if t["r"] is not None], key=lambda t: abs(t["r"]), default=None)
        hasil["hujan_vs_panen"] = {"per_jeda": hp, "terbaik": terbaik(hp)}
        hasil["hujan_vs_tanam"] = {"per_jeda": ht, "terbaik": terbaik(ht)}
        hasil["oni_vs_panen"] = {"per_jeda": op, "terbaik": terbaik(op)}
        hasil["simotandi"] = {"bulan_awal": f"{int(simo.tahun.iloc[0])}-{int(simo.bulan.iloc[0]):02d}",
                              "bulan_akhir": f"{int(simo.tahun.iloc[-1])}-{int(simo.bulan.iloc[-1]):02d}", "n_bulan": int(len(simo)),
                              "seri": [{"ym": f"{int(r.tahun)}-{int(r.bulan):02d}", "tanam_ha": round(float(r.tanam_ha), 0), "panen_ha": round(float(r.panen_ha), 0)} for r in simo.itertuples()]}
    else:
        hasil["hujan_vs_panen"] = hasil["hujan_vs_tanam"] = hasil["oni_vs_panen"] = None
        hasil["simotandi"] = None

    # ---------- 5. ringkasan bahasa sehari-hari ----------
    teks = []
    t = hasil["hujan"]["terakhir"]
    if t["anomali_pct"] is not None:
        arah = "lebih sedikit" if t["anomali_pct"] < 0 else "lebih banyak"
        teks.append(f"Hujan {NAMA_BULAN[int(t['ym'][5:7]) - 1]} {t['ym'][:4]} di DIY rata-rata {t['mm']:.0f} mm, "
                    f"{abs(t['anomali_pct'])}% {arah} dari normalnya ({t['normal']:.0f} mm).")
    if r_k is not None:
        kuat = "kuat" if abs(r_k) >= 0.5 else "sedang" if abs(r_k) >= 0.3 else "lemah"
        teks.append(f"Dalam {n_k} tahun ({NORMAL_AWAL}-{th_akhir_penuh}), makin tinggi ONI Agustus-Oktober, makin sedikit hujan Juni-November di DIY "
                    f"(r = {r_k:+.2f}, hubungan {kuat}{', signifikan' if p_k is not None and p_k < 0.05 else ''}).")
    kel = {k["kelas"]: k for k in komposit}
    if "El Nino" in kel and "La Nina" in kel:
        teks.append(f"Pada tahun El Niño, hujan Juni-November rata-rata {kel['El Nino']['hujan_jun_nov_mm']:.0f} mm "
                    f"dibanding {kel['La Nina']['hujan_jun_nov_mm']:.0f} mm pada tahun La Niña"
                    + (f" dan {kel['Netral']['hujan_jun_nov_mm']:.0f} mm pada tahun netral" if "Netral" in kel else "") + ".")
        on_e, on_l = kel["El Nino"]["onset_rata"], kel["La Nina"]["onset_rata"]
        if on_e and on_l:
            teks.append(f"Musim hujan (bulan pertama >= {AMBANG_ONSET_MM:.0f} mm) rata-rata datang pada bulan ke-{on_e:.1f} saat El Niño "
                        f"dan ke-{on_l:.1f} saat La Niña (9 = September, 10 = Oktober, 11 = November); El Niño menunda hujan sekitar "
                        f"{abs(on_e - on_l):.1f} bulan.")
    if hasil.get("hujan_vs_panen") and hasil["hujan_vs_panen"]["terbaik"]:
        b = hasil["hujan_vs_panen"]["terbaik"]
        teks.append(f"Pada data SIMOTANDI ({hasil['simotandi']['n_bulan']} bulan), luas panen paling sejalan dengan hujan {b['jeda_bulan']} bulan sebelumnya "
                    f"(r = {b['r']:+.2f}): irama hujan -> tanam -> panen. Deretnya masih pendek, jadi ini gambaran musiman, bukan bukti sebab-akibat.")
    hasil["ringkasan_teks"] = " ".join(teks)

    with open(P_OUT, "w", encoding="utf-8") as f:
        json.dump(hasil, f, ensure_ascii=False, indent=1, default=lambda o: None if (isinstance(o, float) and math.isnan(o)) else (o.item() if hasattr(o, "item") else str(o)))
    print(hasil["ringkasan_teks"])
    print(f"Tersimpan {os.path.relpath(P_OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
