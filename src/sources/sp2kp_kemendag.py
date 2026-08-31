"""
Penarik harga pangan SP2KP Kementerian Perdagangan untuk DIY.

=== KENAPA SUMBER INI PENTING ===
SP2KP adalah basis resmi penghitungan Indeks Perkembangan Harga (IPH) yang dipakai
Kemendag dan dirujuk dalam rapat pengendalian inflasi. Dibandingkan dua sumber lain
yang sudah ditarik pipeline ini, SP2KP unggul pada tiga hal:

  1. Kepadatan waktu. Harga berubah pada 6,3% hari kalender, dibanding DPKP 5,5%
     dan PIHPS hanya 2,8%. Perubahannya juga menyebar sepanjang bulan, sedangkan
     PIHPS hanya berubah pada tanggal 25 sampai 27.
  2. Rincian sampai titik pasar. Satu baris = satu tanggal x satu pasar x satu
     varian, sehingga sebaran harga ANTAR PASAR dapat dihitung. Sebaran itu
     dipakai sebagai penskala volatilitas pada modul prakiraan.
  3. Rincian varian. 84 varian, jauh lebih halus daripada 20 deret PIHPS.

=== CATATAN JUJUR TENTANG KODE INI ===
Endpoint di bawah diambil dari dokumentasi berkas unduhan resmi SP2KP. Nama
PARAMETER kueri tidak dapat diverifikasi dari lingkungan pengembangan (domain
kemendag.go.id diblokir dari sana), sehingga modul ini dirancang untuk
MENEMUKAN SENDIRI bentuk balasan API saat pertama kali berjalan:

  - mencoba beberapa kemungkinan nama parameter sampai ada yang mengembalikan data
  - mengenali bentuk balasan (list polos, {"data": [...]}, atau {"result": [...]})
  - memetakan nama kolom secara lentur (tanggal/date/tgl, harga/price/nilai, dst.)
  - MENCETAK satu record mentah ke log bila pemetaan gagal, supaya bentuk aslinya
    dapat dibaca dan modul ini diperbaiki tanpa menebak lagi

Jadi bila run pertama gagal, lihat log langkah ini: di sana tercetak contoh
balasan mentah yang dibutuhkan untuk membetulkan pemetaan.

Keluaran: data/harga_sp2kp_diy.csv
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

BASE = "https://api-sp2kp.kemendag.go.id"
EP_HARGA = f"{BASE}/report/api/average-price-public"
EP_PASAR = f"{BASE}/master/api/pasar"
EP_KOMODITAS = f"{BASE}/master/api/komoditas"

KODE_PROVINSI = "34"                                  # DI Yogyakarta
KODE_KABKOT = ["3401", "3402", "3403", "3404", "3471"]

KELUARAN = os.path.join("data", "harga_sp2kp_diy.csv")

# Peramban sungguhan. Beberapa portal kementerian menolak User-Agent yang
# menyebut kata "bot" atau "scraper" (pelajaran dari SIMOTANDI).
HEAD = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": "https://sp2kp.kemendag.go.id/",
    "Origin": "https://sp2kp.kemendag.go.id",
}

# Kemungkinan nama parameter tanggal & wilayah, dicoba berurutan.
POLA_PARAM = [
    {"tgl_awal": "start_date", "tgl_akhir": "end_date", "prov": "province_code", "kab": "regency_code"},
    {"tgl_awal": "tanggal_awal", "tgl_akhir": "tanggal_akhir", "prov": "kode_provinsi", "kab": "kode_kabkot"},
    {"tgl_awal": "startDate", "tgl_akhir": "endDate", "prov": "provinceId", "kab": "regencyId"},
    {"tgl_awal": "from", "tgl_akhir": "to", "prov": "provinsi", "kab": "kabkot"},
    {"tgl_awal": "date_start", "tgl_akhir": "date_end", "prov": "prov_id", "kab": "kab_id"},
]

# Pemetaan lentur nama kolom balasan -> nama kolom keluaran kita.
SINONIM = {
    "tanggal":       ["tanggal", "date", "tgl", "tanggal_harga", "price_date", "periode"],
    "kabupaten_kota":["kabupaten_kota", "kabupaten", "kab_kota", "regency", "regency_name", "nama_kabkot", "kabkot"],
    "pasar":         ["nama_pasar", "pasar", "market", "market_name", "nama_market"],
    "komoditas":     ["komoditas", "commodity", "nama_komoditas", "commodity_name"],
    "varian":        ["varian", "variant", "nama_varian", "variant_name", "jenis"],
    "satuan":        ["satuan", "unit", "uom"],
    "harga":         ["harga", "price", "average_price", "harga_rata_rata", "nilai", "avg_price"],
}


def catat(*a):
    print(*a, flush=True)


def ambil(url, params=None, percobaan=3):
    """GET dengan jeda bertambah. Mengembalikan objek JSON atau None."""
    for i in range(percobaan):
        try:
            r = requests.get(url, params=params, headers=HEAD, timeout=60)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    catat(f"    balasan 200 tetapi bukan JSON: {r.text[:200]}")
                    return None
            catat(f"    HTTP {r.status_code} untuk {r.url[:150]}")
            if r.status_code in (401, 403):
                catat(f"    isi balasan: {r.text[:300]}")
                return None                      # ditolak, tidak perlu diulang
        except requests.RequestException as e:
            catat(f"    galat jaringan: {type(e).__name__}: {str(e)[:160]}")
        time.sleep(3 * (i + 1))
    return None


def daftar_dari(obj):
    """Cari list record di dalam balasan yang bentuknya bermacam-macam."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for kunci in ("data", "result", "results", "items", "rows", "records", "content"):
            v = obj.get(kunci)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):                       # mis. {"data": {"rows": [...]}}
                dalam = daftar_dari(v)
                if dalam:
                    return dalam
        for v in obj.values():                            # usaha terakhir
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


