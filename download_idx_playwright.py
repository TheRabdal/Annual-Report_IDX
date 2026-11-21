import csv, time
from pathlib import Path
from playwright.sync_api import sync_playwright

# GANTI DUA BARIS INI BUAT TEST DULU
tickers = ["BBCA"] 
TAHUN = [2024]

OUTPUT = Path("PDF_252_FIRM")
OUTPUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page(accept_downloads=True)
    
    for kode in tickers:
        for tahun in TAHUN:
            file_path = OUTPUT / f"{kode}_{tahun}.pdf"
            if file_path.exists():
                print(f"SKIP {kode} {tahun}")
                continue
            
            print(f"Cari {kode} {tahun}...")
            page.goto("https://www.idx.co.id/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            
            # Isi pencarian
            page.get_by_placeholder("Kode Emiten").fill(kode)
            page.get_by_placeholder("Tahun").fill(str(tahun))
            page.get_by_placeholder("Jenis Laporan").click()
            page.get_by_text("Laporan Tahunan").click()
            
            try:
                with page.expect_download(timeout=60000) as dl:
                    page.get_by_role("button", name="Cari").click()
                download = dl.value
                download.save_as(str(file_path))
                print(f"OK {kode} {tahun}")
            except Exception as e:
                print(f"GAGAL {kode} {tahun}: {e}")
            
            time.sleep(3)
    
    browser.close()
print("SELESAI")
