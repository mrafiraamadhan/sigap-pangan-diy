"""
Ambil data cuaca resmi BMKG untuk kab/kota DIY.

API ini PUBLIK, GRATIS, TANPA API KEY:
    https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4=<kode>

Wajib mencantumkan BMKG sebagai sumber data (syarat penggunaan resmi mereka).

Cara jalankan:
    python bmkg_cuaca.py
Hasil ter-append ke ../../data/cuaca_diy.csv (satu baris per kab/kota per waktu ambil)
"""

import csv
import os
import sys
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "..", "..", "config", "wilayah_diy.csv")
OUTPUT_PATH = os.path.join(HERE, "..", "..", "data", "cuaca_diy.csv")

BASE_URL = "https://api.bmkg.go.id/publik/prakiraan-cuaca"


def load_wilayah():
    rows = []
    with open(CONFIG_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["adm4_bmkg"].startswith("ISI_"):
                print(f"[SKIP] {row['kabupaten_kota']}: kode adm4 belum diisi di "
                      f"config/wilayah_diy.csv -- lihat instruksi di README.md")
                continue
            rows.append(row)
    return rows


def fetch_cuaca(adm4: str) -> dict:
    resp = requests.get(BASE_URL, params={"adm4": adm4}, timeout=20)
    resp.raise_for_status()
    return resp.json()


def extract_rows(kabupaten: str, adm4: str, payload: dict) -> list:
    """BMKG mengembalikan array prakiraan per hari, tiap hari berisi array
    per-jam. Kita ambil ringkasan sederhana: suhu, kelembapan, potensi hujan
    lebat/ekstrem, per slot waktu, supaya gampang dianalisis."""
    out = []
    fetched_at = datetime.now(timezone.utc).isoformat()

    data = payload.get("data", [])
    if not data:
        return out

    forecasts = data[0].get("cuaca", [])
    for day_block in forecasts:
        for slot in day_block:
            out.append({
                "fetched_at_utc": fetched_at,
                "kabupaten_kota": kabupaten,
                "adm4": adm4,
                "waktu_lokal": slot.get("local_datetime"),
                "suhu_c": slot.get("t"),
                "kelembapan_persen": slot.get("hu"),
                "cuaca_desc": slot.get("weather_desc"),
                "curah_hujan_mm": slot.get("tp"),  # total precipitation, kalau tersedia
                "kecepatan_angin_kmh": slot.get("ws"),
            })
    return out


def append_csv(rows: list):
    if not rows:
        return
    file_exists = os.path.isfile(OUTPUT_PATH)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def main():
    wilayah = load_wilayah()
    if not wilayah:
        print("Tidak ada wilayah dengan kode adm4 valid. Isi dulu config/wilayah_diy.csv.")
        sys.exit(1)

    all_rows = []
    for w in wilayah:
        print(f"Mengambil cuaca: {w['kabupaten_kota']} (adm4={w['adm4_bmkg']})")
        try:
            payload = fetch_cuaca(w["adm4_bmkg"])
            rows = extract_rows(w["kabupaten_kota"], w["adm4_bmkg"], payload)
            all_rows.extend(rows)
            print(f"  -> {len(rows)} slot waktu diambil")
        except Exception as e:
            print(f"  -> GAGAL: {e}")

    append_csv(all_rows)
    print(f"\nTotal {len(all_rows)} baris ditambahkan ke {OUTPUT_PATH}")
    print("Sumber data: BMKG (Badan Meteorologi, Klimatologi, dan Geofisika) -- wajib dicantumkan.")


if __name__ == "__main__":
    main()
