# Sistem Deteksi Dini Anomali Harga & Ketahanan Pangan DIY (real-time pipeline)

Dibangun untuk YES 2026 (Yogyakarta Economic Symposium) — sub-tema Ketahanan Pangan.

## Ide besar

Gabungkan tiga sumber data resmi Indonesia yang masing-masing punya "denyut" update
berbeda, lalu deteksi anomali gabungan:

| Sumber | Data | Frekuensi update nyata | Cara akses |
|---|---|---|---|
| **PIHPS Bank Indonesia** (bi.go.id/hargapangan) | Harga eceran/produsen pangan strategis per kab/kota | **Harian** | Browser automation (Playwright) — situs AJAX, belum ada API publik resmi |
| **BMKG** (api.bmkg.go.id) | Prakiraan cuaca 3 hari + peringatan dini cuaca ekstrem per kelurahan | **Beberapa kali/hari** | **API JSON resmi, gratis, tanpa API key** |
| **SIMOTANDI** (simotandi.pertanian.go.id) | Fase pertanaman padi (luas tanam/panen) dari citra satelit Sentinel-1, per kab/kota | **Dasarian (~10-12 hari)** | Export Excel di `/front/data-tabular/export` (perlu automation, filter provinsi/kab) |

Ini bukan "real-time" dalam artian streaming detik-ke-detik (data sumbernya sendiri
memang tidak update sesering itu), tapi **near-real-time & event-driven**: sistem
jalan otomatis sesuai denyut update masing-masing sumber, dan begitu ada nilai baru
yang anomali, langsung tercatat/bisa dialert.

## Kenapa arsitekturnya begini

Claude (aku) jalan di sandbox cloud yang **tidak** punya akses internet umum dari
terminal/browser — cuma boleh lewat tool pencarian/fetch tertentu. Jadi scraper
Playwright tidak bisa aku eksekusi terus-menerus dari sesi chat ini. Solusinya:
jalankan pipeline ini di **GitHub Actions** (gratis untuk repo publik, py punya akses
internet penuh, bisa jalankan Playwright headless, dan bisa dijadwalkan via cron).
Ini juga lebih "expert" untuk dipresentasikan di paper: reproducible, auditable
(setiap run tercatat di riwayat Actions), dan tidak bergantung laptop kamu nyala terus.

Alternatif kalau tidak mau pakai GitHub: jalankan `src/pipeline/run_all.py` via
Task Scheduler (Windows) / cron (Mac/Linux) di komputer kamu sendiri.

## Pilar data yang sudah diimplementasikan

1. **Harga pangan** — PIHPS BI + DPKP DIY (`dpkp_diy_scraper.py`)
2. **Cuaca & peringatan dini** — BMKG API resmi (`bmkg_cuaca.py`)
3. **Fase tanam padi** — SIMOTANDI Kementan (`simotandi.py`, dasarian)
4. **Cadangan pangan pemerintah** — `cadangan_pangan_scraper.py` (temuan kunci:
   total cadangan DIY per Agustus 2026 hanya 283,7 ton, nihil di 3 dari 5
   kabupaten/kota — lihat paper untuk analisis lengkap)
5. **Validasi tekstual berita** — `news_validation.py` (Firecrawl API)
6. **Proksi kepanikan publik** — `google_trends_panik.py`: lonjakan volume
   pencarian Google Trends untuk frasa seperti "harga beras naik", "kelangkaan
   pangan" di wilayah DIY (geo=`ID-YO`), mengikuti gagasan "social attention"
   dari literatur (Xu dkk. 2018, ACM TMIS) bahwa perhatian publik/sosial
   membantu memprediksi price shock lebih dini dari data harga semata.
   **Catatan risiko**: `pytrends` adalah library tidak resmi (reverse-engineered),
   rawan rate-limit/CAPTCHA dan bisa berhenti berfungsi jika Google mengubah
   endpoint-nya — jangan jadi satu-satunya sinyal, selalu sandingkan dengan
   pilar data resmi lainnya.

## Sumber data real-time TAMBAHAN yang masih relevan (opsional, tinggal tambah modul)

