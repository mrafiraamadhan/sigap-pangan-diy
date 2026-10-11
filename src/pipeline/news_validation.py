"""
Lapisan validasi tekstual: untuk tiap anomali harga yang terdeteksi
(dari merge_and_detect.py), cari berita independen yang mengonfirmasi
kejadiannya -- mengikuti evidensi Bakry dkk. (2025, arXiv:2508.06497)
bahwa fusi sinyal harga+teks jauh mengungguli model harga semata
(AUC 0,94 vs 0,46 tanpa komponen berita).

=== VERSI 2 (29 Sep 2026): TIDAK LAGI BERGANTUNG PADA FIRECRAWL ===
Arsip kliping berhenti di 26 Agustus 2026 karena kredit Firecrawl habis
(API menjawab 402 "low on credits"). Versi ini memakai sumber GRATIS tanpa
kunci API, berurutan sampai ada yang memberi hasil:

  1. Google News RSS  (news.google.com/rss/search)  -- stabil, tanpa kunci,
     mendukung penyaring tanggal after:/before: sehingga berita yang diambil
     benar-benar dari bulan terjadinya lonjakan.
  2. Bing News RSS    (bing.com/news/search?format=rss) -- cadangan; memberi
     tautan langsung ke media dan cuplikan isi.
  3. Firecrawl        -- hanya bila FIRECRAWL_API_KEY masih diset dan dua
     sumber di atas kosong.

Perubahan lain:
  * Menyimpan sampai 3 artikel per pencarian (kolom `peringkat` 1..3), bukan
    hanya satu. Sebelumnya 3 hasil ditarik tetapi 2 dibuang, padahal itulah
    yang membuat arsip tampak "ratusan baris tapi artikelnya itu-itu saja".
  * Kolom baru `media` (nama media dari feed) dan `tanggal_terbit` (tanggal
    artikel), sehingga papan pantau dapat menampilkan tanggal artikel yang
    sebenarnya, bukan tanggal lonjakan.
  * Hasil diurutkan menurut relevansi sederhana: judul yang menyebut nama
    komoditas dan Yogyakarta/Jogja/DIY didahulukan.

Kolom lama tetap ada dan artinya tidak berubah, jadi papan pantau versi
lama pun masih bisa membaca berkas ini.

=== VERSI 3 (10 Okt 2026): PEMICU DARI SP2KP ===
Anomali (z-score) dari merge_and_detect.py membaca PIHPS, yang hanya bergerak di
akhir bulan (tanggal 25-29). Akibatnya kenaikan nyata di pasar di antara dua
siklus itu tidak pernah dicarikan beritanya (contoh: cabai merah keriting naik
60% dalam 30 hari per 9 Okt 2026 di SP2KP, sementara kliping terakhir 29 Sep).
Kini setiap varian SP2KP yang naik > 10% dalam 30 hari (ambang yang sama dengan
pilar SP2KP di papan pantau) ikut menjadi pemicu. Pencariannya dibatasi ke
jendela 30 hari kenaikan itu. Kolom baru `pemicu` mencatat asal tiap baris:
"anomali PIHPS (z-score)" atau "SP2KP naik N% dalam 30 hari".

Cara pakai:
    python news_validation.py                # mode pipeline (baca anomali, tambah arsip)
    python news_validation.py --uji "harga cabai Yogyakarta"   # coba satu query, cetak hasil
    python news_validation.py --maks 200     # dijalankan lokal: isi arsip lebih banyak dalam sekali jalan
"""

import argparse
import csv
import html
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ANOMALI_PATH = os.path.join(HERE, "..", "..", "data", "gabungan_anomali.csv")
# Versi ringkas SP2KP (rata-rata provinsi per varian per hari) hasil ringkas_sp2kp.py.
SP2KP_PATH = os.path.join(HERE, "..", "..", "docs", "data", "harga_sp2kp_diy.csv")
AMBANG_NAIK_SP2KP = 10.0      # persen dalam 30 hari; sama dengan ambang pilar SP2KP di papan pantau
MIN_PENGAMATAN_SP2KP = 60     # sama dengan papan pantau: varian berderet pendek dilewati
PEMICU_PIHPS = "anomali PIHPS (z-score)"
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "validasi_berita.csv")

API_KEY = os.environ.get("FIRECRAWL_API_KEY")
FIRECRAWL_URL = "https://api.firecrawl.dev/v1/search"

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

BULAN_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
            "Agustus", "September", "Oktober", "November", "Desember"]