# Kunci yang biasa memuat NAMA di dalam objek bersarang, diperiksa berurutan.
KUNCI_NAMA = ("nama", "name", "nama_pasar", "nama_komoditas", "nama_varian",
              "label", "title", "text", "deskripsi", "description", "value", "kode", "code")
KUNCI_ANGKA = ("harga", "price", "nilai", "value", "average_price", "avg_price",
               "harga_rata_rata", "rata_rata", "average", "avg")


def ratakan(v, dalam=0):
    """Pipihkan nilai bersarang menjadi satu nilai yang bisa di-hash.

    Pelajaran dari run 31 Agt: SP2KP mengembalikan sebagian kolom sebagai objek,
    misalnya "komoditas": {"id": 12, "nama": "Bawang"}. pandas.drop_duplicates
    memanggil factorize yang menuntut nilai bisa di-hash, sehingga dict mentah
    menggagalkan seluruh langkah dengan TypeError: unhashable type: 'dict'.
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        if dalam < 3:
            for target in KUNCI_NAMA:
                for k, w in v.items():
                    if str(k).lower().strip() == target:
                        hasil = ratakan(w, dalam + 1)
                        if hasil not in (None, ""):
                            return hasil
        for w in v.values():                       # usaha terakhir: skalar pertama
            if isinstance(w, (str, int, float)) and not isinstance(w, bool) and w != "":
                return w
        return json.dumps(v, ensure_ascii=False, sort_keys=True)[:120]
    if isinstance(v, (list, tuple)):
        bagian = [str(ratakan(x, dalam + 1)) for x in v if x is not None]
        return " | ".join(bagian) if bagian else None
    return str(v)


def ratakan_angka(v, dalam=0):
    """Seperti ratakan() tetapi mengutamakan isi yang benar-benar angka."""
    if isinstance(v, dict):
        if dalam < 3:
            for target in KUNCI_ANGKA:
                for k, w in v.items():
                    if str(k).lower().strip() == target:
                        hasil = ratakan_angka(w, dalam + 1)
                        if hasil is not None:
                            return hasil
        for w in v.values():
            if isinstance(w, (int, float)) and not isinstance(w, bool):
                return w
    return ratakan(v, dalam)


def petakan(rec):
    """Petakan satu record ke kolom baku kita. None bila tidak dikenali."""
    if not isinstance(rec, dict):
        return None
    kecil = {str(k).lower().strip(): v for k, v in rec.items()}
    out = {}
    for baku, kandidat in SINONIM.items():
        for c in kandidat:
            if c in kecil and kecil[c] not in (None, "", []):
                nilai = ratakan_angka(kecil[c]) if baku == "harga" else ratakan(kecil[c])
                if nilai not in (None, ""):
                    out[baku] = nilai
                    break
    # Modul peringkas mengelompokkan deret berdasarkan `varian`. Kalau balasan API
    # tidak memuatnya, komoditas dipakai supaya deretnya tidak hilang.
    if "varian" not in out and "komoditas" in out:
        out["varian"] = out["komoditas"]
    return out if ("tanggal" in out and "harga" in out) else None


# Kata yang menandakan wilayah DIY pada nama pasar / kabupaten.
DIY_KATA = ("yogyakarta", "jogja", "sleman", "bantul", "kulon progo", "kulonprogo",
            "gunungkidul", "gunung kidul", "wates", "wonosari", "sewon", "godean")


def wilayah_diy(b):
    """Apakah baris hasil pemetaan berasal dari DIY?"""
    teks = " ".join(str(b.get(k, "")) for k in ("kabupaten_kota", "pasar")).lower()
    return any(k in teks for k in DIY_KATA)


def bersihkan_riwayat():
    """Buang baris riwayat yang bukan dari DIY, lalu tulis ulang bila perlu.

    Dijalankan PALING AWAL, sebelum menyentuh API. Run 31 Agt sempat menuliskan
    10 baris harga baja ringan dari Banda Aceh ke riwayat. Baris seperti itu
    bukan cuma salah isinya: ia juga ikut jadi acuan "varian yang dikenal",
    sehingga balasan API yang salah justru tampak sah. Membersihkannya lebih
    dulu membuat riwayat memulihkan diri tanpa unggah ulang manual.
    """
    if not os.path.exists(KELUARAN):
        return
    try:
        d = pd.read_csv(KELUARAN)
    except Exception as e:
        catat(f"Riwayat tidak terbaca ({type(e).__name__}), pembersihan dilewati.")
        return
    if not {"kabupaten_kota", "pasar"} <= set(d.columns):
        return
    for k in ("kabupaten_kota", "pasar"):
        d[k] = d[k].fillna("").astype(str)
    bersih = d.apply(wilayah_diy, axis=1)
    n = int((~bersih).sum())
    if n:
        buang = sorted(set(d.loc[~bersih, "varian"].astype(str)))[:6] if "varian" in d else []
        catat(f"Membersihkan {n} baris riwayat yang bukan dari DIY: {buang}")
        d[bersih].to_csv(KELUARAN, index=False)
        catat(f"Riwayat kini {int(bersih.sum())} baris.")


def varian_riwayat():
    """Daftar varian yang SUDAH terbukti benar, dibaca dari riwayat."""
    if not os.path.exists(KELUARAN):
        return set()
    try:
        d = pd.read_csv(KELUARAN, usecols=["varian"])
        return {str(v).strip().lower() for v in d.varian.dropna().unique()}
    except Exception:
        return set()


def nilai_balasan(rows, awal, akhir, kenal):
    """Seberapa cocok balasan API dengan yang sebenarnya kita minta.

    Pelajaran run 31 Agt: pola parameter pertama mengembalikan 10 record, jadi
    dianggap berhasil. Padahal isinya harga baja ringan di Banda Aceh tertanggal
    Januari 2024. API MENERIMA parameter kita lalu MENGABAIKANNYA, dan modul ini
    berhenti mencoba pola lain karena mengira sudah berhasil.

    Karena itu "ada isinya" tidak pernah cukup. Balasan dinilai dulu: berapa
    persen barisnya jatuh di rentang tanggal yang diminta, berapa persen dari
    DIY, dan berapa persen varian yang dikenali dari riwayat.
    """
    baris = [b for b in (petakan(r) for r in rows) if b]
    if not baris:
        return {"n": 0, "tanggal": 0.0, "diy": 0.0, "kenal": 0.0}

    def frac(f):
        return sum(1 for b in baris if f(b)) / len(baris)

    def di_rentang(b):
        t = pd.to_datetime(b.get("tanggal"), errors="coerce")
        return bool(pd.notna(t) and awal <= t.date() <= akhir)

    return {
        "n": len(baris),
        "tanggal": frac(di_rentang),
        "diy": frac(wilayah_diy),
        "kenal": (frac(lambda b: str(b.get("varian", "")).strip().lower() in kenal)
                  if kenal else 1.0),
    }


def tarik_rentang(tgl_awal, tgl_akhir, kenal):
    """Coba tiap pola parameter, terima hanya yang balasannya benar-benar cocok."""
    terbaik = None
    for i, p in enumerate(POLA_PARAM, 1):
        params = {
            p["tgl_awal"]: tgl_awal.isoformat(),
            p["tgl_akhir"]: tgl_akhir.isoformat(),
            p["prov"]: KODE_PROVINSI,
        }
        catat(f"  pola parameter {i}/{len(POLA_PARAM)}: {list(params)}")
        js = ambil(EP_HARGA, params)
        rows = daftar_dari(js) if js else []
        if not rows:
            continue
        s = nilai_balasan(rows, tgl_awal, tgl_akhir, kenal)
        catat(f"    -> {len(rows)} record; dalam rentang tanggal {s['tanggal']:.0%}, "
              f"dari DIY {s['diy']:.0%}, varian dikenali {s['kenal']:.0%}")
        if s["diy"] >= 0.5 and s["tanggal"] >= 0.5:
            catat("    diterima: balasan cocok dengan yang diminta.")
            return rows, p
        catat("    DITOLAK: parameter tampaknya diabaikan API, pola lain dicoba.")
        if terbaik is None or s["diy"] > terbaik[1]["diy"]:
            terbaik = (rows, s, i)

    if terbaik:
        catat(f"Tidak ada pola parameter yang menghasilkan data DIY. Percobaan "
              f"terbaik (pola {terbaik[2]}) hanya {terbaik[1]['diy']:.0%} dari DIY "
              f"dan {terbaik[1]['tanggal']:.0%} di rentang tanggal.")
    return [], None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hari-mundur", type=int, default=30,
                    help="tarik berapa hari ke belakang (isi mis. 900 untuk mengisi histori)")
    ap.add_argument("--potong-hari", type=int, default=90,
                    help="ukuran potongan permintaan agar tidak menembus batas API")
    args = ap.parse_args()

    akhir = date.today()
    awal = akhir - timedelta(days=args.hari_mundur)
    catat(f"SP2KP: menarik {awal} s.d. {akhir} ({args.hari_mundur} hari)")

    bersihkan_riwayat()
    kenal = varian_riwayat()
    if kenal:
        catat(f"Riwayat memuat {len(kenal)} varian yang sudah terbukti benar; "
              "balasan API akan diperiksa terhadap daftar itu.")

    semua, pola = [], None
    t = awal
    while t <= akhir:
        t2 = min(t + timedelta(days=args.potong_hari - 1), akhir)
        catat(f"  potongan {t} s.d. {t2}")
        if pola is None:
            rows, pola = tarik_rentang(t, t2, kenal)
        else:
            params = {pola["tgl_awal"]: t.isoformat(), pola["tgl_akhir"]: t2.isoformat(),
                      pola["prov"]: KODE_PROVINSI}
            rows = daftar_dari(ambil(EP_HARGA, params) or {})
            catat(f"    -> {len(rows)} record")
        semua.extend(rows)
        t = t2 + timedelta(days=1)
        time.sleep(2)

    if not semua:
        catat("TIDAK ADA DATA DIY. Kemungkinan penyebab: nama parameter berbeda, "
              "API menuntut kunci akses, atau alamat IP runner diblokir.")
        catat("Riwayat yang sudah ada TIDAK diubah, jadi papan pantau tetap memakai "
              "data lama yang benar. Langkah ini sengaja TIDAK menggagalkan pipeline.")
        return 0

    catat(f"Total {len(semua)} record mentah.")
    catat("Contoh record mentah (untuk memperbaiki pemetaan bila perlu):")
    catat("  " + json.dumps(semua[0], ensure_ascii=False)[:600])

    baris = [b for b in (petakan(r) for r in semua) if b]
    if not baris:
        catat("PEMETAAN GAGAL: tidak ada record yang punya kolom tanggal + harga.")
        catat(f"  kunci yang tersedia: {sorted(semua[0].keys())}")
        return 0

    # Saringan terakhir per baris. Penilaian di tarik_rentang() memakai ambang
    # mayoritas, jadi beberapa baris nyasar masih bisa lolos. Data pangan DIY
    # tidak boleh tercampur harga bahan bangunan dari provinsi lain.
    sebelum_saring = len(baris)
    baris = [b for b in baris if wilayah_diy(b)]
    dibuang = sebelum_saring - len(baris)
    if dibuang:
        catat(f"{dibuang} baris dibuang karena bukan dari DIY.")
    if not baris:
        catat("SEMUA baris bukan dari DIY. Berkas riwayat TIDAK ditimpa.")
        return 0

    df = pd.DataFrame(baris)
    df["tanggal"] = pd.to_datetime(df["tanggal"], errors="coerce").dt.date
    df["harga"] = pd.to_numeric(df["harga"], errors="coerce")
    # Kolom teks dipaksa jadi teks. Ini yang mencegah TypeError saat dedup.
    for k in ("kabupaten_kota", "pasar", "komoditas", "varian", "satuan"):
        if k in df.columns:
            df[k] = df[k].map(lambda x: "" if x is None else str(ratakan(x)).strip())
    df = df.dropna(subset=["tanggal", "harga"]).query("harga > 0")

    if not len(df):
        catat("Semua record gugur saat pembersihan (tanggal/harga tidak terbaca).")
        catat("Berkas lama TIDAK ditimpa. Perbaiki pemetaan lebih dulu.")
        return 0

    catat(f"{len(df)} baris bersih. Contoh hasil pemetaan:")
    for _, b in df.head(2).iterrows():
        catat("  " + json.dumps({k: str(v) for k, v in b.items()}, ensure_ascii=False)[:400])

    df["diambil_pada_utc"] = datetime.now(timezone.utc).isoformat()

    kunci = [k for k in ("tanggal", "pasar", "komoditas", "varian") if k in df.columns]
    os.makedirs("data", exist_ok=True)
    if os.path.exists(KELUARAN):
        lama = pd.read_csv(KELUARAN)
        lama["tanggal"] = pd.to_datetime(lama["tanggal"], errors="coerce").dt.date
        for k in ("kabupaten_kota", "pasar", "komoditas", "varian", "satuan"):
            if k in lama.columns:
                lama[k] = lama[k].fillna("").astype(str).str.strip()
        # Kalau penamaan varian dari API tidak sama dengan riwayat yang sudah ada,
        # deret barunya akan dianggap komoditas lain dan modul prakiraan kehilangan
        # riwayat panjangnya. Ini diperiksa dan dilaporkan, bukan didiamkan.
        if "varian" in df.columns and "varian" in lama.columns:
            baru_set, lama_set = set(df.varian.unique()), set(lama.varian.unique())
            beririsan = baru_set & lama_set
            catat(f"Varian: {len(baru_set)} dari API, {len(lama_set)} di riwayat, "
                  f"{len(beririsan)} beririsan.")
            if not beririsan:
                catat("PERINGATAN: tidak ada satu pun nama varian yang cocok dengan "
                      "riwayat. Penamaan dari API kemungkinan berbeda; periksa contoh "
                      "di atas sebelum hasil prakiraan dipercaya.")
            elif len(baru_set - lama_set):
                catat(f"  varian baru yang belum ada di riwayat: "
                      f"{sorted(baru_set - lama_set)[:8]}")
        sebelum = len(lama)
        df = pd.concat([lama, df], ignore_index=True)
    else:
        sebelum = 0

    for k in kunci:
        if k != "tanggal":
            df[k] = df[k].fillna("").astype(str)
    df = df.drop_duplicates(subset=kunci, keep="last").sort_values(kunci)
    df.to_csv(KELUARAN, index=False)

    catat(f"Tersimpan {KELUARAN}: {len(df)} baris (+{len(df) - sebelum} dari run ini), "
          f"{df['komoditas'].nunique() if 'komoditas' in df else '?'} komoditas, "
          f"{df.tanggal.min()} s.d. {df.tanggal.max()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
