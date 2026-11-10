"""
Penarik harga pangan SP2KP Kementerian Perdagangan untuk DIY -- per PASAR, per hari.

=== KENAPA SUMBER INI PENTING ===
SP2KP adalah basis resmi penghitungan Indeks Perkembangan Harga (IPH). Dibanding
PIHPS dan DPKP, SP2KP lebih padat waktunya (harga berubah pada ~6% hari kalender),
rinci sampai titik pasar (sebaran antarpasar dipakai sebagai penskala volatilitas
prakiraan), dan rinci sampai varian (52 varian aktif di DIY).

=== ASAL ENDPOINT (ditemukan 10 Nov 2026) ===
Versi sebelumnya menebak-nebak nama parameter untuk /report/api/average-price-public
dan selalu gagal (API mengembalikan 10 record bawaan). Endpoint dan parameter di
bawah dibaca langsung dari permintaan yang dikirim halaman resmi
sp2kp.kemendag.go.id/statistik/area ("Statistik Perubahan Harga Wilayah"):

  POST https://api-sp2kp.kemendag.go.id/report/api/average-price/export-area-daily-json
  form-data: start_date, end_date (YYYY-MM-DD), level, kode_provinsi, kode_kab_kota,
             pasar_id, variant_ids (kosong = semua), skip_sat_sun, tipe_komoditas=1
  level: 1 = provinsi, 2 = kab/kota (wajib kode_kab_kota), 3 = pasar (wajib pasar_id)

Daftar pasar: GET /master/api/pasar?kode_provinsi=34&tipe_komoditas_id=1&is_nasional=true
Diverifikasi: Beras Medium Pasar Beringharjo 12-16 Okt 2026 = Rp13.000 dan Pasar
Wates = Rp13.150, persis sama dengan Excel unduhan manual. Riwayat per pasar tersedia
sampai Feb 2024 (termasuk Imogiri, Sleman, Wates yang di data kita baru mulai Agt 2026).

=== PENGAMAN ===
Baris yang tumpang tindih dengan riwayat dibandingkan dulu. Bila lebih dari 2% harga
berbeda, hasil run TIDAK ditulis (riwayat lama aman) dan contohnya dicetak ke log.

Cara pakai:
    python src/sources/sp2kp_kemendag.py                     # 30 hari terakhir
    python src/sources/sp2kp_kemendag.py --hari-mundur 1010  # isi riwayat sejak Feb 2024
Keluaran: data/harga_sp2kp_diy.csv
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sp2kp_dari_excel import satuan_rapi  # noqa: E402  (normalisasi satuan yang sama dgn jalur Excel)

BASE = "https://api-sp2kp.kemendag.go.id"
EP_DATA = f"{BASE}/report/api/average-price/export-area-daily-json"
EP_PASAR = f"{BASE}/master/api/pasar"
KODE_PROVINSI = "34"
KAB = {"3401": "Kab. Kulon Progo", "3402": "Kab. Bantul", "3403": "Kab. Gunungkidul",
       "3404": "Kab. Sleman", "3471": "Kota Yogyakarta"}
KELUARAN = os.path.join("data", "harga_sp2kp_diy.csv")
KOLOM = ["tanggal", "kabupaten_kota", "pasar", "komoditas", "varian", "satuan", "harga", "diambil_pada_utc"]
KUNCI = ["tanggal", "pasar", "komoditas", "varian"]
BATAS_BEDA = 0.02

HEAD = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": "https://sp2kp.kemendag.go.id/",
    "Origin": "https://sp2kp.kemendag.go.id",
}


def catat(*a):
    print(*a, flush=True)


def minta(metode, url, coba=3, **kw):
    for i in range(coba):
        try:
            r = requests.request(metode, url, headers=HEAD, timeout=90, **kw)
            if r.status_code == 200:
                return r.json()
            catat(f"    HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code in (401, 403, 422):
                return None
        except (requests.RequestException, ValueError) as e:
            catat(f"    galat: {type(e).__name__}: {str(e)[:160]}")
        time.sleep(4 * (i + 1))
    return None


def daftar_pasar():
    j = minta("GET", EP_PASAR, params={"take": 9999, "kode_provinsi": KODE_PROVINSI, "kode_kab_kota": "",
                                       "tipe_komoditas_id": 1, "is_nasional": "true"})
    data = (j or {}).get("data") if isinstance(j, dict) else j
    return [{"id": str(p["id"]), "nama": str(p["nama"]).strip(), "kab": str(p.get("kode_kab_kota", ""))}
            for p in (data or []) if str(p.get("kode_provinsi", KODE_PROVINSI)) == KODE_PROVINSI]


def tarik(pasar, awal, akhir):
    form = {"start_date": awal.isoformat(), "end_date": akhir.isoformat(), "level": "3",
            "variant_ids": "", "kode_provinsi": KODE_PROVINSI, "kode_kab_kota": pasar["kab"],
            "pasar_id": pasar["id"], "skip_sat_sun": "false", "tipe_komoditas": "1"}
    # files={k: (None, v)} -> multipart/form-data, sama seperti FormData di peramban
    j = minta("POST", EP_DATA, files={k: (None, v) for k, v in form.items()})
    if not isinstance(j, dict) or j.get("status") != "success":
        catat(f"    balasan tidak sukses: {str(j)[:200]}")
        return None
    return j.get("data") or []


def ke_baris(pasar, data, peta_kom, peta_sat, diambil):
    out = []
    for v in data:
        varian = str(v.get("variant", "")).strip()
        if not varian:
            continue
        kuant = v.get("kuantitas")
        sat_api = str(v.get("satuan", "")).strip()
        satuan = peta_sat.get(varian) or satuan_rapi(f"{kuant}{sat_api}" if kuant not in (None, 1, "1") else sat_api)
        komoditas = peta_kom.get(varian) or varian.split()[0]
        for d in v.get("daftarHarga") or []:
            h = d.get("harga")
            if h is None or float(h) <= 0:
                continue
            out.append({"tanggal": d["date"], "kabupaten_kota": KAB.get(pasar["kab"], pasar["kab"]),
                        "pasar": pasar["nama"], "komoditas": komoditas, "varian": varian,
                        "satuan": satuan, "harga": float(h), "diambil_pada_utc": diambil})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hari-mundur", type=int, default=30,
                    help="tarik berapa hari ke belakang (1010 = sejak Feb 2024)")
    ap.add_argument("--potong-hari", type=int, default=366, help="panjang satu permintaan per pasar")
    a = ap.parse_args()

    akhir = date.today()
    awal = akhir - timedelta(days=a.hari_mundur)
    catat(f"SP2KP: menarik {awal} s.d. {akhir} per pasar (endpoint export-area-daily-json, level 3)")

    lama = pd.read_csv(KELUARAN) if os.path.exists(KELUARAN) else pd.DataFrame(columns=KOLOM)
    peta_kom = lama.drop_duplicates("varian", keep="last").set_index("varian")["komoditas"].to_dict() if len(lama) else {}
    peta_sat = lama.drop_duplicates("varian", keep="last").set_index("varian")["satuan"].to_dict() if len(lama) else {}

    pasar = daftar_pasar()
    if not pasar:
        catat("GAGAL: daftar pasar DIY kosong. Riwayat TIDAK diubah.")
        return 0
    catat(f"{len(pasar)} pasar: " + ", ".join(f"{p['nama']} ({p['kab']})" for p in pasar))

    diambil = datetime.now(timezone.utc).isoformat(timespec="seconds")
    baru = []
    for p in pasar:
        t = awal
        while t <= akhir:
            t2 = min(t + timedelta(days=a.potong_hari - 1), akhir)
            data = tarik(p, t, t2)
            if data is None:
                catat(f"  {p['nama']} {t}..{t2}: GAGAL")
            else:
                rows = ke_baris(p, data, peta_kom, peta_sat, diambil)
                catat(f"  {p['nama']} {t}..{t2}: {len(data)} varian, {len(rows)} harga")
                baru.extend(rows)
            t = t2 + timedelta(days=1)
            time.sleep(1)
    if not baru:
        catat("TIDAK ADA DATA. Riwayat TIDAK diubah; langkah ini sengaja tidak menggagalkan pipeline.")
        return 0

    df = pd.DataFrame(baru, columns=KOLOM)
    if len(lama):
        for k in KUNCI[1:]:
            lama[k] = lama[k].fillna("").astype(str)
        irisan = lama.merge(df, on=KUNCI, suffixes=("_lama", "_baru"))
        beda = irisan[(irisan.harga_lama - irisan.harga_baru).abs() > 0.5]
        porsi = len(beda) / len(irisan) if len(irisan) else 0
        catat(f"Tumpang tindih dengan riwayat: {len(irisan)} baris, {len(beda)} beda harga ({porsi:.1%})")
        if len(beda):
            catat("  contoh: " + str(beda[KUNCI + ["harga_lama", "harga_baru"]].head(5).to_dict("records")))
        if porsi > BATAS_BEDA:
            catat(f"DIBATALKAN: beda harga > {BATAS_BEDA:.0%}. Riwayat TIDAK diubah; periksa contoh di atas.")
            return 0
        asing = sorted(set(df.varian) - set(lama.varian))
        if asing:
            catat(f"  varian baru (belum ada di riwayat): {asing[:10]}")
        # Hanya baris BARU atau yang harganya BERUBAH yang ditulis; baris yang sama persis
        # dibiarkan apa adanya supaya berkas (dan riwayat git) tidak berubah tanpa alasan.
        sama = set(map(tuple, irisan.loc[(irisan.harga_lama - irisan.harga_baru).abs() <= 0.5, KUNCI].values))
        df = df[[tuple(r) not in sama for r in df[KUNCI].values]]
        if df.empty:
            catat("Tidak ada harga baru atau berubah; berkas tidak ditulis ulang.")
            return 0
        sebelum = len(lama)
        gab = pd.concat([lama, df], ignore_index=True)
    else:
        sebelum, gab = 0, df
    gab = gab.drop_duplicates(subset=KUNCI, keep="last").sort_values(KUNCI)
    os.makedirs(os.path.dirname(KELUARAN), exist_ok=True)
    gab[KOLOM].to_csv(KELUARAN, index=False)
    catat(f"Tersimpan {KELUARAN}: {len(gab)} baris (+{len(gab) - sebelum} baru), "
          f"{gab.tanggal.min()} s.d. {gab.tanggal.max()}, {gab.pasar.nunique()} pasar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
