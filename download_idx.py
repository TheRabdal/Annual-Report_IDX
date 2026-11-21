"""
Download Laporan Tahunan (Annual Report) dari IDX untuk 252 emiten x 6 tahun.

Versi API -- tanpa browser/Playwright. Jauh lebih cepat & tahan gangguan
karena langsung memakai endpoint yang dipakai website IDX sendiri:
    /primary/ListedCompany/GetFinancialReport?...&reportType=arf

Cara pakai:
    python download_idx.py --test        # tes 1 file: BBCA 2024
    python download_idx.py               # gas semua 252 x 2020-2025
    python download_idx.py --years 2024 2025
    python download_idx.py --only BBCA BMRI
    python download_idx.py --include-esg # ikut ambil file ESG/keberlanjutan

Hasil:
    PDF_252_FIRM/BBCA_2024.pdf              -> file utama (Annual Report)
    PDF_252_FIRM/BBCA_2024__part2.pdf       -> bagian lain dari AR yang dipecah
    PDF_252_FIRM/_available.csv             -> semua file yang tersedia di IDX
    PDF_252_FIRM/_failed.csv                -> yang gagal, tinggal jalankan ulang
    PDF_252_FIRM/_done/*.ok                 -> penanda (untuk resume otomatis)

Aman dijalankan berkali-kali: yang sudah ada akan di-SKIP.

Catatan teknis: IDX dilindungi Akamai dan memblokir fingerprint TLS Python
(403), jadi request dikirim lewat curl.exe (sudah ada di Windows 10/11).
Kalau curl tidak ada, otomatis fallback ke urllib.
"""

import csv
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

# ---------------------------------------------------------------- konfigurasi
BASE = "https://www.idx.co.id"
API = BASE + "/primary/ListedCompany/GetFinancialReport"
LISTING_PAGE = BASE + "/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan"
REFERER = LISTING_PAGE

OUTPUT = Path("PDF_252_FIRM")
DONE_DIR = OUTPUT / "_done"
COOKIE_FILE = OUTPUT / "_cookies.txt"

WORKERS = 2              # jumlah request paralel (IDX mudah kena 429 kalau terlalu agresif)
MIN_INTERVAL = 1.2       # jeda minimum antar request (detik) supaya tidak kena rate limit
TIMEOUT = 300            # detik per request (file AR 2020 bisa 30-40 MB)
RETRIES = 5
INCLUDE_ESG = "--include-esg" in sys.argv

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CURL = shutil.which("curl")
if CURL is None:
    for p in (r"C:\Windows\System32\curl.exe", "/usr/bin/curl", "/usr/local/bin/curl"):
        if Path(p).exists():
            CURL = p
            break

# ------------------------------------------------------------- argumen manual
def arg_values(flag):
    if flag not in sys.argv:
        return []
    i = sys.argv.index(flag)
    out = []
    for a in sys.argv[i + 1:]:
        if a.startswith("--"):
            break
        out.append(a)
    return out


TEST_MODE = "--test" in sys.argv

if arg_values("--workers"):
    WORKERS = max(1, int(arg_values("--workers")[0]))
if arg_values("--interval"):
    MIN_INTERVAL = max(0.0, float(arg_values("--interval")[0]))
# --wait N : tunggu sampai N menit kalau IDX sedang memblokir, lalu mulai otomatis
WAIT_MIN = int(arg_values("--wait")[0]) if arg_values("--wait") else 0
RETRY_FAILED = "--retry-failed" in sys.argv

if TEST_MODE:
    tickers = ["BBCA"]
    TAHUN = [2024]
elif arg_values("--only"):
    tickers = [t.upper() for t in arg_values("--only")]
    TAHUN = [int(y) for y in arg_values("--years")] or [2020, 2021, 2022, 2023, 2024, 2025]
