"""
Scraper fase tanam padi (dasarian) DIY dari SIMOTANDI Kementan -- endpoint API asli.

Alur (ditemukan lewat inspeksi manual DevTools browser, 30 Agt 2026):

1. GET https://simotandi.pertanian.go.id/data-tabular
   -> ambil cookie sesi (Laravel: simotandi_session, XSRF-TOKEN) & CSRF token
      dari meta tag <meta name="csrf-token" content="...">.
   -> juga dipakai buat mencari opsi dropdown "periode" (dasarian) dengan nilai
      TERBESAR = dasarian TERBARU yang tersedia, supaya kita tidak hardcode
      angka yang lama-lama basi (dasarian baru terbit tiap ~10-12 hari).

2. POST https://simotandi.pertanian.go.id/front/data-tabular/export
   form data: provinsi=34 (kode BPS DI Yogyakarta), kabupaten=<kode BPS 4 digit>,
              kecamatan=all, periode=<id dasarian terbaru>
   header wajib: X-Csrf-Token, X-Requested-With: XMLHttpRequest, Referer
   -> server membuat job export & mengembalikan referensi job (dipoll di
      langkah berikut).

3. GET https://simotandi.pertanian.go.id/export/status/{job_id}  (poll berkala)
   -> {"status": "done", "message": "done:100%",
       "url": "https://minio-simotandi.pertanian.go.id/.../Data_Tabular_....xlsx"}
   Pada sesi inspeksi manual, job_id yang dipoll SAMA dengan nilai periode yang
   dikirim -- kode di bawah pakai itu sebagai asumsi utama, dengan fallback ke
   id dari respons POST kalau ternyata server memberi id job terpisah.

4. GET file .xlsx dari url di atas (object storage publik, tanpa auth
   tambahan) lalu parse dengan pandas.

Kode wilayah kabupaten/kota DIY (standar BPS/Kemendagri):
  3471 Kota Yogyakarta, 3472 Kab. Bantul, 3473 Kab. Gunung Kidul,
  3474 Kab. Kulon Progo, 3475 Kab. Sleman

CATATAN: modul ini lebih kompleks dari pihps_scraper.py (perlu sesi + CSRF +
tunggu job async), jadi kalau situs berubah struktur, jalankan lagi:
    python3 src/sources/simotandi.py --inspect
"""

import argparse
import io
import os
import re
import time
from datetime import datetime, timezone

import pandas as pd
import requests
from playwright.sync_api import sync_playwright

BASE = "https://simotandi.pertanian.go.id"
DATA_TABULAR_PAGE = f"{BASE}/data-tabular"
EXPORT_ENDPOINT = f"{BASE}/front/data-tabular/export"
STATUS_ENDPOINT_TMPL = f"{BASE}/export/status/{{job_id}}"

PROVINCE_ID_DIY = 34
KABUPATEN_DIY = {
    "3471": "Kota Yogyakarta",
    "3472": "Kabupaten Bantul",
    "3473": "Kabupaten Gunung Kidul",
    "3474": "Kabupaten Kulon Progo",
    "3475": "Kabupaten Sleman",
}

HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (compatible; SIGAP-Pangan-DIY/1.0; "
                  "+https://github.com/mrafiraamadhan/sigap-pangan-diy)",
}

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "simotandi_fase_tanam_diy.csv")

MAX_POLL = 24            # maks ~24x cek status
POLL_INTERVAL_SEC = 5    # jeda 5 detik antar cek (~2 menit total maksimal)


