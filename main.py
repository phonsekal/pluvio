from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
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
app.mount("/static", StaticFiles(directory="static"), name="static")

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
    
    Expected columns: E=NUP, Y=Status Inventarisasi
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
        for i, h in enumerate(headers):
            if "nup" in h:
                nup_idx = i
            if "inventarisasi" in h or h == "status inventarisasi":
                status_idx = i
        if nup_idx is None:
            return []
        items = []
        for row in rows[1:]:
            vals = list(row) + [None] * max(0, len(headers) - len(row))
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
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', system-ui, sans-serif; min-height: 100vh; background: #0a0a0a; }
        .bg-dramatic {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: url('/static/background.jpg') center center / cover no-repeat;
            filter: brightness(0.3) saturate(1.3);
            z-index: -2;
        }
        .bg-overlay {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: linear-gradient(135deg, rgba(249,115,22,0.15) 0%, transparent 40%, rgba(234,88,12,0.1) 100%),
                        linear-gradient(to bottom, transparent 0%, rgba(10,10,10,0.7) 100%);
            z-index: -1;
        }
        .glass { background: rgba(0,0,0,0.45); backdrop-filter: blur(24px) saturate(1.5); border: 1px solid rgba(249,115,22,0.15); }
        .glass-hover:hover { background: rgba(0,0,0,0.55); border-color: rgba(249,115,22,0.3); }
        .gradient-border { position: relative; }
        .gradient-border::before { content: ''; position: absolute; inset: -1px; border-radius: inherit; padding: 1px; background: linear-gradient(135deg, rgba(249,115,22,0.5), rgba(234,88,12,0.5), rgba(250,204,21,0.3)); -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0); -webkit-mask-composite: xor; mask-composite: exclude; pointer-events: none; opacity: 0; transition: opacity 0.4s; }
        .gradient-border:hover::before { opacity: 1; }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(30px) scale(0.97); } to { opacity: 1; transform: translateY(0) scale(1); } }
        .fade-up { animation: fadeUp 0.6s cubic-bezier(0.16,1,0.3,1) forwards; }
        @keyframes pulse-glow { 0%, 100% { box-shadow: 0 0 20px rgba(249,115,22,0.1); } 50% { box-shadow: 0 0 40px rgba(249,115,22,0.2); } }
        .glow-pulse { animation: pulse-glow 3s ease-in-out infinite; }
        @keyframes float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
        .float { animation: float 4s ease-in-out infinite; }
        .scrollbar-thin::-webkit-scrollbar { width: 6px; }
        .scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
        .scrollbar-thin::-webkit-scrollbar-thumb { background: rgba(249,115,22,0.3); border-radius: 3px; }
        .scrollbar-thin::-webkit-scrollbar-thumb:hover { background: rgba(249,115,22,0.5); }
    </style>
