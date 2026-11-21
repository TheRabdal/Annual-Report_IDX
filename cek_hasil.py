"""
Rekap hasil download. Jalankan kapan saja:
    python cek_hasil.py

Menampilkan: berapa emiten/tahun lengkap, mana yang bolong, dan file
yang ukurannya terlalu kecil (kemungkinan cuma cover).
"""

import csv
import collections
import json
from pathlib import Path

OUT = Path("PDF_252_FIRM")
TAHUN = [2020, 2021, 2022, 2023, 2024, 2025]

tickers = []
with open("tickers.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        k = (row.get("kode") or "").strip().upper()
        if k:
            tickers.append(k)

nodata = set()
for p in (OUT / "_done").glob("*.ok"):
    try:
        if json.loads(p.read_text(encoding="utf-8")).get("status") == "nodata":
            kode, tahun = p.stem.rsplit("_", 1)
            nodata.add((kode, int(tahun)))
    except Exception:
        pass

per_ticker = collections.defaultdict(list)
files = list(OUT.glob("*.pdf"))
total_bytes = 0
kecil = []
rusak = []
for fp in files:
    size = fp.stat().st_size
    total_bytes += size
    # cek integritas: header %PDF- dan ada penanda %%EOF di 2 KB terakhir
    with open(fp, "rb") as fh:
        head = fh.read(5)
        fh.seek(max(0, size - 2048))
        tail = fh.read()
    if head != b"%PDF-" or b"%%EOF" not in tail:
        rusak.append(fp.name)
    stem = fp.stem
    kode = stem.split("_")[0]
    try:
        tahun = int(stem.split("_")[1])
    except (IndexError, ValueError):
        continue
    per_ticker[kode].append(fp.name)
    if size < 300_000:
        kecil.append((fp.name, size))

print(f"Folder   : {OUT.resolve()}")
print(f"File PDF : {len(files):,}  ({total_bytes / 1073741824:.2f} GB)")
print(f"Emiten   : {len(per_ticker)} dari {len(tickers)} punya minimal 1 PDF")
if rusak:
    print(f"PDF RUSAK: {len(rusak)} file -> {', '.join(rusak[:10])}")
else:
    print("Integritas: semua PDF valid (%PDF- ... %%EOF)")
print()

lengkap, bolong = [], []
for k in tickers:
    ada = {int(n.split("_")[1]) for n in per_ticker.get(k, [])}
    kurang = [y for y in TAHUN if y not in ada and (k, y) not in nodata]
    if len(kurang) == 0:
        lengkap.append(k)
    else:
        bolong.append((k, kurang))

print(f"Lengkap 6 tahun : {len(lengkap)} emiten")
print(f"Masih bolong    : {len(bolong)} emiten")
for k, yrs in bolong[:40]:
    print(f"   {k:<6s} -> {', '.join(map(str, yrs))}")
if len(bolong) > 40:
    print(f"   ... dan {len(bolong) - 40} emiten lain")

print()
print(f"Tanpa data di IDX : {len(nodata)} kombinasi (normal untuk emiten baru/2020-2021)")
if kecil:
    print(f"File < 300 KB     : {len(kecil)} (kemungkinan cover, cek _placeholders.csv)")
    for n, s in kecil[:10]:
        print(f"   {n:<45s} {s / 1024:8.0f} KB")
    if len(kecil) > 10:
        print(f"   ... dan {len(kecil) - 10} file lain")