KOLOM = ["tanggal", "komoditas", "kabupaten_kota", "query", "jumlah_berita_ditemukan",
         "judul_teratas", "url_teratas", "ringkasan", "tervalidasi",
         "media", "tanggal_terbit", "peringkat", "diambil_pada_utc", "pemicu"]

# Batasan kesopanan. RSS tidak punya kuota resmi, tetapi tetap diberi jeda.
MAKS_VALIDASI = 20        # maksimal query pencarian per run
JEDA_ANTAR_QUERY = 3      # detik
HASIL_PER_QUERY = 3       # artikel yang disimpan per pencarian


def catat(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- pembantu
def bersih_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def tanggal_iso(teks):
    """'Mon, 14 Sep 2026 03:10:00 GMT' -> '2026-09-14'. Kosong bila gagal."""
    if not teks:
        return ""
    for pola in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(teks.strip()[:31], pola).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(\d{4}-\d{2}-\d{2})", teks)
    return m.group(1) if m else ""


def ke_bulat(x, cadangan=0):
    """'3', '3.0', 3.0 -> 3; kosong/tak terbaca -> cadangan."""
    try:
        return int(float(str(x).strip()))
    except (TypeError, ValueError):
        return cadangan


def akhir_bulan(yyyy_mm):
    t, b = int(yyyy_mm[:4]), int(yyyy_mm[5:7])
    return f"{t + (b == 12):04d}-{(b % 12) + 1:02d}-01"


# ---------------------------------------------------------------- sumber 1: Google News RSS
def cari_google_news(query, limit, bulan=None, rentang=None):
    """bulan = 'YYYY-MM' untuk membatasi ke bulan lonjakan (opsional);
    rentang = ('YYYY-MM-DD', 'YYYY-MM-DD') untuk jendela tanggal eksplisit (opsional)."""
    q = query
    if rentang:
        q = f"{query} after:{rentang[0]} before:{rentang[1]}"
    elif bulan:
        q = f"{query} after:{bulan}-01 before:{akhir_bulan(bulan)}"
    url = "https://news.google.com/rss/search?" + urlencode({"q": q, "hl": "id", "gl": "ID", "ceid": "ID:id"})
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    hasil = []
    for it in root.findall("./channel/item"):
        judul = bersih_html(it.findtext("title"))
        src = it.find("source")
        media = (src.text or "").strip() if src is not None else ""
        # Judul Google News berakhiran " - Nama Media"; dipotong supaya rapi.
        if media and judul.endswith(" - " + media):
            judul = judul[: -len(media) - 3].strip()
        hasil.append({
            "title": judul,
            "url": (it.findtext("link") or "").strip(),
            "description": "",                       # feed pencarian Google tidak memuat cuplikan isi
            "media": media,
            "tanggal_terbit": tanggal_iso(it.findtext("pubDate")),
        })
        if len(hasil) >= limit:
            break
    return hasil


# ---------------------------------------------------------------- sumber 2: Bing News RSS
def cari_bing_news(query, limit):
    url = "https://www.bing.com/news/search?" + urlencode({"q": query, "format": "rss", "setlang": "id", "cc": "ID"})
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    ns = {"News": "https://www.bing.com/news/search?q=&format=rss"}
    hasil = []
    for it in root.findall("./channel/item"):
        src = it.find("News:Source", ns)
        hasil.append({
            "title": bersih_html(it.findtext("title")),
            "url": (it.findtext("link") or "").strip(),
            "description": bersih_html(it.findtext("description")),
            "media": (src.text or "").strip() if src is not None else "",
            "tanggal_terbit": tanggal_iso(it.findtext("pubDate")),
        })
        if len(hasil) >= limit:
            break
    return hasil


# ---------------------------------------------------------------- sumber 3: Firecrawl (opsional)
def cari_firecrawl(query, limit):
    if not API_KEY:
        return []
    resp = requests.post(FIRECRAWL_URL, headers={"Authorization": f"Bearer {API_KEY}"},
                         json={"query": query, "limit": limit}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    isi = data.get("data") if isinstance(data, dict) else data
    if isinstance(isi, dict):
        isi = isi.get("web") or isi.get("results") or []
    hasil = []
    for h in (isi if isinstance(isi, list) else []):
        hasil.append({"title": h.get("title", ""), "url": h.get("url", ""),
                      "description": h.get("description") or h.get("snippet") or "",
                      "media": "", "tanggal_terbit": ""})
    return hasil[:limit]


# ---------------------------------------------------------------- gabungan
def skor_relevansi(h, komoditas):
    """Judul yang menyebut komoditasnya dan wilayah DIY diutamakan."""
    teks = (h.get("title", "") + " " + h.get("description", "")).lower()
    kata = komoditas.lower().split()[0] if komoditas else ""
    s = 0
    if kata and kata in teks:
        s += 2
    if re.search(r"yogya|jogja|\bdiy\b|sleman|bantul|kulon progo|gunungkidul", teks):
        s += 1
    return s


KATA_PANGAN = re.compile(r"harga pangan|bahan pokok|sembako|bapok|inflasi|kebutuhan pokok|pasar tradisional", re.I)


def relevan_pangan(h, komoditas):
    """Judul/cuplikan harus menyebut komoditasnya (kata pertama: cabai, beras, gula,
    telur, daging, bawang) atau jelas-jelas soal harga pangan."""
    teks = (h.get("title", "") + " " + h.get("description", "")).lower()
    kata = (komoditas or "").lower().split()
    inti = [k for k in kata[:2] if len(k) >= 4]        # "cabai rawit" -> cabai, rawit
    if any(k in teks for k in inti):
        return True
    return bool(KATA_PANGAN.search(teks))


def dalam_rentang(h, rentang, kelonggaran_hari=3):
    """Artikel yang tanggal terbitnya diketahui harus jatuh di jendela kenaikan (± beberapa hari).
    Yang tanggalnya tidak diketahui dibiarkan lolos (penyaring komoditas tetap berlaku)."""
    t = h.get("tanggal_terbit") or ""
    if not rentang or not t:
        return True
    from datetime import date, timedelta
    try:
        d = date.fromisoformat(t[:10])
        a = date.fromisoformat(rentang[0]) - timedelta(days=kelonggaran_hari)
        b = date.fromisoformat(rentang[1]) + timedelta(days=kelonggaran_hari)
    except ValueError:
        return True
    return a <= d <= b


def cari_berita(query, limit=HASIL_PER_QUERY, bulan=None, komoditas="", rentang=None):
    """Coba tiap sumber berurutan; kembalikan list dict hasil (bisa kosong)."""
    percobaan = []
    if rentang:
        percobaan.append(("Google News RSS (jendela kenaikan)", lambda: cari_google_news(query, limit * 3, rentang=rentang)))
    elif bulan:
        percobaan.append(("Google News RSS (bulan lonjakan)", lambda: cari_google_news(query, limit * 3, bulan)))
    percobaan += [
        ("Google News RSS", lambda: cari_google_news(query, limit * 3)),
        ("Bing News RSS", lambda: cari_bing_news(query, limit * 3)),
        ("Firecrawl", lambda: cari_firecrawl(query, limit * 2)),
    ]
    for nama, fn in percobaan:
        try:
            hasil = [h for h in fn() if h.get("url")]
        except Exception as e:
            catat(f"    {nama}: gagal ({type(e).__name__}: {str(e)[:120]})")
            continue
        if hasil:
            # buang duplikat URL, lalu urutkan menurut relevansi (stabil: urutan feed dipertahankan)
            unik, sudah = [], set()
            for h in hasil:
                if h["url"] in sudah:
                    continue
                sudah.add(h["url"])
                unik.append(h)
            # Buang artikel yang tidak menyebut komoditasnya maupun soal harga pangan
            # (pelajaran run 29 Sep: query bulan lama mengembalikan "rujak lotis",
            # "cokelat pedas", berita APBD). Lebih baik kosong daripada menyesatkan.
            relevan = [h for h in unik if relevan_pangan(h, komoditas) and dalam_rentang(h, rentang)]
            relevan.sort(key=lambda h: -skor_relevansi(h, komoditas))
            catat(f"    {nama}: {len(unik)} artikel, {len(relevan)} relevan")
            if relevan:
                return relevan[:limit]
            continue
        catat(f"    {nama}: kosong")
    return []


def validasi_anomali(tanggal, komoditas, kabupaten, pemicu=PEMICU_PIHPS, rentang=None):
    """Bangun query dari konteks anomali; kembalikan daftar baris (satu per artikel).
    rentang = jendela tanggal kenaikan (untuk pemicu SP2KP); tanpa rentang dipakai bulan lonjakan."""
    diambil = datetime.now(timezone.utc).isoformat()
    bulan = str(tanggal)[:7]
    try:
        nama_bulan = BULAN_ID[int(bulan[5:7]) - 1] + " " + bulan[:4]
    except (ValueError, IndexError):
        nama_bulan = bulan
    # Dengan jendela tanggal, nama bulan tidak ditulis di query (kenaikan 30 hari bisa
    # melintasi dua bulan; penyaring after:/before: yang membatasi waktunya).
    query = f"harga {komoditas.strip()} Yogyakarta" + ("" if rentang else f" {nama_bulan}")
    hasil = cari_berita(query, bulan=bulan, komoditas=komoditas, rentang=rentang)
    baris = []
    if not hasil:
        # Dicatat sebagai "sudah dicari, tidak ada berita relevan" supaya run berikutnya
        # tidak mengulang pencarian yang sama; papan pantau mengabaikan baris tanpa URL.
        return [{"tanggal": tanggal, "komoditas": komoditas.strip(), "kabupaten_kota": kabupaten, "query": query,
                 "jumlah_berita_ditemukan": 0, "judul_teratas": "", "url_teratas": "", "ringkasan": "",
                 "tervalidasi": False, "media": "", "tanggal_terbit": "", "peringkat": 0, "diambil_pada_utc": diambil,
                 "pemicu": pemicu}]
    for i, h in enumerate(hasil, 1):
        baris.append({
            "tanggal": tanggal,
            "komoditas": komoditas.strip(),
            "kabupaten_kota": kabupaten,
            "query": query,
            "jumlah_berita_ditemukan": len(hasil),
            "judul_teratas": h.get("title", ""),
            "url_teratas": h.get("url", ""),
            "ringkasan": str(h.get("description", ""))[:400],
            "tervalidasi": True,
            "media": h.get("media", ""),
            "tanggal_terbit": h.get("tanggal_terbit", ""),
            "peringkat": i,
            "diambil_pada_utc": diambil,
            "pemicu": pemicu,
        })
    return baris


def pemicu_sp2kp():
    """Varian SP2KP yang naik > AMBANG_NAIK_SP2KP % dalam 30 hari pada tanggal data terakhirnya.
    Rumusnya sama dengan pilar SP2KP di papan pantau: harga terakhir dibanding pengamatan
    terakhir yang <= 30 hari sebelumnya; varian dengan < 60 pengamatan dilewati.
    Kembalikan list dict {tanggal, komoditas, kabupaten_kota, pemicu, rentang}."""
    if not os.path.isfile(SP2KP_PATH):
        catat(f"SP2KP ringkas belum ada ({SP2KP_PATH}); pemicu SP2KP dilewati.")
        return []
    import pandas as pd
    d = pd.read_csv(SP2KP_PATH)
    d["harga"] = pd.to_numeric(d["harga"], errors="coerce")
    d["t"] = pd.to_datetime(d["tanggal"], errors="coerce")
    d = d.dropna(subset=["t", "harga"]).query("harga > 0")
    if d.empty:
        return []
    t_maks = d["t"].max()
    keluar = []
    for varian, g in d.groupby("varian"):
        g = g.sort_values("t")
        if len(g) < MIN_PENGAMATAN_SP2KP:
            continue
        akhir = g.iloc[-1]
        if (t_maks - akhir["t"]).days > 7:          # varian yang sudah tidak dilaporkan
            continue
        batas = akhir["t"] - pd.Timedelta(days=30)
        dulu = g[g["t"] <= batas]
        if dulu.empty:
            continue
        h30 = float(dulu.iloc[-1]["harga"])
        naik = (float(akhir["harga"]) - h30) / h30 * 100
        if naik > AMBANG_NAIK_SP2KP:
            keluar.append({"tanggal": akhir["t"].date().isoformat(), "komoditas": str(varian).strip(),
                           "kabupaten_kota": "DI Yogyakarta", "naik": naik,
                           "pemicu": f"SP2KP naik {naik:.0f}% dalam 30 hari",
                           "rentang": (batas.date().isoformat(), (akhir["t"] + pd.Timedelta(days=1)).date().isoformat())})
    keluar.sort(key=lambda r: -r["naik"])
    return keluar


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uji", help="coba satu query lalu berhenti (untuk memeriksa sumber dari log Actions)")
    ap.add_argument("--maks", type=int, default=MAKS_VALIDASI,
                    help=f"maksimal pencarian per run (default {MAKS_VALIDASI}; naikkan bila dijalankan lokal untuk mengisi arsip)")
    args = ap.parse_args()
    maks = max(1, args.maks)

    if args.uji:
        catat(f"Uji query: {args.uji}")
        for h in cari_berita(args.uji, limit=5, komoditas=args.uji):
            catat(f"  [{h.get('tanggal_terbit') or '?'}] {h.get('media') or '-'} | {h.get('title')}\n     {h.get('url')}")
        return

    if not os.path.isfile(ANOMALI_PATH):
        catat(f"Belum ada {ANOMALI_PATH} -- jalankan merge_and_detect.py dulu.")
        return

    import pandas as pd
    df = pd.read_csv(ANOMALI_PATH)
    anomali = df[df.get("anomali_harga", False).astype(str).str.lower() == "true"]
    anomali = anomali.sort_values("tanggal", ascending=False)

    # Kandidat = pemicu SP2KP (kenaikan yang sedang berlangsung, didahulukan) lalu
    # anomali PIHPS dari yang terbaru. Kunci komoditas|bulan mencegah pencarian ganda.
    kandidat = []
    sp = pemicu_sp2kp()
    if sp:
        catat(f"{len(sp)} varian SP2KP naik > {AMBANG_NAIK_SP2KP:.0f}% dalam 30 hari: "
              + ", ".join(f"{r['komoditas']} ({r['naik']:+.0f}%)" for r in sp))
    kandidat.extend(sp)
    for _, row in anomali.iterrows():
        kandidat.append({"tanggal": str(row["tanggal"]), "komoditas": str(row["komoditas"]),
                         "kabupaten_kota": str(row["kabupaten_kota"]), "pemicu": PEMICU_PIHPS, "rentang": None})
    if not kandidat:
        catat("Tidak ada anomali maupun kenaikan SP2KP untuk divalidasi.")
        return
    total_kandidat = len(kandidat)

    # Arsip lama dibaca dulu lalu DITAMBAH, tidak pernah ditimpa atau menyusut.
    lama = []
    if os.path.isfile(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, newline="", encoding="utf-8") as f:
                lama = list(csv.DictReader(f))
            catat(f"{len(lama)} baris kliping sudah terkumpul dari run sebelumnya.")
        except Exception as e:
            catat(f"Kliping lama tidak terbaca ({type(e).__name__}), mulai dari kosong.")

    kunci = lambda r: f"{str(r.get('komoditas', '')).strip()}|{str(r.get('tanggal', ''))[:7]}"
    sudah = {kunci(r) for r in lama}

    tambahan = []
    percobaan = 0
    for row in kandidat:
        if percobaan >= maks:
            catat(f"Batas {maks} pencarian per run tercapai (dari {total_kandidat} kandidat); "
                  "sisanya dilanjutkan run berikutnya.")
            break
        k = kunci(row)
        if k in sudah:
            continue
        percobaan += 1
        sudah.add(k)
        catat(f"Validasi: {row['tanggal']} - {row['komoditas']} - {row['kabupaten_kota']} [{row['pemicu']}]")
        try:
            baris = validasi_anomali(str(row["tanggal"]), str(row["komoditas"]), str(row["kabupaten_kota"]),
                                     pemicu=row["pemicu"], rentang=row["rentang"])
            tambahan.extend(baris)
            if baris and not baris[0]["url_teratas"]:
                catat("    tidak ada berita relevan; dicatat supaya tidak dicari ulang.")
        except Exception as e:
            catat(f"  GAGAL: {type(e).__name__}: {str(e)[:160]}")
        time.sleep(JEDA_ANTAR_QUERY)

    if not tambahan:
        catat("\nTidak ada kliping baru pada run ini; berkas lama dibiarkan apa adanya.")
        return

    # Baris lama (sebelum kolom `pemicu` ada) seluruhnya berasal dari anomali PIHPS.
    for r in lama:
        if not str(r.get("pemicu") or "").strip():
            r["pemicu"] = PEMICU_PIHPS
    gabung = [{c: r.get(c, "") for c in KOLOM} for r in (tambahan + lama)]
    # Angka bulat bisa tersimpan sebagai "1.0" bila berkas pernah ditulis ulang lewat
    # pandas (unggahan 29 Sep 2026). int("1.0") gagal, dan karena langkah ini
    # continue-on-error, seluruh hasil run ikut hilang tanpa terlihat. Dinormalkan di sini.
    for r in gabung:
        for c in ("peringkat", "jumlah_berita_ditemukan"):
            r[c] = ke_bulat(r.get(c), "")
    # Urutan: tanggal lonjakan terbaru dulu; di dalam tanggal yang sama, peringkat 1 dulu.
    gabung.sort(key=lambda r: ke_bulat(r.get("peringkat"), 1) or 1)
    gabung.sort(key=lambda r: str(r.get("tanggal", "")), reverse=True)
    assert len(gabung) >= len(lama), "arsip kliping tidak boleh menyusut"

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=KOLOM)
        w.writeheader()
        w.writerows(gabung)

    catat(f"\n{len(tambahan)} artikel baru dari {percobaan} pencarian.")
    catat(f"Arsip kliping: {len(lama)} + {len(tambahan)} = {len(gabung)} baris, disimpan ke {OUTPUT_PATH}")


if __name__ == "__main__":
    sys.exit(main())