</head>
<body class="text-gray-200 min-h-screen">
    <div class="bg-dramatic"></div>
    <div class="bg-overlay"></div>

    <!-- Header -->
    <header class="sticky top-0 z-50 glass border-b border-orange-500/10 glow-pulse">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-orange-500 to-orange-700 flex items-center justify-center text-white font-bold text-lg shadow-lg shadow-orange-500/30 float">P</div>
                <div>
                    <h1 class="text-lg font-bold text-white tracking-tight">PLUVIO</h1>
                    <p class="text-[11px] text-gray-500 font-medium tracking-wide uppercase">Sensus BMN Dashboard</p>
                </div>
            </div>
            <div id="lastUpdated" class="text-xs text-gray-600 hidden sm:block"></div>
        </div>
    </header>

    <!-- Main -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 py-8">
        <!-- Title -->
        <div class="text-center mb-10 fade-up">
            <h2 class="text-4xl sm:text-5xl font-black text-white mb-3 tracking-tight drop-shadow-lg">                    📚 Sensus <span class="bg-gradient-to-r from-orange-400 via-amber-400 to-yellow-300 bg-clip-text text-transparent">BMN</span>
            </h2>
            <p class="text-gray-500 text-sm max-w-lg mx-auto">Perbandingan data <span class="text-gray-400">databmnbuku.csv</span> dengan Google Sheet sensus & inventarisasi</p>
        </div>

        <!-- Stats -->
        <div id="stats" class="mb-10"></div>

        <!-- Search -->
        <div id="searchWrap" class="mb-8 hidden">
            <div class="relative max-w-xl mx-auto">
                <svg class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                <input type="text" id="searchInput" class="w-full pl-12 pr-4 py-3.5 rounded-2xl glass text-white placeholder-gray-500 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500/40 transition-all" placeholder="Cari NUP atau Judul buku...">
                <span id="searchCount" class="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-gray-600 hidden"></span>
            </div>
        </div>

        <!-- Content -->
        <div id="content">
            <div class="flex flex-col items-center justify-center py-24">
                <div class="w-12 h-12 border-3 border-orange-500/20 border-t-orange-500 rounded-full animate-spin mb-4"></div>
                <p class="text-gray-500 text-sm">Memuat data sensus...</p>
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
            if (groups.length === 0) return '<p class="text-gray-600 text-sm py-4 text-center">Tidak ada data</p>';
            return groups.map((g, gi) => `
                <div class="mb-3" data-group="${catId}-${g.name}">
                    <button onclick="toggleGroup(this)" class="w-full flex items-center justify-between px-4 py-3 rounded-xl bg-white/[0.02] hover:bg-white/[0.05] transition-all text-left group">
                        <div class="flex items-center gap-3">
                            <svg class="w-4 h-4 text-gray-500 group-hover:text-orange-400 transition-transform chevron" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
                            <span class="text-sm font-semibold text-orange-300/80">${escapeHtml(g.name)}</span>
                        </div>
                        <span class="text-xs text-gray-600 bg-white/5 px-2.5 py-1 rounded-full">${g.items.length} item</span>
                    </button>
                    <div class="hidden mt-2 ml-4 overflow-x-auto scrollbar-thin">
                        <table class="w-full text-sm">
                            <thead><tr class="text-left text-[11px] text-gray-600 uppercase tracking-wider">
                                <th class="pb-2 pl-4 w-12">#</th>
                                <th class="pb-2 w-28">NUP</th>
                                <th class="pb-2">Judul</th>
                                <th class="pb-2 w-24">Kodifikasi</th>
                            </tr></thead>
                            <tbody>${g.items.map((item, i) => `
                                <tr class="border-t border-white/[0.03] hover:bg-white/[0.02]">
                                    <td class="py-2 pl-4 text-gray-600 text-xs">${i + 1}</td>
                                    <td class="py-2 font-mono text-orange-400/80 font-semibold text-xs whitespace-nowrap">${escapeHtml(item.nup)}</td>
                                    <td class="py-2 pr-4 text-gray-300 text-xs">${escapeHtml(item.judul.length > 65 ? item.judul.substring(0, 65) + '…' : item.judul)}</td>
                                    <td class="py-2 text-blue-400/60 text-xs font-mono whitespace-nowrap">${escapeHtml(item.kodifikasi)}</td>
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
                <button onclick="scrollToSection('${id}')" class="glass glass-hover gradient-border rounded-2xl p-6 text-center transition-all duration-500 hover:scale-105 hover:-translate-y-2 fade-up glow-pulse" style="animation-delay:${delay}ms; box-shadow: 0 4px 30px ${color}15;">
                    <div class="text-4xl sm:text-5xl font-black mb-2" style="color:${color}; text-shadow: 0 0 30px ${color}40;">${count.toLocaleString()}</div>
                    <div class="text-[11px] text-gray-400 font-semibold uppercase tracking-widest">${icon} ${label}</div>
                </button>`;
        }

        function statCardTotal(count, delay) {
            return `
                <div class="glass rounded-2xl p-6 text-center fade-up glow-pulse" style="animation-delay:${delay}ms; box-shadow: 0 4px 30px rgba(249,115,22,0.15);">
                    <div class="text-4xl sm:text-5xl font-black mb-2 bg-gradient-to-r from-orange-300 via-amber-400 to-yellow-300 bg-clip-text text-transparent" style="filter: drop-shadow(0 0 20px rgba(249,115,22,0.3));">${count.toLocaleString()}</div>
                    <div class="text-[11px] text-gray-400 font-semibold uppercase tracking-widest">📊 Total Data</div>
                </div>`;
        }

        function categoryHTML(catId, icon, colorClass, title, count, groups, delay) {
            return `
                <div class="glass rounded-3xl overflow-hidden mb-8 fade-up glow-pulse" style="animation-delay:${delay}ms" id="sec-${catId}">
                    <button onclick="toggleCategory('${catId}')" class="w-full flex items-center justify-between px-8 py-6 hover:bg-orange-500/5 transition-all duration-300 text-left group">
                        <div class="flex items-center gap-5">
                            <span class="text-3xl transform group-hover:scale-110 transition-transform">${icon}</span>
                            <div>
                                <h3 class="text-lg font-bold text-white tracking-tight">${title}</h3>
                                <p class="text-xs text-gray-500 mt-1">${count.toLocaleString()} item</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-4">
                            <span class="text-sm font-bold px-4 py-2 rounded-xl ${colorClass}">${count.toLocaleString()}</span>
                            <svg class="w-6 h-6 text-gray-500 chevron transition-transform duration-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
                        </div>
                    </button>
                    <div id="${catId}-body" class="hidden px-8 pb-8">
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


# ── Routes ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    return SENsus_HTML


@app.get("/sensus", response_class=HTMLResponse)
async def sensus_page():
    return SENsus_HTML


@app.get("/api/sensus")
async def get_sensus_data():
    csv_items = read_csv_local()
    sheet_items = await read_google_sheet()
    inventory_items = read_inventory()
    return compare_data(csv_items, sheet_items, inventory_items)


@app.get("/api/profile")
async def get_profile():
    return {"name": "PLUVIO", "description": "Sensus BMN Dashboard"}
