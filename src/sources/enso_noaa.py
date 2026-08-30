"""
Ambil indeks ENSO/El Nino resmi dari NOAA Climate Prediction Center: Oceanic
Nino Index (ONI) -- rata-rata bergerak 3 bulan anomali suhu permukaan laut di
wilayah Nino 3.4 (indikator standar dunia untuk status El Nino/La Nina).

Sumber: https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
File teks polos, di-update NOAA sekitar tanggal 9-14 tiap bulan. TIDAK perlu
Playwright/browser -- cukup requests biasa, jauh lebih stabil daripada PIHPS.

Kenapa ini relevan untuk ketahanan pangan DIY: episode El Nino kuat (1997/98,
2015/16) historis berasosiasi dengan kekeringan panjang yang mengganggu
produksi pangan di Jawa. Pilar ini membuat pemantauan status El Nino jadi
otomatis/berkelanjutan, bukan cuma snapshot manual satu kali seperti
sebelumnya (lihat data/indikator_eksternal_snapshot.csv).

Cara pakai:
    pip install requests
    python enso_noaa.py
"""

import os
import re
from datetime import datetime, timezone

import requests

URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "enso_oni.csv")

HEADERS = {"User-Agent": "Mozilla/5.0 (research bot - YES2026 food security paper)"}

# Baris data formatnya: SEAS YR TOTAL ANOM, contoh: "MJJ 2026  29.02   1.39"
ROW_RE = re.compile(r"^([A-Z]{3})\s+(\d{4})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$")


def klasifikasi_enso(anom: float) -> str:
    """Klasifikasi status ENSO berdasarkan ambang batas standar NOAA (ONI)."""
    if anom >= 2.0:
        return "El Nino sangat kuat"
    if anom >= 1.5:
        return "El Nino kuat"
    if anom >= 1.0:
        return "El Nino moderat"
    if anom >= 0.5:
        return "El Nino lemah"
    if anom <= -2.0:
        return "La Nina sangat kuat"
    if anom <= -1.5:
        return "La Nina kuat"
    if anom <= -1.0:
        return "La Nina moderat"
    if anom <= -0.5:
        return "La Nina lemah"
    return "Netral"


def ambil_data() -> list:
    try:
        resp = requests.get(URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"GAGAL ambil data NOAA ONI: {e}")
        return []

    rows = []
    for line in resp.text.splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        musim, tahun, total, anom = m.groups()
        rows.append(
            {
                "musim": musim,
                "tahun": int(tahun),
                "suhu_permukaan_laut_c": float(total),
                "anomali_nino34_c": float(anom),
                "status_enso": klasifikasi_enso(float(anom)),
                "diambil_pada_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
    return rows


def append_csv(rows: list):
    if not rows:
        return
    import csv

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    # Hindari duplikat: cuma tambahkan baris (musim, tahun) yang belum ada.
    existing_keys = set()
    if os.path.isfile(OUTPUT_PATH):
        with open(OUTPUT_PATH, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing_keys.add((r["musim"], r["tahun"]))

    new_rows = [r for r in rows if (r["musim"], str(r["tahun"])) not in existing_keys]

    file_exists = os.path.isfile(OUTPUT_PATH)
    with open(OUTPUT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"{len(new_rows)} baris baru ditambahkan ke {OUTPUT_PATH} "
          f"(dari {len(rows)} baris hasil parse, sisanya sudah ada).")


def main():
    print("Mengambil indeks ENSO/El Nino (NOAA ONI)...")
    rows = ambil_data()
    if not rows:
        print("Tidak ada data ter-parse.")
        return
    terbaru = rows[-1]
    print(f"Data terbaru: {terbaru['musim']} {terbaru['tahun']} -- "
          f"anomali Nino 3.4 = {terbaru['anomali_nino34_c']:+.2f}C "
          f"({terbaru['status_enso']})")
    append_csv(rows)


if __name__ == "__main__":
    main()
