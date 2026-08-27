from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
import csv
import io
import httpx
import os
import json
import re
import tempfile
from pathlib import Path
from collections import defaultdict

app = FastAPI(title="PLUVIO - Sensus BMN")

# ── Config ──────────────────────────────────────────────────────────
CENSUS_SHEET_URL = os.environ.get(
    "CENSUS_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1CtWwWaMNW8lhkAHsCUhSNHBZxGAWlTrqDABzRM4_wkk/export?format=csv&gid=0",
)
INVENTORY_SPREADSHEET_ID = os.environ.get("INVENTORY_SPREADSHEET_ID", "")
INVENTORY_FOLDER_ID = os.environ.get("INVENTORY_FOLDER_ID", "1iEcdKlf41sAOZpjBPBa2KbFy4rWG129D")
CSV_PATH = Path(__file__).parent / "databmnbuku.csv"
GDRIVE_CREDS_JSON = os.environ.get("GDRIVE_CREDS_JSON", "")
CREDS_PATH = Path(__file__).parent / "gdrive-creds.json"
# Also check for existing service account JSON files
CREDS_GLOB = list(Path(__file__).parent.glob("*.json"))
RAK_CONFIG_PATH = Path(__file__).parent / "rak_config.json"


def _get_gdrive_creds():
    """Load Google service account credentials from env or file."""
    creds_json = GDRIVE_CREDS_JSON
    if not creds_json and CREDS_PATH.exists():
        creds_json = CREDS_PATH.read_text()
    # Also search for any service account JSON in project root
    if not creds_json:
        for p in CREDS_GLOB:
            if p.name in ("vercel.json",):
                continue
            try:
                data = json.loads(p.read_text())
                if data.get("type") == "service_account":
                    creds_json = p.read_text()
                    break
            except (json.JSONDecodeError, KeyError):
                continue
    if not creds_json:
        return None
    return json.loads(creds_json)


# ── CSV (databmnbuku.csv) ───────────────────────────────────────────


def read_csv_local():
    """Read databmnbuku.csv. Kodifikasi = Kode1/Kode2/Kode3."""
    items = []
    try:
        with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                nup = row.get("NUP", "").strip()
                merk = row.get("Merk", "").strip()
                judul = merk if merk and merk != "-" else "Monografi"
                k1 = row.get("Kode1", "").strip()
                k2 = row.get("Kode2", "").strip()
                k3 = row.get("Kode3", "").strip()
                parts = [k for k in [k1, k2, k3] if k and k != "-"]
                kodifikasi = "/".join(parts) if parts else ""
                if nup:
                    items.append({"nup": nup, "judul": judul, "kodifikasi": kodifikasi})
    except Exception as e:
        print(f"Error reading CSV: {e}")
    return items


# ── Google Sheets (Census) ─────────────────────────────────────────


async def read_google_sheet():
    items = []
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(CENSUS_SHEET_URL)
            resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            nup = row.get("NUP", "").strip()
            judul = row.get("Judul", "").strip()
            status_val = row.get("Status", "").strip()
            if nup:
                items.append({
                    "nup": nup,
                    "judul": judul,
                    "status_ditemukan": status_val == "Ditemukan",
                })
    except Exception as e:
        print(f"Error fetching Google Sheet: {e}")
        items = []
    return items


# ── Google Sheets API helper ────────────────────────────────────────
def _get_sheets_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_info = _get_gdrive_creds()
        if not creds_info:
            return None
        creds = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"Sheets API init failed: {e}")
        return None


def _get_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_info = _get_gdrive_creds()
        if not creds_info:
            return None
        creds = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"Drive API init failed: {e}")
        return None


# ── Inventory (Google Drive Excel) ─────────────────────────────────


def _read_sheet_via_api(spreadsheet_id):
    """Read spreadsheet via Google Sheets API."""
    svc = _get_sheets_service()
    if not svc:
        return []
    try:
        meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_name = meta["sheets"][0]["properties"]["title"]
        result = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{sheet_name}'!A:Z")
            .execute()
        )
        rows = result.get("values", [])
        if len(rows) < 2:
            return []
        headers = [h.strip().lower() for h in rows[0]]
        items = []
        for row in rows[1:]:
            vals = row + [""] * (len(headers) - len(row))
            d = dict(zip(headers, vals))
            nup = d.get("nup", "").strip()
            judul = d.get("judul", "").strip()
            catatan = d.get("catatan", "").strip()
            status_val = d.get("status", "").strip()
            if nup:
                items.append({
                    "nup": nup,
                    "judul": judul,
                    "catatan": catatan,
                    "status_ditemukan": status_val == "Ditemukan",
                })
        return items
    except Exception as e:
        print(f"Sheets API read error: {e}")
        return []


def _find_latest_excel():
    """Find latest Excel file in Google Drive folder."""
    svc = _get_drive_service()
    if not svc or not INVENTORY_FOLDER_ID:
        return None
    try:
        q = f"'{INVENTORY_FOLDER_ID}' in parents and mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' and trashed=false"
        resp = svc.files().list(q=q, fields="files(id,name,modifiedTime)", orderBy="modifiedTime desc", pageSize=5).execute()
        files = resp.get("files", [])
        return files[0] if files else None
    except Exception as e:
        print(f"Drive search error: {e}")
        return None


def _download_excel(file_id):
    """Download Excel file from Google Drive."""
    svc = _get_drive_service()
    if not svc:
        return None
    try:
        resp = svc.files().get_media(fileId=file_id).execute()
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.write(resp)
        tmp.close()
        return tmp.name
    except Exception as e:
        print(f"Drive download error: {e}")
        return None