else:
    tickers = []
    with open("tickers.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            kode = (row.get("kode") or "").strip().upper()
            if kode:
                tickers.append(kode)
    TAHUN = [int(y) for y in arg_values("--years")] or [2020, 2021, 2022, 2023, 2024, 2025]

OUTPUT.mkdir(exist_ok=True)
DONE_DIR.mkdir(exist_ok=True)

# ------------------------------------------------------- lapisan HTTP (curl)
_boot_lock = threading.Lock()
_ua_lock = threading.Lock()
_ua_opener = None
_cj = None


def _curl(args):
    return subprocess.run([CURL] + args, capture_output=True, text=True,
                          errors="replace", timeout=TIMEOUT + 60)


_rate_lock = threading.Lock()
_last_request = [0.0]
_pause_until = [0.0]


def global_pause(seconds: float, reason: str = ""):
    """Semua worker ikut berhenti, supaya tidak memperparah rate limit."""
    with _rate_lock:
        end = time.time() + seconds
        if end > _pause_until[0]:
            _pause_until[0] = end
            if reason:
                print(f"  ({reason}: semua worker istirahat {seconds:.0f}s)", flush=True)


def throttle():
    """Jeda antar request + hormati jeda global saat kena rate limit."""
    while True:
        with _rate_lock:
            remaining = _pause_until[0] - time.time()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 5))
    with _rate_lock:
        wait = _last_request[0] + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()


def bootstrap():
    """Ambil cookie sesi IDX (wajib, kalau tidak PDF dibalas 403)."""
    with _boot_lock:
        try:
            _curl(["-s", "-o", os.devnull, "-c", str(COOKIE_FILE),
                   "-H", f"User-Agent: {UA}", "-H", "Accept: text/html,application/xhtml+xml",
                   "-H", "Accept-Language: id-ID,id;q=0.9,en;q=0.8",
                   "-H", "Sec-Fetch-Mode: navigate", "-H", "Sec-Fetch-Dest: document",
                   LISTING_PAGE])
        except Exception as e:
            print(f"  (bootstrap warning: {e})")


def _urllib_opener():
    global _ua_opener, _cj
    with _ua_lock:
        if _ua_opener is None:
            _cj = http.cookiejar.CookieJar()
            _ua_opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(_cj))
        return _ua_opener