def inspect_mode():
    """Mode diagnostik manual -- pakai ini lagi kalau alur di atas suatu saat
    berhenti jalan (situs berubah struktur)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250)
        page = browser.new_page()

        found = []

        def on_request(req):
            if "export" in req.url:
                found.append((req.method, req.url))
                print(">> EXPORT REQUEST:", req.method, req.url)

        page.on("request", on_request)
        page.goto(DATA_TABULAR_PAGE, wait_until="networkidle", timeout=60000)

        print("\n=== MODE INSPEKSI SIMOTANDI ===")
        print("1. Di jendela browser, filter Provinsi = DI Yogyakarta")
        print("2. Pilih kabupaten/kota satu per satu (atau semua kalau bisa)")
        print("3. Klik tombol 'Export Excel'")
        print("4. Catat URL yang tercetak di terminal -- itu pattern query")
        print("   yang perlu ditiru di download_export() di bawah.")
        print("\nTekan Enter setelah selesai...")
        input()

        print(f"\nTotal {len(found)} request terkait export tercatat.")
        browser.close()


def buat_sesi():
    """Buka halaman awal buat ambil cookie sesi + CSRF token Laravel, dan HTML
    halaman itu sendiri (dipakai buat cari periode terbaru)."""
    sesi = requests.Session()
    sesi.headers.update(HEADERS_BASE)
    resp = sesi.get(DATA_TABULAR_PAGE, timeout=30)
    resp.raise_for_status()

    m = re.search(r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', resp.text)
    if not m:
        m = re.search(r'content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']', resp.text)
    if not m:
        raise RuntimeError(
            "Tidak menemukan csrf-token di halaman data-tabular -- "
            "struktur situs SIMOTANDI mungkin sudah berubah."
        )
    csrf_token = m.group(1)

    return sesi, resp.text, csrf_token


def cari_periode_terbaru(html: str):
    """Cari opsi dropdown 'periode' (dasarian) dengan value numerik TERBESAR
    = dasarian paling baru yang tersedia."""
    blok = re.search(
        r'<select[^>]*name=["\']periode["\'][^>]*>(.*?)</select>',
        html, re.IGNORECASE | re.DOTALL,
    )
    sumber = blok.group(1) if blok else html  # fallback: cari di seluruh halaman

    opsi = re.findall(r'<option[^>]*value=["\'](\d+)["\'][^>]*>([^<]*)</option>', sumber)
    if not opsi:
        raise RuntimeError(
            "Tidak menemukan opsi dropdown 'periode' -- struktur situs "
            "SIMOTANDI mungkin sudah berubah."
        )

    nilai, label = max(opsi, key=lambda pasangan: int(pasangan[0]))
    return nilai, label.strip()


def minta_export(sesi, csrf_token: str, kabupaten_id: str, periode_id: str):
    headers = {
        "X-Csrf-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": DATA_TABULAR_PAGE,
    }
    data = {
        "provinsi": PROVINCE_ID_DIY,
        "kabupaten": kabupaten_id,
        "kecamatan": "all",
        "periode": periode_id,
    }
    resp = sesi.post(EXPORT_ENDPOINT, data=data, headers=headers, timeout=30)
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        print(f"  -> respons POST export bukan JSON (mungkin sesi/CSRF invalid): "
              f"{resp.text[:200]}")
        return {}


def tunggu_dan_ambil_url(sesi, job_id: str):
    url_status = STATUS_ENDPOINT_TMPL.format(job_id=job_id)
    for percobaan in range(MAX_POLL):
        resp = sesi.get(url_status, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        status = payload.get("status")
        print(f"  status export (cek {percobaan + 1}/{MAX_POLL}): "
              f"{status} -- {payload.get('message')}")
        if status == "done" and payload.get("url"):
            return payload["url"]
        if status in ("failed", "error"):
            raise RuntimeError(f"Export gagal di server SIMOTANDI: {payload}")
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError("Export tidak selesai dalam waktu wajar -- dilewati run ini.")


def ambil_data() -> pd.DataFrame:
    sesi, html, csrf_token = buat_sesi()
    periode_id, periode_label = cari_periode_terbaru(html)
    print(f"Periode dasarian terbaru terdeteksi: {periode_id} ({periode_label})")

    semua_baris = []
    diambil_pada = datetime.now(timezone.utc).isoformat()

    for kabupaten_id, nama_kabupaten in KABUPATEN_DIY.items():
        print(f"Minta export SIMOTANDI: {nama_kabupaten} ({kabupaten_id})")
        try:
            hasil_minta = minta_export(sesi, csrf_token, kabupaten_id, periode_id)
            print(f"  respons minta export: {hasil_minta}")
            # Berdasarkan observasi manual, job_id yang dipoll SAMA dengan
            # periode_id yang dikirim. Fallback ke id dari respons POST kalau
            # ternyata server memberi id job terpisah di respons JSON-nya.
            job_id = str(hasil_minta.get("id", periode_id)) if isinstance(hasil_minta, dict) else periode_id

            url_file = tunggu_dan_ambil_url(sesi, job_id)
            print(f"  file siap: {url_file}")

            file_resp = sesi.get(url_file, timeout=60)
            file_resp.raise_for_status()

            df = pd.read_excel(io.BytesIO(file_resp.content))
            df["kabupaten_kota"] = nama_kabupaten
            df["kode_kabupaten"] = kabupaten_id
            df["periode_dasarian"] = periode_label
            df["periode_id"] = periode_id
            df["diambil_pada_utc"] = diambil_pada
            semua_baris.append(df)
        except Exception as e:
            print(f"  -> GAGAL untuk {nama_kabupaten}: {e}")

    if not semua_baris:
        return pd.DataFrame()

    return pd.concat(semua_baris, ignore_index=True)


def append_csv(df: pd.DataFrame):
    if df.empty:
        print("Tidak ada data SIMOTANDI untuk disimpan pada run ini.")
        return

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    file_exists = os.path.isfile(OUTPUT_PATH)

    if file_exists:
        existing = pd.read_csv(OUTPUT_PATH)
        gabungan = pd.concat([existing, df], ignore_index=True)
        # dedup berdasarkan semua kolom KECUALI timestamp pengambilan (itu
        # pasti beda tiap run walau datanya sama persis). Bandingkan sebagai
        # string dulu supaya angka yang "berubah tipe" gara-gara baca-ulang
        # CSV (mis. "49" jadi int 49) tetap dianggap sama.
        kolom_pembanding = [c for c in gabungan.columns if c != "diambil_pada_utc"]
        kunci_dup = gabungan[kolom_pembanding].astype(str).duplicated(keep="first")
        gabungan = gabungan[~kunci_dup]
        baru = len(gabungan) - len(existing)
        gabungan.to_csv(OUTPUT_PATH, index=False)
    else:
        df.to_csv(OUTPUT_PATH, index=False)
        baru = len(df)

    print(f"{baru} baris baru ditambahkan ke {OUTPUT_PATH}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true",
                         help="Mode diagnostik manual (buka browser) -- pakai kalau "
                              "alur export berhenti jalan & perlu dicek ulang")
    args = parser.parse_args()

    if args.inspect:
        inspect_mode()
    else:
        hasil = ambil_data()
        append_csv(hasil)