def _parse_excel_file(path):
    """Parse Excel file with openpyxl.
    
    Expected columns: E=NUP, F=Nama Barang, Y=Status Inventarisasi
    Filter: only rows where Nama Barang (col F) = 'Monografi'
    NUP from openpyxl may come as float (e.g. '3.0'), strip to int.
    """
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if len(rows) < 2:
            return []
        headers = [str(h).strip().lower() if h else "" for h in rows[0]]
        nup_idx = None
        status_idx = None
        nama_barang_idx = None
        for i, h in enumerate(headers):
            if "nup" in h:
                nup_idx = i
            if "inventarisasi" in h or h == "status inventarisasi":
                status_idx = i
            if "nama barang" in h or h == "nama barang":
                nama_barang_idx = i
        if nup_idx is None:
            return []
        items = []
        for row in rows[1:]:
            vals = list(row) + [None] * max(0, len(headers) - len(row))
            # Filter: hanya Monografi
            if nama_barang_idx is not None and nama_barang_idx < len(vals):
                nb = str(vals[nama_barang_idx]).strip().lower() if vals[nama_barang_idx] else ""
                if nb != "monografi":
                    continue
            nup_raw = vals[nup_idx]
            if nup_raw is None:
                continue
            # Strip float decimals (e.g. 3.0 → '3', 11.0 → '11')
            if isinstance(nup_raw, float):
                nup = str(int(nup_raw))
            else:
                nup = str(nup_raw).strip()
                if nup.endswith(".0"):
                    try:
                        nup = str(int(float(nup)))
                    except ValueError:
                        pass
            if not nup or nup.lower() in ("none", ""):
                continue
            status = ""
            if status_idx is not None and status_idx < len(vals):
                sv = vals[status_idx]
                status = str(sv).strip() if sv is not None else ""
            items.append({"nup": nup, "status": status})
        return items
    except ImportError:
        print("openpyxl not installed")
        return []
    except Exception as e:
        print(f"Excel parse error: {e}")
        return []


def read_inventory():
    """Read inventory Excel — always fresh, no cache."""
    # Try Google Sheets API first (if spreadsheet ID configured)
    if INVENTORY_SPREADSHEET_ID:
        items = _read_sheet_via_api(INVENTORY_SPREADSHEET_ID)
        if items:
            return items

    # Try Google Drive (find + download + parse Excel)
    file_info = _find_latest_excel()
    if file_info:
        print(f"Inventory file: {file_info.get('name', '?')}")
        path = _download_excel(file_info["id"])
        if path:
            items = _parse_excel_file(path)
            try:
                os.unlink(path)
            except OSError:
                pass
            if items:
                return items

    return []