- **BMKG gempa terkini** — `https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json`
  (JSON publik, update tiap ada gempa) — relevan kalau mau menyentuh dimensi
  "resiliensi" bencana, bukan cuma pangan.
- **NASA POWER API** — `https://power.larc.nasa.gov/api/` — data cuaca/curah hujan
  satelit harian, API resmi gratis tanpa key, bagus sebagai pembanding/pelengkap
  BMKG kalau BMKG API sedang down.
- **Panel Harga Badan Pangan Nasional** (panelharga.badanpangan.go.id) — alternatif/
  pembanding PIHPS; saat dicek masih maintenance, cek ulang secara berkala.

## Struktur repo

```
realtime-pipeline/
  .github/workflows/pipeline.yml   -> jadwal otomatis (cron)
  config/wilayah_diy.csv           -> kode wilayah adm4 BMKG utk 5 kab/kota DIY (ISI DULU, lihat di bawah)
  src/sources/bmkg_cuaca.py        -> ambil prakiraan cuaca + peringatan dini
  src/sources/pihps_scraper.py     -> scrape harga pangan harian (Playwright)
  src/sources/simotandi.py         -> ambil data fase tanam padi (dasarian)
  src/pipeline/merge_and_detect.py -> gabung semua sumber + deteksi anomali
  data/                            -> hasil tersimpan di sini (CSV, di-commit tiap run)
```

## Setup (~15 menit tersisa -- kode wilayah BMKG sudah diisi)

~~Isi kode wilayah BMKG~~ **SUDAH DIISI** di `config/wilayah_diy.csv` (satu
kelurahan representatif per kabupaten/kota, diverifikasi silang lewat
bmkg.go.id dan kodepos.co.id/desapos.id). Sebelum production, tetap tes
sekali ke API asli dari lingkungan dengan internet normal untuk memastikan
kodenya masih valid.

1. **Temukan endpoint asli PIHPS & SIMOTANDI** (sekali saja, karena situsnya AJAX):
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   python src/sources/pihps_scraper.py --inspect
   python src/sources/simotandi.py --inspect
   ```
   Ikuti instruksi di terminal (pilih DIY & tanggal manual di browser yang
   terbuka), catat request XHR/fetch yang muncul, lalu isi konstanta
   `DATA_ENDPOINT` / fungsi `download_export()` di file terkait kalau ketemu
   endpoint JSON langsung (jauh lebih stabil daripada scraping tabel HTML).

2. **Daftar Firecrawl API key** (gratis, di firecrawl.dev) untuk
   `news_validation.py`, lalu simpan sebagai GitHub Secret bernama
   `FIRECRAWL_API_KEY` (Settings → Secrets and variables → Actions).

3. **Push ke GitHub**, aktifkan Actions, lalu jalankan sekali manual lewat tab
   Actions ("Run workflow") untuk tes. Setelah itu otomatis jalan sesuai jadwal
   (BMKG tiap 6 jam, harga pangan tiap hari, SIMOTANDI tiap Senin, Google Trends
   tiap hari).

4. Hasil gabungan + flag anomali akan ter-commit ke `data/gabungan_anomali.csv`
   setiap run — tinggal load file ini untuk bikin grafik/dashboard di paper.

## Model deteksi anomali

`src/pipeline/merge_and_detect.py` pakai pendekatan berlapis, semuanya masih
level pemula-friendly tapi sudah bisa disebut "sistem":

1. **Z-score rolling per komoditas per kab/kota** pada harga PIHPS (lonjakan harga
   tidak wajar dibanding pola beberapa minggu terakhir)
2. **Korelasi silang dengan curah hujan/cuaca ekstrem BMKG** (apakah lonjakan harga
   berbarengan dengan cuaca ekstrem yang bisa mengganggu distribusi/produksi)
3. **Konteks fase tanam SIMOTANDI** (apakah anomali terjadi saat masa tanam/panen
   yang seharusnya menambah/mengurangi pasokan — bikin insight-nya lebih kaya)

Ini bisa dinaikkan levelnya ke Isolation Forest / Prophet forecasting kalau kamu
mau, tapi baseline z-score + korelasi ini sudah cukup kuat untuk paper dan jauh
lebih gampang dijelaskan ke juri.
