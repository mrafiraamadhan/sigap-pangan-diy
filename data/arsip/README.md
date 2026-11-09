# Arsip SIGAP: data yang tidak bisa diunduh ulang

Folder ini menyimpan data yang **hilang kalau tidak dicatat saat itu juga**. Ada dua penyebab: sumbernya hanya menampilkan angka hari ini, atau keluaran pipeline ditimpa setiap kali dijalankan. Semua berkas di sini **hanya bertambah, tidak pernah ditimpa**.

Folder ini tidak disalin ke `docs/data/`, jadi papan pantau tetap ringan.

| Berkas | Isi | Ditulis oleh | Mulai |
|---|---|---|---|
| `prakiraan_terbit.csv` | Prakiraan harga **pertama** yang terbit untuk tiap sumber, komoditas, horizon, dan tanggal data. Ini bahan uji jujur, karena prakiraannya dibuat sebelum harga aktual diketahui. | `arsip_harian.py` (setiap run) | 31 Agt 2026 (isi mundur dari git) |
| `log_cek_sumber.csv` | Satu baris per pilar per run: kapan dicek, jumlah baris, berubah atau tidak. Dipakai untuk menghitung seberapa sering tiap sumber benar-benar diperbarui. | `arsip_harian.py` | 29 Sep 2026 (isi mundur dari git) |
| `trends_terbit.csv` | Skor Google Trends per tanggal, ditulis setiap kali angkanya berubah. Google menskala ulang angka di setiap pengambilan. | `arsip_harian.py` | 30 Agt 2026 (isi mundur dari git) |
| `berita_terbit.csv` | Setiap hasil berita unik saat pertama kali terlihat. Hasil pencarian berita bisa berubah atau hilang. | `arsip_harian.py` | 30 Agt 2026 (isi mundur dari git) |
| `log_cadangan.csv` | Setiap cek portal cadangan pangan DIY, baik berhasil maupun gagal: status, kode HTTP, tanggal data di portal, dan total kg. | `cadangan_pangan_scraper.py` | Saat kode ini dipasang |

## Catatan pembacaan

- **Cadangan pangan.** Riwayat stoknya ada di `data/cadangan_pangan_diy.csv`, direkam sejak 30 Agt 2026, dan hanya bertambah saat angkanya berubah. `log_cadangan.csv` melengkapinya dengan membedakan dua keadaan:
  - `status=ok` dengan `baris_baru=0`: portal bisa dibuka, tetapi angkanya tetap.
  - `status=gagal_*`: portal tidak bisa diambil.
- **Prakiraan.** Untuk satu tanggal data, hanya prakiraan pertama yang disimpan. Prakiraan ulang dengan kode yang lebih baru tidak menggantikannya, supaya hasil uji tidak bisa "diperbaiki" setelah aktualnya diketahui. Kolom `pertama_terlihat_utc` adalah waktu terbitnya. Kolom `asal_arsip` berisi hash commit untuk baris hasil isi mundur, atau `pipeline` untuk baris dari run langsung.
- **Google Trends.** Versi lama berkas tidak menyimpan nama kata kunci, jadi serinya diberi label menurut urutan pengambilan (`urutan-1`, `urutan-2`). Sejak kode diperbaiki, label seri berisi kata kuncinya.
- **Mengisi ulang dari git.** Jalankan `python src/pipeline/isi_arsip_dari_git.py` dari klon penuh. Aman dijalankan ulang.