# ── Rak Config ─────────────────────────────────────────────────────
def load_rak_config():
    """Load rak definitions from JSON file."""
    try:
        with open(RAK_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading rak config: {e}")
        return []


def _parse_kodifikasi_numeric(kodifikasi: str):
    """Extract prefix letter and numeric value from a kodifikasi string.
    
    For single-number codes like 'U 001.3', use as-is.
    For multi-part codes like 'U 899 210 72 MAH' or 'PB 398.209 598 48 JAY c',
    extract only the FIRST number (which is the classification code).
    
    Examples:
      'U 001.3' → ('U', 1.3)
      'K 899.213' → ('K', 899.213)
      'U 899 210 72 MAH' → ('U', 899.21072)
      'PB 398.209' → ('PB', 398.209)
      'PB 398.209 598 48 JAY c' → ('PB', 398.209)
    """
    if not kodifikasi or kodifikasi == "-":
        return None, None
    # Extract prefix: first letter(s)
    prefix = ""
    rest = ""
    for i, c in enumerate(kodifikasi):
        if c.isalpha():
            prefix += c
        else:
            rest = kodifikasi[i:].strip()
            break
    if not prefix:
        return None, None
    # Extract only the first number from rest
    first_num_match = re.match(r'(\d+(?:\.\d+)?)', rest)
    if not first_num_match:
        return prefix, None
    first_num = first_num_match.group(1)
    # For multi-part codes like '899 210 72', check if there are more
    # number parts that should be combined (no existing decimal)
    remaining = rest[first_num_match.end():].strip()
    if '.' not in first_num:
        # Multi-part without decimal: combine next number parts
        # e.g., '899 210 72 MAH' → '89921072'
        extra_nums = re.findall(r'^(\d+)', remaining)
        all_digits = first_num
        temp_remaining = remaining
        for en in extra_nums:
            # Only combine if combined length would give 3+ digit main part
            combined = all_digits + en
            if len(combined) <= 8:  # Reasonable max for DDC code
                all_digits += en
                temp_remaining = temp_remaining[len(en):].strip()
            else:
                break
        try:
            if len(all_digits) > 3:
                main_part = all_digits[:3]
                decimal_part = all_digits[3:]
                val = float(f"{main_part}.{decimal_part}")
            else:
                val = float(all_digits)
        except ValueError:
            return prefix, None
        return prefix, val
    else:
        # Already has decimal: use as-is
        try:
            return prefix, float(first_num)
        except ValueError:
            return prefix, None


def match_rak(kodifikasi: str, rak_config: list):
    """Find which shelf a kodifikasi belongs to."""
    prefix, numeric = _parse_kodifikasi_numeric(kodifikasi)
    if prefix is None or numeric is None:
        return None
    for rak in rak_config:
        if rak["prefix"].upper() == prefix.upper():
            if rak["start"] <= numeric <= rak["end"]:
                return rak["name"]
    return None


def get_rak_progress(csv_items, sheet_items, inventory_items, rak_config):
    """Categorize items by shelf and status."""
    # Build lookups
    sheet_lookup = {}
    for si in sheet_items:
        sheet_lookup[si["nup"]] = si
    inventory_ditemukan = set()
    for inv in inventory_items:
        if inv.get("status", "").strip() == "Ditemukan":
            nup = inv.get("nup", "").strip()
            if nup:
                inventory_ditemukan.add(nup)

    # Initialize rak buckets
    rak_names = [r["name"] for r in rak_config]
    rak_map = {r["name"]: r for r in rak_config}
    
    result = {}
    for name in rak_names:
        result[name] = {
            "config": rak_map[name],
            "belum_ditemukan": [],
            "ditemukan_belum_sensus": [],
            "sudah_sensus_belum_kirim": [],
            "sensus_ditemukan": [],
        }
    unmatched = []

    for ci in csv_items:
        nup = ci["nup"]
        judul = ci["judul"]
        kodifikasi = ci["kodifikasi"]
        rak_name = match_rak(kodifikasi, rak_config)

        # Determine category
        cat = None
        if nup in sheet_lookup:
            si = sheet_lookup[nup]
            if si["status_ditemukan"]:
                cat = "sensus_ditemukan"
            else:
                cat = "ditemukan_belum_sensus"
        else:
            if nup in inventory_ditemukan:
                cat = "sudah_sensus_belum_kirim"
            else:
                cat = "belum_ditemukan"

        item = {"nup": nup, "judul": judul, "kodifikasi": kodifikasi}

        if rak_name and rak_name in result:
            result[rak_name][cat].append(item)
        else:
            unmatched.append({**item, "category": cat})

    return {"raks": result, "unmatched": unmatched}


# ── Helpers ─────────────────────────────────────────────────────────
def get_kodifikasi_group(kodifikasi: str) -> str:
    if not kodifikasi or kodifikasi == "-":
        return "Tanpa Kodifikasi"
    for char in kodifikasi:
        if char.isalpha():
            return char.upper()
    return "Lainnya"


def compare_data(csv_items, sheet_items, inventory_items):
    """Compare CSV, Census Sheet, and Inventory Excel.
    
    Iterate from CSV side so total always equals CSV count.
    For each CSV item:
      1. In Census Sheet with Status='Ditemukan' → sensus_ditemukan
      2. In Census Sheet with Status='' → sensus_belum_ditemukan
      3. NOT in Census Sheet, but in Excel with Status='Ditemukan' → sudah_ditemukan_belum_sensus
      4. NOT in Census Sheet, NOT in Excel → belum_sensus
    """
    # Build Census Sheet lookup (NUP → status)
    sheet_lookup = {}
    for si in sheet_items:
        sheet_lookup[si["nup"]] = si

    # Build Excel inventory NUP set (only Status='Ditemukan')
    inventory_ditemukan_nups = set()
    for inv in inventory_items:
        if inv.get("status", "").strip() == "Ditemukan":
            nup = inv.get("nup", "").strip()
            if nup:
                inventory_ditemukan_nups.add(nup)

    belum_sensus = []
    sensus_ditemukan = []
    sensus_belum_ditemukan = []  # Ditemukan, Belum Sensus
    sudah_ditemukan_belum_sensus = []  # Sensus, Belum Dikirim

    for ci in csv_items:
        nup = ci["nup"]
        judul = ci["judul"]
        kodifikasi = ci["kodifikasi"]

        if nup in sheet_lookup:
            si = sheet_lookup[nup]
            display_judul = si["judul"] if si["judul"] else judul
            if si["status_ditemukan"]:
                # Category 1: Sensus & Ditemukan
                sensus_ditemukan.append({
                    "nup": nup, "judul": display_judul, "kodifikasi": kodifikasi,
                })
            else:
                # Category 2: Ditemukan, Belum Sensus (in Sheet, Status='')
                sensus_belum_ditemukan.append({
                    "nup": nup, "judul": display_judul, "kodifikasi": kodifikasi,
                })
        else:
            # Not in Census Sheet
            if nup in inventory_ditemukan_nups:
                # Category 3: Sensus, Belum Dikirim (in Excel but not in Census Sheet)
                sudah_ditemukan_belum_sensus.append({
                    "nup": nup, "judul": judul, "kodifikasi": kodifikasi,
                })
            else:
                # Category 4: Belum Ditemukan dan Dikirim
                belum_sensus.append({
                    "nup": nup, "judul": judul, "kodifikasi": kodifikasi,
                })

    return {
        "belum_sensus": belum_sensus,
        "sensus_ditemukan": sensus_ditemukan,
        "sensus_belum_ditemukan": sensus_belum_ditemukan,
        "sudah_ditemukan_belum_sensus": sudah_ditemukan_belum_sensus,
    }


def group_by_kodifikasi(items):
    groups = defaultdict(list)
    for item in items:
        group = get_kodifikasi_group(item["kodifikasi"])
        groups[group].append(item)
    return dict(sorted(groups.items()))


# ── HTML Template ───────────────────────────────────────────────────
SENsus_HTML = """<!DOCTYPE html>
<html lang="id" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sensus BMN - PLUVIO</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
                    colors: {
                        dark: { 900: '#1a0f00', 800: '#2d1a00', 700: '#3d2500' },
                        orange: { 500: '#f97316', 600: '#ea580c', 700: '#c2410c' },
                    }
                }
            }
        }
    </script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', system-ui, sans-serif; }
        .glass { background: rgba(0,0,0,0.7); backdrop-filter: blur(20px); border: 1px solid rgba(255,150,50,0.15); }
        .glass-hover:hover { background: rgba(0,0,0,0.8); border-color: rgba(255,150,50,0.3); }
        .gradient-border { position: relative; }
        .gradient-border::before { content: ''; position: absolute; inset: -1px; border-radius: inherit; padding: 1px; background: linear-gradient(135deg, rgba(249,115,22,0.4), rgba(234,88,12,0.4), rgba(194,65,12,0.4)); -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0); -webkit-mask-composite: xor; mask-composite: exclude; pointer-events: none; opacity: 0; transition: opacity 0.3s; }
        .gradient-border:hover::before { opacity: 1; }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .fade-up { animation: fadeUp 0.5s ease-out forwards; }
        .scrollbar-thin::-webkit-scrollbar { width: 6px; }
        .scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
        .scrollbar-thin::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }
        .scrollbar-thin::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }
    </style>
</head>
<body class="bg-dark-900 text-gray-200 min-h-screen">

    <!-- Header -->
    <header class="sticky top-0 z-50 glass border-b border-white/5">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-orange-500 to-orange-700 flex items-center justify-center text-white font-bold text-lg shadow-lg shadow-orange-500/20">P</div>
                <div>
                    <h1 class="text-lg font-bold text-white tracking-tight">PLUVIO</h1>
                    <p class="text-[11px] text-gray-400 font-medium tracking-wide uppercase">Sensus BMN Dashboard</p>
                </div>
            </div>
            <div class="flex items-center gap-3">
                <div id="lastUpdated" class="text-xs text-gray-400 hidden sm:block"></div>
                <a href="/rak" class="text-xs text-orange-400 hover:text-orange-300 font-medium px-3 py-1.5 rounded-lg border border-orange-500/20 hover:border-orange-500/40 transition-colors">📊 Progress per Rak</a>
            </div>
        </div>
    </header>

    <!-- Main -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 py-8">
        <!-- Title -->
        <div class="text-center mb-10 fade-up">
            <h2 class="text-3xl sm:text-4xl font-extrabold text-white mb-3 tracking-tight">                    Sensus <span class="bg-gradient-to-r from-orange-400 via-orange-500 to-yellow-400 bg-clip-text text-transparent">BMN</span>
            </h2>
            <p class="text-gray-300 text-sm max-w-lg mx-auto">Perbandingan data <span class="text-orange-400">databmnbuku.csv</span> dengan Google Sheet sensus & inventarisasi</p>
        </div>

        <!-- Stats -->
        <div id="stats" class="mb-10"></div>

        <!-- Search -->
        <div id="searchWrap" class="mb-8 hidden">
            <div class="relative max-w-xl mx-auto">
                <svg class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                <input type="text" id="searchInput" class="w-full pl-12 pr-4 py-3.5 rounded-2xl glass text-white placeholder-gray-500 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500/40 transition-all" placeholder="Cari NUP atau Judul buku...">
                <span id="searchCount" class="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-gray-400 hidden"></span>
            </div>
        </div>

        <!-- Content -->
        <div id="content">
            <div class="flex flex-col items-center justify-center py-24">
                <div class="w-12 h-12 border-3 border-orange-500/20 border-t-orange-500 rounded-full animate-spin mb-4"></div>
                <p class="text-gray-300 text-sm">Memuat data sensus...</p>
            </div>
        </div>
    </main>

    <!-- Footer -->
    <footer class="text-center py-8 text-xs text-gray-700">
        Built with FastAPI & deployed on Vercel
    </footer>

    <script>
        // ── Helpers ──
        function getGroup(k) {
            if (!k || k === '-') return 'Tanpa Kodifikasi';
            for (let c of k) { if (/[a-zA-Z]/.test(c)) return c.toUpperCase(); }
            return 'Lainnya';
        }
        function groupByKodifikasi(items) {
            const g = {};
            items.forEach(item => {
                const key = getGroup(item.kodifikasi);
                if (!g[key]) g[key] = [];
                g[key].push(item);
            });
            return Object.keys(g).sort().map(k => ({ name: k, items: g[k] }));
        }
        function escapeHtml(s) {
            const d = document.createElement('div');
            d.textContent = s;
            return d.innerHTML;
        }

        // ── Renderers ──
        function renderGroups(groups, catId) {
            if (groups.length === 0) return '<p class="text-gray-400 text-sm py-4 text-center">Tidak ada data</p>';
            return groups.map((g, gi) => `
                <div class="mb-3" data-group="${catId}-${g.name}">
                    <button onclick="toggleGroup(this)" class="w-full flex items-center justify-between px-4 py-3 rounded-xl bg-white/[0.02] hover:bg-white/[0.05] transition-all text-left group">
                        <div class="flex items-center gap-3">
                            <svg class="w-4 h-4 text-gray-400 group-hover:text-orange-400 transition-transform chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
                            <span class="text-sm font-semibold text-orange-300/80">${escapeHtml(g.name)}</span>
                        </div>
                        <span class="text-xs text-gray-400 bg-white/5 px-2.5 py-1 rounded-full">${g.items.length} item</span>
                    </button>
                    <div class="hidden mt-2 ml-4 overflow-x-auto scrollbar-thin">
                        <table class="w-full text-sm">
                            <thead><tr class="text-left text-[11px] text-gray-400 uppercase tracking-wider">
                                <th class="pb-2 pl-4 w-12">#</th>
                                <th class="pb-2 w-28">NUP</th>
                                <th class="pb-2">Judul</th>
                                <th class="pb-2 w-24">Kodifikasi</th>
                            </tr></thead>
                            <tbody>${g.items.map((item, i) => `
                                <tr class="border-t border-white/[0.03] hover:bg-white/[0.02]">
                                    <td class="py-2 pl-4 text-gray-400 text-xs">${i + 1}</td>
                                    <td class="py-2 font-mono text-orange-400/80 font-semibold text-xs whitespace-nowrap">${escapeHtml(item.nup)}</td>
                                    <td class="py-2 pr-4 text-gray-300 text-xs">${escapeHtml(item.judul.length > 65 ? item.judul.substring(0, 65) + '…' : item.judul)}</td>
                                    <td class="py-2 text-orange-300 text-xs font-mono font-semibold whitespace-nowrap">${escapeHtml(item.kodifikasi)}</td>
                                </tr>`).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            `).join('');
        }

        function toggleGroup(btn) {
            const content = btn.nextElementSibling;
            const chevron = btn.querySelector('.chevron');
            content.classList.toggle('hidden');
            chevron.style.transform = content.classList.contains('hidden') ? '' : 'rotate(90deg)';
        }

        function toggleCategory(catId) {
            const body = document.getElementById(catId + '-body');
            const chevron = document.querySelector(`[onclick="toggleCategory('${catId}')"] .chevron`);
            body.classList.toggle('hidden');
            if (chevron) chevron.style.transform = body.classList.contains('hidden') ? '' : 'rotate(90deg)';
        }

        function scrollToSection(id) {
            document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }

        // ── Search ──
        let allData = {};
        document.addEventListener('DOMContentLoaded', () => {
            document.getElementById('searchInput')?.addEventListener('input', function() {
                const q = this.value.toLowerCase().trim();
                const countEl = document.getElementById('searchCount');
                let total = 0;
                document.querySelectorAll('tbody tr').forEach(row => {
                    const match = !q || row.textContent.toLowerCase().includes(q);
                    row.style.display = match ? '' : 'none';
                    if (match) total++;
                });
                if (q) {
                    // Expand collapsed groups
                    document.querySelectorAll('#content .hidden').forEach(el => {
                        if (el.tagName !== 'INPUT') el.classList.remove('hidden');
                    });
                    document.querySelectorAll('.chevron').forEach(c => c.style.transform = 'rotate(90deg)');
                    countEl.textContent = total + ' hasil';
                    countEl.classList.remove('hidden');
                } else {
                    countEl.classList.add('hidden');
                }
            });
        });

        // ── Stat Card HTML ──
        function statCard(id, icon, color, count, label, delay) {
            return `
                <button onclick="scrollToSection('${id}')" class="glass glass-hover gradient-border rounded-2xl p-5 text-center transition-all duration-300 hover:scale-[1.02] hover:-translate-y-1 fade-up" style="animation-delay:${delay}ms">
                    <div class="text-3xl sm:text-4xl font-extrabold mb-1" style="color:${color}">${count.toLocaleString()}</div>
                    <div class="text-[11px] text-gray-400 font-medium uppercase tracking-wider">${icon} ${label}</div>
                </button>`;
        }

        function statCardTotal(count, delay) {
            return `
                <div class="glass rounded-2xl p-5 text-center fade-up" style="animation-delay:${delay}ms">
                    <div class="text-3xl sm:text-4xl font-extrabold mb-1 bg-gradient-to-r from-orange-400 via-orange-500 to-yellow-400 bg-clip-text text-transparent">${count.toLocaleString()}</div>
                    <div class="text-[11px] text-gray-400 font-medium uppercase tracking-wider">📊 Total Data</div>
                </div>`;
        }

        function categoryHTML(catId, icon, colorClass, title, count, groups, delay) {
            return `
                <div class="glass rounded-2xl overflow-hidden mb-6 fade-up" style="animation-delay:${delay}ms" id="sec-${catId}">
                    <button onclick="toggleCategory('${catId}')" class="w-full flex items-center justify-between px-6 py-5 hover:bg-white/[0.02] transition-colors text-left">
                        <div class="flex items-center gap-4">
                            <span class="text-2xl">${icon}</span>
                            <div>
                                <h3 class="text-base font-bold text-white">${title}</h3>
                                <p class="text-xs text-gray-400 mt-0.5">${count.toLocaleString()} item</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-3">
                            <span class="text-xs font-bold px-3 py-1.5 rounded-full ${colorClass}">${count.toLocaleString()}</span>
                            <svg class="w-5 h-5 text-gray-400 chevron transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
                        </div>
                    </button>
                    <div id="${catId}-body" class="hidden px-6 pb-6">
                        ${renderGroups(groups, catId)}
                    </div>
                </div>`;
        }

        // ── Load Data ──
        async function loadData() {
            try {
                const resp = await fetch('/api/sensus');
                if (!resp.ok) throw new Error('API error: ' + resp.status);
                const data = await resp.json();
                allData = data;

                const bs = data.belum_sensus || [];
                const sd = data.sensus_ditemukan || [];
                const sbd = data.sensus_belum_ditemukan || [];
                const ds = data.sudah_ditemukan_belum_sensus || [];
                const total = bs.length + sd.length + sbd.length + ds.length;

                document.getElementById('stats').innerHTML = `
                    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 sm:gap-4">
                        ${statCard('sec-bs', '❌', '#f87171', bs.length, 'Belum Ditemukan & Dikirim', 0)}
                        ${statCard('sec-sbd', '⚠️', '#facc15', sbd.length, 'Ditemukan, Belum Sensus', 80)}
                        ${statCard('sec-ds', '📋', '#fb923c', ds.length, 'Sensus, Belum Dikirim', 160)}
                        ${statCard('sec-sd', '✅', '#4ade80', sd.length, 'Sensus & Ditemukan', 240)}
                        ${statCardTotal(total, 320)}
                    </div>`;

                document.getElementById('searchWrap').classList.remove('hidden');

                document.getElementById('content').innerHTML =
                    categoryHTML('bs', '❌', 'bg-red-500/15 text-red-400', 'Belum Ditemukan dan Dikirim', bs.length, groupByKodifikasi(bs), 400) +
                    categoryHTML('sbd', '⚠️', 'bg-yellow-500/15 text-yellow-400', 'Ditemukan, Belum Sensus', sbd.length, groupByKodifikasi(sbd), 480) +
                    categoryHTML('ds', '📋', 'bg-orange-500/15 text-orange-400', 'Sensus, Belum Dikirim', ds.length, groupByKodifikasi(ds), 560) +
                    categoryHTML('sd', '✅', 'bg-emerald-500/15 text-emerald-400', 'Sensus & Ditemukan', sd.length, groupByKodifikasi(sd), 640);

                // Show last updated
                const now = new Date();
                document.getElementById('lastUpdated').textContent = 'Updated: ' + now.toLocaleString('id-ID');

            } catch(e) {
                document.getElementById('content').innerHTML = `
                    <div class="text-center py-16">
                        <div class="text-4xl mb-4">❌</div>
                        <p class="text-red-400 font-medium">Gagal memuat data</p>
                        <p class="text-gray-600 text-sm mt-2">${escapeHtml(e.message)}</p>
                        <button onclick="loadData()" class="mt-4 px-6 py-2.5 rounded-xl bg-orange-500/20 text-orange-400 text-sm font-medium hover:bg-orange-500/30 transition-colors">Coba Lagi</button>
                    </div>`;
            }
        }
        loadData();
    </script>
</body>
</html>"""


# ── Rak HTML Template ──────────────────────────────────────────────
RAK_HTML = """<!DOCTYPE html>
<html lang="id" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Progress per Rak - PLUVIO</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
                    colors: {
                        dark: { 900: '#1a0f00', 800: '#2d1a00', 700: '#3d2500' },
                    }
                }
            }
        }
    </script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', system-ui, sans-serif; }
        .glass { background: rgba(0,0,0,0.7); backdrop-filter: blur(20px); border: 1px solid rgba(255,150,50,0.15); }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .fade-up { animation: fadeUp 0.5s ease-out forwards; }
        .scrollbar-thin::-webkit-scrollbar { width: 6px; }
        .scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
        .scrollbar-thin::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }
    </style>
</head>
<body class="bg-dark-900 text-gray-200 min-h-screen">
    <!-- Header -->
    <header class="sticky top-0 z-50 glass border-b border-white/5">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <a href="/" class="w-10 h-10 rounded-xl bg-gradient-to-br from-orange-500 to-orange-700 flex items-center justify-center text-white font-bold text-lg shadow-lg shadow-orange-500/20 hover:scale-105 transition-transform">P</a>
                <div>
                    <h1 class="text-lg font-bold text-white tracking-tight">PLUVIO</h1>
                    <p class="text-[11px] text-gray-400 font-medium tracking-wide uppercase">Progress per Rak</p>
                </div>
            </div>
            <a href="/" class="text-xs text-orange-400 hover:text-orange-300 font-medium px-3 py-1.5 rounded-lg border border-orange-500/20 hover:border-orange-500/40 transition-colors">← Sensus BMN</a>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-4 sm:px-6 py-8">
        <div class="text-center mb-10 fade-up">
            <h2 class="text-3xl sm:text-4xl font-extrabold text-white mb-3 tracking-tight">
                Progress <span class="bg-gradient-to-r from-orange-400 via-orange-500 to-yellow-400 bg-clip-text text-transparent">per Rak</span>
            </h2>
            <p class="text-gray-300 text-sm max-w-lg mx-auto">Status sensus BMN berdasarkan <span class="text-orange-400">kelompok rak</span> yang sudah didefinisikan</p>
        </div>

        <!-- Filter Tabs -->
        <div class="flex flex-wrap justify-center gap-2 mb-8 fade-up" style="animation-delay:100ms">
            <button onclick="filterStatus('all')" id="tab-all" class="filter-tab active px-4 py-2 rounded-xl text-sm font-medium transition-all bg-orange-500 text-white">Semua</button>
            <button onclick="filterStatus('belum_ditemukan')" id="tab-bs" class="filter-tab px-4 py-2 rounded-xl text-sm font-medium transition-all bg-white/5 text-gray-400 hover:bg-white/10">❌ Belum Ditemukan</button>
            <button onclick="filterStatus('ditemukan_belum_sensus')" id="tab-sbd" class="filter-tab px-4 py-2 rounded-xl text-sm font-medium transition-all bg-white/5 text-gray-400 hover:bg-white/10">⚠️ Ditemukan, Belum Sensus</button>
            <button onclick="filterStatus('sudah_sensus_belum_kirim')" id="tab-ds" class="filter-tab px-4 py-2 rounded-xl text-sm font-medium transition-all bg-white/5 text-gray-400 hover:bg-white/10">📋 Sudah Sensus, Belum Kirim</button>
            <button onclick="filterStatus('sensus_ditemukan')" id="tab-sd" class="filter-tab px-4 py-2 rounded-xl text-sm font-medium transition-all bg-white/5 text-gray-400 hover:bg-white/10">✅ Sensus & Ditemukan</button>
        </div>

        <!-- Search -->
        <div class="mb-8 fade-up" style="animation-delay:150ms">
            <div class="relative max-w-xl mx-auto">
                <svg class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                <input type="text" id="searchInput" class="w-full pl-12 pr-4 py-3.5 rounded-2xl glass text-white placeholder-gray-500 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500/40 transition-all" placeholder="Cari NUP, Judul, atau Rak...">
            </div>
        </div>

        <!-- Content -->
        <div id="content">
            <div class="flex flex-col items-center justify-center py-24">
                <div class="w-12 h-12 border-3 border-orange-500/20 border-t-orange-500 rounded-full animate-spin mb-4"></div>
                <p class="text-gray-300 text-sm">Memuat data rak...</p>
            </div>
        </div>
    </main>

    <footer class="text-center py-8 text-xs text-gray-700">Built with FastAPI & deployed on Vercel</footer>

    <script>
        let rakData = null;
        let currentFilter = 'all';

        function escapeHtml(s) {
            const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
        }

        function getStatusColor(cat) {
            const colors = {
                belum_ditemukan: { bg: 'bg-red-500/15', text: 'text-red-400', dot: 'bg-red-500' },
                ditemukan_belum_sensus: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', dot: 'bg-yellow-500' },
                sudah_sensus_belum_kirim: { bg: 'bg-orange-500/15', text: 'text-orange-400', dot: 'bg-orange-500' },
                sensus_ditemukan: { bg: 'bg-emerald-500/15', text: 'text-emerald-400', dot: 'bg-emerald-500' },
            };
            return colors[cat] || colors.belum_ditemukan;
        }

        function getStatusLabel(cat) {
            const labels = {
                belum_ditemukan: '❌ Belum Ditemukan',
                ditemukan_belum_sensus: '⚠️ Ditemukan, Belum Sensus',
                sudah_sensus_belum_kirim: '📋 Sudah Sensus, Belum Kirim',
                sensus_ditemukan: '✅ Sensus & Ditemukan',
            };
            return labels[cat] || cat;
        }

        function filterStatus(cat) {
            currentFilter = cat;
            document.querySelectorAll('.filter-tab').forEach(t => {
                t.className = 'filter-tab px-4 py-2 rounded-xl text-sm font-medium transition-all bg-white/5 text-gray-400 hover:bg-white/10';
            });
            const activeTab = document.getElementById('tab-' + (cat === 'all' ? 'all' : cat.split('_')[0] === 'belum' ? 'bs' : cat === 'ditemukan_belum_sensus' ? 'sbd' : cat === 'sudah_sensus_belum_kirim' ? 'ds' : 'sd'));
            if (cat === 'all') {
                document.getElementById('tab-all').className = 'filter-tab px-4 py-2 rounded-xl text-sm font-medium transition-all bg-orange-500 text-white';
            } else {
                const map = { belum_ditemukan: 'bs', ditemukan_belum_sensus: 'sbd', sudah_sensus_belum_kirim: 'ds', sensus_ditemukan: 'sd' };
                const el = document.getElementById('tab-' + map[cat]);
                if (el) el.className = 'filter-tab px-4 py-2 rounded-xl text-sm font-medium transition-all bg-orange-500 text-white';
            }
            renderRaks();
        }

        function renderRaks() {
            if (!rakData) return;
            const raks = rakData.raks;
            const search = document.getElementById('searchInput').value.toLowerCase().trim();
            const container = document.getElementById('content');

            // Sort raks by kodifikasi order (prefix + numeric)
            const prefixOrder = {'D':1,'K':2,'L':3,'M':4,'PB':5,'R':6,'S':7,'U':8};
            let sorted = Object.entries(raks).map(([name, data]) => {
                const total = data.belum_ditemukan.length + data.ditemukan_belum_sensus.length + data.sudah_sensus_belum_kirim.length + data.sensus_ditemukan.length;
                const prefix = (data.config?.prefix || 'ZZZ').toUpperCase();
                const numeric = data.config?.start || 0;
                const prefixRank = prefixOrder[prefix] || 99;
                return { name, data, total, prefixRank, numeric };
            }).filter(r => r.total > 0);

            // Also add unmatched items as a "Lainnya" group
            if (rakData.unmatched && rakData.unmatched.length > 0) {
                const umItems = rakData.unmatched;
                const umData = {
                    config: { prefix: 'ZZZ', start: 0, end: 999, category: 'Tanpa Rak' },
                    belum_ditemukan: umItems.filter(i => i.category === 'belum_ditemukan'),
                    ditemukan_belum_sensus: umItems.filter(i => i.category === 'ditemukan_belum_sensus'),
                    sudah_sensus_belum_kirim: umItems.filter(i => i.category === 'sudah_sensus_belum_kirim'),
                    sensus_ditemukan: umItems.filter(i => i.category === 'sensus_ditemukan'),
                };
                const umTotal = umItems.length;
                if (umTotal > 0) sorted.push({ name: 'Lainnya (Tanpa Rak)', data: umData, total: umTotal, prefixRank: 100, numeric: 0 });
            }

            sorted.sort((a, b) => b.data.belum_ditemukan.length - a.data.belum_ditemukan.length || a.prefixRank - b.prefixRank || a.numeric - b.numeric);

            let html = '<div class="grid gap-4">';

            sorted.forEach((rak, idx) => {
                const { name, data, total } = rak;
                const cfg = data.config;
                const cats = ['belum_ditemukan', 'ditemukan_belum_sensus', 'sudah_sensus_belum_kirim', 'sensus_ditemukan'];

                // Build bar segments
                const segments = cats.map(cat => {
                    const count = data[cat].length;
                    if (count === 0) return '';
                    const colors = { belum_ditemukan: '#f87171', ditemukan_belum_sensus: '#facc15', sudah_sensus_belum_kirim: '#fb923c', sensus_ditemukan: '#4ade80' };
                    const pct = total > 0 ? (count / total * 100) : 0;
                    return `<div style="width:${pct}%;background:${colors[cat]}" class="h-full rounded-sm min-w-[2px]" title="${getStatusLabel(cat)}: ${count}"></div>`;
                }).join('');

                // Build items for active filter
                let itemsHtml = '';
                if (currentFilter !== 'all') {
                    const items = data[currentFilter] || [];
                    let filtered = items;
                    if (search) {
                        filtered = items.filter(it => it.nup.toLowerCase().includes(search) || it.judul.toLowerCase().includes(search) || name.toLowerCase().includes(search));
                    }
                    // Sort by kodifikasi
                    filtered = [...filtered].sort((a, b) => (a.kodifikasi || '').localeCompare(b.kodifikasi || '', undefined, {numeric: true}));
                    if (filtered.length > 0) {
                        itemsHtml = `<div class="mt-3 ml-4 overflow-x-auto scrollbar-thin max-h-64 overflow-y-auto">
                            <table class="w-full text-sm">
                                <thead><tr class="text-left text-[11px] text-gray-400 uppercase tracking-wider">
                                    <th class="pb-2 pl-3 w-10">#</th>
                                    <th class="pb-2 w-24">NUP</th>
                                    <th class="pb-2">Judul</th>
                                    <th class="pb-2 w-24">Kodifikasi</th>
                                </tr></thead>
                                <tbody>${filtered.map((it, i) => `
                                    <tr class="border-t border-white/[0.03] hover:bg-white/[0.02]">
                                        <td class="py-1.5 pl-3 text-gray-500 text-xs">${i+1}</td>
                                        <td class="py-1.5 font-mono text-orange-400/80 font-semibold text-xs whitespace-nowrap">${escapeHtml(it.nup)}</td>
                                        <td class="py-1.5 pr-3 text-gray-300 text-xs">${escapeHtml(it.judul.length > 60 ? it.judul.substring(0,60)+'…' : it.judul)}</td>
                                        <td class="py-1.5 text-orange-300 text-xs font-mono">${escapeHtml(it.kodifikasi)}</td>
                                    </tr>`).join('')}
                                </tbody>
                            </table>
                        </div>`;
                    } else {
                        itemsHtml = `<div class="mt-2 text-center text-xs text-gray-500">Tidak ada item</div>`;
                    }
                }

                // Filter items for search match
                if (currentFilter === 'all' && search) {
                    const allItems = [...data.belum_ditemukan, ...data.ditemukan_belum_sensus, ...data.sudah_sensus_belum_kirim, ...data.sensus_ditemukan];
                    const matchCount = allItems.filter(it => it.nup.toLowerCase().includes(search) || it.judul.toLowerCase().includes(search)).length;
                    if (matchCount === 0) return;
                }

                html += `
                <div class="glass rounded-2xl overflow-hidden fade-up" style="animation-delay:${idx * 50}ms">
                    <button onclick="toggleRak(this)" class="w-full flex items-center gap-4 px-5 py-4 hover:bg-white/[0.02] transition-colors text-left">
                        <svg class="w-4 h-4 text-gray-400 chevron transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
                        <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-3">
                                <span class="text-base font-bold text-white">${escapeHtml(name)}</span>
                                <span class="text-[10px] text-gray-500 px-2 py-0.5 rounded-full bg-white/5">${escapeHtml(cfg.category)}</span>
                                <span class="text-[10px] text-gray-500 font-mono">${escapeHtml(cfg.prefix)} ${cfg.start} – ${cfg.end}</span>
                            </div>
                            <div class="flex gap-2 mt-1.5">
                                ${data.sensus_ditemukan.length ? `<span class="text-[10px] text-emerald-400">✅ ${data.sensus_ditemukan.length}</span>` : ''}
                                ${data.ditemukan_belum_sensus.length ? `<span class="text-[10px] text-yellow-400">⚠️ ${data.ditemukan_belum_sensus.length}</span>` : ''}
                                ${data.sudah_sensus_belum_kirim.length ? `<span class="text-[10px] text-orange-400">📋 ${data.sudah_sensus_belum_kirim.length}</span>` : ''}
                                ${data.belum_ditemukan.length ? `<span class="text-[10px] text-red-400">❌ ${data.belum_ditemukan.length}</span>` : ''}
                            </div>
                        </div>
                        <div class="flex items-center gap-3">
                            <div class="w-32 h-2 rounded-full bg-white/5 overflow-hidden hidden sm:flex gap-px">${segments}</div>
                            <span class="text-sm font-bold text-orange-400 w-10 text-right">${total}</span>
                        </div>
                    </button>
                    <div class="rak-items hidden px-5 pb-4">${itemsHtml}</div>
                </div>`;
            });

            html += '</div>';
            container.innerHTML = html;
        }

        function toggleRak(btn) {
            const items = btn.nextElementSibling;
            const chevron = btn.querySelector('.chevron');
            items.classList.toggle('hidden');
            chevron.style.transform = items.classList.contains('hidden') ? '' : 'rotate(90deg)';
        }

        document.getElementById('searchInput')?.addEventListener('input', renderRaks);

        async function loadData() {
            try {
                const resp = await fetch('/api/rak');
                if (!resp.ok) throw new Error('API error');
                rakData = await resp.json();
                renderRaks();
            } catch(e) {
                document.getElementById('content').innerHTML = `
                    <div class="text-center py-16">
                        <div class="text-4xl mb-4">❌</div>
                        <p class="text-red-400 font-medium">Gagal memuat data</p>
                        <p class="text-gray-600 text-sm mt-2">${escapeHtml(e.message)}</p>
                        <button onclick="loadData()" class="mt-4 px-6 py-2.5 rounded-xl bg-orange-500/20 text-orange-400 text-sm font-medium hover:bg-orange-500/30 transition-colors">Coba Lagi</button>
                    </div>`;
            }
        }
        loadData();
    </script>
</body>
</html>"""


# ── Routes ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    return SENsus_HTML


@app.get("/sensus", response_class=HTMLResponse)
async def sensus_page():
    return SENsus_HTML


@app.get("/rak", response_class=HTMLResponse)
async def rak_page():
    return RAK_HTML


@app.get("/api/sensus")
async def get_sensus_data():
    csv_items = read_csv_local()
    sheet_items = await read_google_sheet()
    inventory_items = read_inventory()
    return compare_data(csv_items, sheet_items, inventory_items)


@app.get("/api/rak")
async def get_rak_data():
    csv_items = read_csv_local()
    sheet_items = await read_google_sheet()
    inventory_items = read_inventory()
    rak_config = load_rak_config()
    return get_rak_progress(csv_items, sheet_items, inventory_items, rak_config)


@app.get("/api/profile")
async def get_profile():
    return {"name": "PLUVIO", "description": "Sensus BMN Dashboard"}
