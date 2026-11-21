Jalankan semua unduhan (default):

```powershell
python download_idx.py
```

*Catatan: Script aman dihentikan dan dijalankan ulang kapan saja. File yang sudah berhasil diunduh akan otomatis di-SKIP.*

Tes cepat (1 file saja):

```powershell
python download_idx.py --test
```

Unduh untuk tahun tertentu saja (contoh 2024 & 2025):

```powershell
python download_idx.py --years 2024 2025
```

Unduh emiten tertentu saja:

```powershell
python download_idx.py --only BBCA BMRI TLKM
```

Jika terkena batasan/rate-limit dari IDX (error 429), kurangi kecepatan:

```powershell
python download_idx.py --workers 1 --interval 3
```

### Mengecek Hasil Unduhan

Setelah atau saat proses berjalan, Anda bisa mengecek rekap dan integritas PDF dengan perintah:

```powershell
python cek_hasil.py
```

Semua file PDF yang terunduh akan disimpan di folder `PDF_252_FIRM/`.

---

# Download Laporan Tahunan IDX — 252 Emiten × 2020–2025

Folder ini berisi pengunduh Laporan Tahunan (Annual Report) dari IDX
(<https://www.idx.co.id/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan>)
untuk 252 emiten terbesar, tahun 2020 sampai 2025.

## Isi folder

| File | Fungsi |
|---|---|
| `tickers.csv` | 252 kode emiten (`kode` di baris 1, lalu 1 kode per baris) — sesuai list yang kamu kirim |
| `download_idx.py` | Pengunduh utama (versi API, tanpa browser) |
| `download_idx_playwright.py` | Versi asli berbasis Playwright (arsip, tidak dipakai) |
| `cek_hasil.py` | Rekap + cek integritas PDF |
| `run.bat` | Double-click untuk melanjutkan unduhan |
| `download_log.txt`, `download_log2.txt` | Log proses sebelumnya |
| `PDF_252_FIRM/` | Hasil download |

## Cara pakai

```powershell
# tes cepat 1 file
python download_idx.py --test

# jalankan semua (aman diulang, yang sudah ada di-SKIP)
python download_idx.py

# tahun tertentu saja
python download_idx.py --years 2024 2025

# emiten tertentu
python download_idx.py --only BBCA BMRI TLKM

# atur kecepatan kalau IDX membalas 429 (rate limit)
python download_idx.py --workers 1 --interval 3

# tunggu otomatis sampai IDX membuka akses (maks 420 menit), lalu mulai sendiri
python download_idx.py --wait 420 --workers 2 --interval 2.5

# ikut ambil file ESG / Laporan Keberlanjutan
python download_idx.py --include-esg

# rekap hasil
python cek_hasil.py
```

Kalau koneksi terputus atau laptop dimatikan: tinggal jalankan
`python download_idx.py` lagi. File yang sudah selesai tidak diunduh ulang.

## Format nama file

```
PDF_252_FIRM/BBCA_2024.pdf             <- file utama Annual Report
PDF_252_FIRM/BBCA_2024__part2.pdf      <- bagian lain (IDX memecah AR besar)
PDF_252_FIRM/BBCA_2024__ESG.pdf        <- hanya jika pakai --include-esg
```

File bantu di dalam `PDF_252_FIRM/`:

- `_available.csv` — semua file yang tersedia di IDX beserta URL asli
- `_failed.csv` — kegagalan teknis (bisa diulang dengan menjalankan ulang)
- `_placeholders.csv` — file utama kecil (<300 KB, biasanya hanya cover)
- `_done/*.ok` — penanda per emiten/tahun untuk resume

## Kenapa bukan Playwright

Script versi browser mengandalkan `page.expect_download()` saat klik tombol
**Cari**. Di IDX, tombol itu hanya menampilkan tabel hasil; file baru terunduh
kalau ikon download per baris diklik. Akibatnya `expect_download` akan timeout
60 detik di setiap emiten dan tidak ada file yang tersimpan.

Versi `download_idx.py` memanggil endpoint yang dipakai website IDX sendiri:

```
GET /primary/ListedCompany/GetFinancialReport
      ?indexFrom=0&pageSize=100&year=2024&reportType=arf
      &stemming=1&kodeEmiten=BBCA&emitenType=s
```

Hasilnya JSON berisi daftar lampiran PDF (`File_Path`), lalu diunduh langsung.

Dua catatan teknis:

1. **Wajib cookie.** Tanpa cookie sesi, file PDF dibalas `403 Forbidden`.
   Script mengambil cookie dari halaman IDX dulu (`_cookies.txt`).
2. **Python diblokir, curl lolos.** Proteksi Akamai IDX menolak TLS
   fingerprint `urllib`/`requests` (403), tapi menerima `curl`. Karena itu
   request dikirim lewat `curl.exe` bawaan Windows 10/11. Kalau curl tidak
   ditemukan, script fallback ke `urllib`.

## Rate limit (429) dan Cloudflare

IDX memakai Cloudflare. Kalau request terlalu rapat:

- **429** = rate limit sementara → script menjeda **semua worker** 30–300 detik,
  lalu melanjutkan sendiri.
- **403 + "Just a moment…"** = challenge → jeda lebih panjang. Kalau masih
  diblok saat start, script berhenti dengan pesan jelas (exit code 2) daripada
  memboroskan kuota.

Default sekarang: **2 worker, jeda 1,2 detik**. Kalau masih kena 429, turunkan:
`python download_idx.py --workers 1 --interval 3`

Estimasi total: 1.512 kombinasi × ±3 request ≈ 4.500 request → **2–4 jam**
tergantung kecepatan koneksi dan besar file.

## Soal tahun yang kosong (`NODATA`)

Banyak emiten tidak punya laporan 2020–2021 di EREP IDX. Ini sudah
diverifikasi langsung ke API (contoh: AKRA dan TMAS mengembalikan
`ResultCount: 0` untuk 2020), jadi bukan kegagalan script. Emiten yang baru
IPO juga otomatis kosong untuk tahun sebelum pencatatan.

`_failed.csv` hanya berisi kegagalan teknis; `NODATA` tidak masuk situ.