def http_get(url, accept, retries=RETRIES, pause_on_limit=True):
    """Return (status_code, body_bytes)."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            throttle()
            if CURL:
                tmp = Path(OUTPUT / f"_tmp_{threading.get_ident()}.bin")
                args = ["-s", "--compressed", "-L",
                        "-b", str(COOKIE_FILE), "-c", str(COOKIE_FILE),
                        "-H", f"User-Agent: {UA}",
                        "-H", f"Accept: {accept}",
                        "-H", "Accept-Language: id-ID,id;q=0.9,en;q=0.8",
                        "-H", f"Referer: {REFERER}",
                        "--max-time", str(TIMEOUT),
                        "-o", str(tmp), "-w", "%{http_code}", url]
                r = _curl(args)
                code = int((r.stdout or "000").strip()[-3:] or 0)
                body = tmp.read_bytes() if tmp.exists() else b""
                if tmp.exists():
                    tmp.unlink()
                if code == 200:
                    return code, body
                last = f"HTTP {code}"
                if code == 429:
                    last = "HTTP 429 (rate limit)"
                    if not pause_on_limit:
                        raise RuntimeError(last)
                    global_pause(min(300, 30 * attempt) + random.uniform(2, 8), "429 rate limit")
                    continue
                if code == 403 and b"Just a moment" in body[:2000]:
                    last = "HTTP 403 (Cloudflare challenge)"
                    if not pause_on_limit:
                        raise RuntimeError(last)
                    global_pause(min(300, 30 * attempt) + random.uniform(2, 8), "403 Cloudflare")
                    continue
                if code in (403, 500, 502, 503, 504):
                    bootstrap()
            else:
                op = _urllib_opener()
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA, "Accept": accept, "Referer": REFERER})
                with op.open(req, timeout=TIMEOUT) as resp:
                    return 200, resp.read()
        except Exception as e:
            last = e
        time.sleep(1.2 * attempt + random.random())
    raise RuntimeError(f"GET gagal setelah {retries}x: {last}")


def api_reports(kode: str, tahun: int):
    """Kembalikan daftar attachment (PDF) untuk emiten+tahun."""
    url = (f"{API}?indexFrom=0&pageSize=100&year={tahun}&reportType=arf"
           f"&stemming=1&kodeEmiten={kode}&emitenType=s")
    code, body = http_get(url, "application/json, text/plain, */*")
    data = json.loads(body.decode("utf-8", "replace"))
    out, seen = [], set()
    for res in data.get("Results") or []:
        for att in res.get("Attachments") or []:
            fid = att.get("File_ID")
            if not att.get("File_Path") or fid in seen:
                continue
            seen.add(fid)
            out.append(att)
    return out


def build_url(path: str) -> str:
    """Rakit URL file IDX.

    Sebagian file lama (2020) punya nama dengan spasi dan path berslash ganda,
    mis. ".../04_Annual Report//2020/FASW/FASW_Annual Report_2020.pdf".
    curl menolak URL semacam itu (HTTP 000), jadi dirapikan + di-encode.
    """
    clean = "/".join(seg for seg in path.split("/") if seg)
    url = f"{BASE}/{clean}"
    return quote(url, safe=":/?&=#%[]@!$'()*+,;~-._")


def download(att, dest: Path):
    url = build_url(att["File_Path"])
    code, body = http_get(url, "application/pdf,application/octet-stream,*/*")
    if not body.startswith(b"%PDF-"):
        raise ValueError(f"bukan PDF ({len(body)} bytes)")
    # Catatan: beberapa emiten menaruh file utama berupa cover kecil (~5 KB),
    # isi lengkapnya ada di lampiran "-attN". Jadi batas bawah dibuat longgar.
    if len(body) < 1_000:
        raise ValueError(f"file terlalu kecil ({len(body)} bytes)")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(body)
    os.replace(tmp, dest)
    return len(body)


# --------------------------------------------------------------------- util
def safe(name: str, maxlen: int = 90) -> str:
    bad = '<>:"/\\|?*'
    s = "".join("_" if c in bad else c for c in name).strip(" .")
    return s[:maxlen]


def is_esg(att) -> bool:
    n = (att.get("File_Name") or "").upper()
    return n.startswith("ESG") or "ESG" in n or "KEBERLANJUTAN" in n or "SUSTAINAB" in n


def is_part(att) -> bool:
    return "-att" in (att.get("File_Name") or "").lower()


def pick_primary(atts):
    """File utama = Annual Report tanpa suffix -att, selain ESG."""
    cands = [a for a in atts if not is_esg(a) and not is_part(a)]
    if cands:
        return cands[0]
    cands = [a for a in atts if not is_esg(a)]
    if cands:
        return cands[0]
    return atts[0]


def part_label(att) -> str:
    stem = Path(att.get("File_Name") or "file.pdf").stem
    if is_esg(att):
        return "ESG"
    low = stem.lower()
    if "-att" in low:
        tail = low.split("-att")[-1]
        return f"part{tail}" if tail.isdigit() else f"part_{safe(stem, 40)}"
    return safe(stem, 80)


# ------------------------------------------------------------------- worker
print_lock = threading.Lock()
stats = {"ok": 0, "skip": 0, "nodata": 0, "partial": 0, "fail": 0, "files": 0, "bytes": 0}
failed_rows = []
avail_rows = []
tiny_rows = []


def log(msg):
    with print_lock:
        print(msg, flush=True)


def process(kode: str, tahun: int):
    marker = DONE_DIR / f"{kode}_{tahun}.ok"
    primary_path = OUTPUT / f"{kode}_{tahun}.pdf"
    if marker.exists() and primary_path.exists() and primary_path.stat().st_size > 1_000:
        try:
            st = json.loads(marker.read_text(encoding="utf-8")).get("status")
        except Exception:
            st = None
        if st in ("ok", "nodata"):
            return ("skip", kode, tahun, 0, 0)

    try:
        atts = api_reports(kode, tahun)
    except Exception as e:
        with print_lock:
            failed_rows.append((kode, tahun, "API", str(e)[:200]))
        return ("fail", kode, tahun, 0, str(e)[:110])

    if not atts:
        marker.write_text(json.dumps({"status": "nodata"}), encoding="utf-8")
        return ("nodata", kode, tahun, 0, 0)

    primary = pick_primary(atts)
    todo = [primary] + [a for a in atts if a is not primary]
    if not INCLUDE_ESG:
        todo = [a for a in todo if a is primary or not is_esg(a)]

    with print_lock:
        for a in atts:
            avail_rows.append({
                "kode": kode, "tahun": tahun,
                "file_name": a.get("File_Name", ""),
                "status": "diambil" if a in todo else ("skip_esg" if is_esg(a) else "skip"),
                "url": build_url(a["File_Path"]),
            })

    saved, nbytes, errs = [], 0, []
    for att in todo:
        dest = primary_path if att is primary else \
            OUTPUT / f"{kode}_{tahun}__{part_label(att)}.pdf"
        if dest.exists() and dest.stat().st_size > 1_000:
            saved.append(dest.name)
            nbytes += dest.stat().st_size
            continue
        try:
            nbytes += download(att, dest)
            saved.append(dest.name)
        except Exception as e:
            errs.append(f"{att.get('File_Name')}: {e}")
            with print_lock:
                failed_rows.append((kode, tahun, att.get("File_Name", ""), str(e)[:200]))

    ok_primary = primary_path.exists() and primary_path.stat().st_size > 1_000
    if ok_primary and primary_path.stat().st_size < 300_000:
        with print_lock:
            tiny_rows.append((kode, tahun, primary_path.name,
                              primary_path.stat().st_size,
                              sum(1 for f in saved if "__" in f)))
    status = "ok" if ok_primary and not errs else ("partial" if saved else "fail")
    marker.write_text(json.dumps({
        "status": status, "files": saved, "errors": errs,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    detail = nbytes if status != "fail" else (errs[0] if errs else "gagal tanpa detail")
    return (status, kode, tahun, len(saved), detail)


# --------------------------------------------------------------------- main
def preflight():
    """Cek sekali apakah IDX masih memblokir. Cepat gagal, tanpa nunggu lama."""
    url = (f"{API}?indexFrom=0&pageSize=1&year={TAHUN[0]}&reportType=arf"
           f"&stemming=1&kodeEmiten={tickers[0]}&emitenType=s")
    try:
        code, body = http_get(url, "application/json, text/plain, */*",
                              retries=1, pause_on_limit=False)
        return body.lstrip().startswith(b"{")
    except Exception as e:
        print(f"  preflight: {e}")
        return False


def main():
    if RETRY_FAILED:
        pairs = set()
        fpath = OUTPUT / "_failed.csv"
        if not fpath.exists():
            print("Tidak ada _failed.csv — tidak ada yang perlu diulang.")
            return
        with open(fpath, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    pairs.add((row["kode"].strip(), int(row["tahun"])))
                except (KeyError, ValueError):
                    pass
        jobs = sorted(pairs)
    else:
        jobs = [(k, y) for k in tickers for y in TAHUN]
    total = len(jobs)
    print("IDX Annual Report downloader")
    print(f"  emiten : {len(tickers)}  |  tahun: {TAHUN}")
    print(f"  total  : {total} kombinasi emiten x tahun")
    print(f"  output : {OUTPUT.resolve()}")
    print(f"  mesin  : {'curl' if CURL else 'urllib'}  |  worker: {WORKERS}"
          f"  |  jeda: {MIN_INTERVAL:.1f}s  |  ESG: {'ikut' if INCLUDE_ESG else 'dilewati'}")
    if WAIT_MIN:
        print(f"  tunggu : maks {WAIT_MIN} menit kalau IDX memblokir")
    print("-" * 78, flush=True)

    bootstrap()
    if not preflight():
        if WAIT_MIN <= 0:
            print("  IDX masih memblokir akses (rate limit / Cloudflare).")
            print("  Tunggu 10-30 menit lalu jalankan lagi: python download_idx.py")
            print("  (file yang sudah terunduh tetap aman)")
            sys.exit(2)
        deadline = time.time() + WAIT_MIN * 60
        delay = 180
        opened = False
        print(f"  IDX sedang memblokir. Mode tunggu aktif (maks {WAIT_MIN} menit).", flush=True)
        while time.time() < deadline:
            left = int((deadline - time.time()) / 60)
            print(f"  cek lagi dalam {delay // 60} menit (sisa jatah tunggu {left} menit)", flush=True)
            time.sleep(delay)
            bootstrap()
            if preflight():
                opened = True
                break
            delay = min(600, int(delay * 1.6))
        if not opened:
            print("  Jatah tunggu habis, IDX masih memblokir. Coba lagi nanti.")
            sys.exit(2)
        print("  IDX terbuka kembali — mulai unduh.", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(process, k, y): (k, y) for k, y in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            k, y = futs[fut]
            try:
                status, kode, tahun, nfiles, info = fut.result()
            except Exception as e:
                status, kode, tahun, nfiles, info = "fail", k, y, 0, str(e)[:110]
                with print_lock:
                    failed_rows.append((k, y, "worker", str(e)[:200]))

            stats[status] = stats.get(status, 0) + 1
            if status in ("ok", "partial"):
                stats["files"] += nfiles
                stats["bytes"] += info if isinstance(info, int) else 0
            mb = (info / 1048576) if isinstance(info, int) else 0
            elapsed = time.time() - t0
            eta = (elapsed / i) * (total - i) / 60 if i else 0
            tag = {"ok": "OK", "nodata": "NODATA", "partial": "PARTIAL",
                   "fail": "GAGAL", "skip": "SKIP"}[status]
            detail = f"{nfiles} file {mb:5.1f} MB" if status != "nodata" else "tidak ada laporan"
            if status == "fail":
                detail = str(info)[:95]
            log(f"[{i:4d}/{total}] {tag:7s} {kode:<6s} {tahun}  {detail:<30s} ETA {eta:5.1f}m")

    with open(OUTPUT / "_failed.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kode", "tahun", "file", "error"])
        w.writerows(failed_rows)
    with open(OUTPUT / "_available.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["kode", "tahun", "file_name", "status", "url"])
        w.writeheader()
        w.writerows(avail_rows)
    with open(OUTPUT / "_placeholders.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kode", "tahun", "file_utama", "ukuran_byte", "jumlah_lampiran"])
        w.writerows(tiny_rows)

    dur = (time.time() - t0) / 60
    print("-" * 78)
    print(f"SELESAI dalam {dur:.1f} menit")
    print(f"  baru sukses: {stats['ok']}   sudah ada (skip): {stats['skip']}   "
          f"partial: {stats['partial']}   tanpa data: {stats['nodata']}   gagal: {stats['fail']}")
    print(f"  file       : {stats['files']} PDF, {stats['bytes'] / 1073741824:.2f} GB")
    if failed_rows:
        print(f"  gagal      : lihat {OUTPUT / '_failed.csv'} lalu jalankan ulang script ini")
    # bersihkan file sementara
    for p in OUTPUT.glob("_tmp_*.bin"):
        try:
            p.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
