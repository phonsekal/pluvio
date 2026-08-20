from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import csv
import io
import httpx
import os
from pathlib import Path
from collections import defaultdict

app = FastAPI(title="PLUVIO - Profil & Sensus BMN")

# Profile data
PROFILE = {
    "name": "Hapsari Wirastuti S",
    "title": "Sekretaris Menteri",
    "email": "hap.hap@hapsari.com",
    "bio": "Profesional di bidang administrasi dan manajemen perkantoran dengan pengalaman luas dalam mendukung operasional kementerian. Terampil dalam koordinasi, pengelolaan dokumen, dan komunikasi strategis.",
    "skills": [
        "Manajemen Perkantoran",
        "Koordinasi Acara & Rapat",
        "Pengelolaan Dokumen Resmi",
        "Komunikasi & Diplomasi",
        "Public Speaking",
        "Time Management"
    ],
    "experience": [
        {
            "role": "Sekretaris Menteri",
            "org": "Kementerian",
            "period": "2020 - Sekarang",
            "desc": "Mengelola operasional sekretariat, mengkoordinasi rapat tingkat tinggi, serta memastikan kelancaran komunikasi internal dan eksternal kementerian."
        },
        {
            "role": "Staff Administrasi",
            "org": "Kementerian",
            "period": "2015 - 2020",
            "desc": "Mendukung kegiatan administrasi harian, pengarsipan dokumen resmi, dan penyusunan laporan periodik."
        }
    ],
    "education": [
        {
            "degree": "S.Hum - Administrasi Negara",
            "school": "Universitas Indonesia",
            "year": "2014"
        }
    ]
}

GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1CtWwWaMNW8lhkAHsCUhSNHBZxGAWlTrqDABzRM4_wkk/export?format=csv&gid=0"
CSV_PATH = Path(__file__).parent / "databmnbuku.csv"

_csv_cache = None

def read_csv_local():
    """Read databmnbuku.csv with caching"""
    global _csv_cache
    if _csv_cache is not None:
        return _csv_cache
    items = []
    try:
        with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                nup = row.get("NUP", "").strip()
                judul = row.get("Nama Barang", "").strip()
                kodifikasi = row.get("Kode1", "").strip()
                if nup:
                    items.append({
                        "nup": nup,
                        "judul": judul,
                        "kodifikasi": kodifikasi
                    })
    except Exception as e:
        print(f"Error reading CSV: {e}")
    _csv_cache = items
    return items

_sheet_cache = None

async def read_google_sheet():
    """Read Google Sheet CSV with caching"""
    global _sheet_cache
    if _sheet_cache is not None:
        return _sheet_cache
    items = []
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(GOOGLE_SHEET_URL)
            resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            nup = row.get("NUP", "").strip()
            judul = row.get("Judul", "").strip()
            catatan = row.get("Catatan", "").strip()
            status_val = row.get("Status", "").strip()
            if nup:
                items.append({
                    "nup": nup,
                    "judul": judul,
                    "catatan": catatan,
                    "status_ditemukan": status_val == "Ditemukan"
                })
    except Exception as e:
        print(f"Error fetching Google Sheet: {e}")
        items = []
    _sheet_cache = items
    return items

def get_kodifikasi_group(kodifikasi: str) -> str:
    """Extract first letter of kodifikasi for grouping"""
    if not kodifikasi or kodifikasi == "-":
        return "Tanpa Kodifikasi"
    for char in kodifikasi:
        if char.isalpha():
            return char.upper()
    return "Lainnya"

def compare_data(csv_items, sheet_items):
    """Compare CSV with Google Sheet"""
    sheet_lookup = {}
    for item in sheet_items:
        sheet_lookup[item["nup"]] = item
    
    sensus_ditemukan = []
    sensus_belum_ditemukan = []
    di_sheet_tanpa_sensus = []
    belum_sensus = []
    
    for csv_item in csv_items:
        nup = csv_item["nup"]
        if nup in sheet_lookup:
            sheet_item = sheet_lookup[nup]
            if sheet_item["catatan"] == "sensus" and sheet_item["status_ditemukan"]:
                sensus_ditemukan.append({
                    "nup": nup,
                    "judul": csv_item["judul"],
                    "kodifikasi": csv_item["kodifikasi"],
                    "judul_sheet": sheet_item["judul"]
                })
            elif sheet_item["catatan"] == "sensus" and not sheet_item["status_ditemukan"]:
                sensus_belum_ditemukan.append({
                    "nup": nup,
                    "judul": csv_item["judul"],
                    "kodifikasi": csv_item["kodifikasi"],
                    "judul_sheet": sheet_item["judul"]
                })
            else:
                di_sheet_tanpa_sensus.append({
                    "nup": nup,
                    "judul": csv_item["judul"],
                    "kodifikasi": csv_item["kodifikasi"],
                    "judul_sheet": sheet_item["judul"]
                })
        else:
            belum_sensus.append({
                "nup": nup,
                "judul": csv_item["judul"],
                "kodifikasi": csv_item["kodifikasi"]
            })
    
    return {
        "sensus_ditemukan": sensus_ditemukan,
        "sensus_belum_ditemukan": sensus_belum_ditemukan,
        "di_sheet_tanpa_sensus": di_sheet_tanpa_sensus,
        "belum_sensus": belum_sensus
    }

def group_by_kodifikasi(items):
    """Group items by first letter of kodifikasi"""
    groups = defaultdict(list)
    for item in items:
        group = get_kodifikasi_group(item["kodifikasi"])
        groups[group].append(item)
    return dict(sorted(groups.items()))


# ========== PROFILE PAGE ==========

@app.get("/", response_class=HTMLResponse)
async def profile():
    skills_html = "".join(f'<span class="skill-tag">{s}</span>' for s in PROFILE["skills"])
    
    experience_html = "".join(f'''
        <div class="timeline-item">
            <div class="timeline-dot"></div>
            <div class="timeline-content">
                <h3>{e["role"]}</h3>
                <p class="org">{e["org"]}</p>
                <p class="period">{e["period"]}</p>
                <p class="desc">{e["desc"]}</p>
            </div>
        </div>''' for e in PROFILE["experience"])

    education_html = "".join(f'''
        <div class="timeline-item">
            <div class="timeline-dot"></div>
            <div class="timeline-content">
                <h3>{ed["degree"]}</h3>
                <p class="org">{ed["school"]}</p>
                <p class="period">Lulus {ed["year"]}</p>
            </div>
        </div>''' for ed in PROFILE["education"])

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{PROFILE["name"]} - Profile</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', sans-serif;
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            color: #e0e0e0; min-height: 100vh;
        }}
        .nav {{ 
            display: flex; justify-content: center; gap: 16px; 
            padding: 20px; background: rgba(0,0,0,0.3);
            position: sticky; top: 0; z-index: 100;
            backdrop-filter: blur(10px);
        }}
        .nav a {{
            padding: 10px 24px; border-radius: 10px;
            text-decoration: none; color: #a78bfa; font-weight: 500;
            transition: all 0.3s;
        }}
        .nav a:hover, .nav a.active {{ background: rgba(167, 139, 250, 0.2); color: #fff; }}
        .container {{ max-width: 800px; margin: 0 auto; padding: 40px 20px; }}
        .profile-card {{
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 20px;
            padding: 40px; margin-bottom: 30px; text-align: center;
        }}
        .avatar {{
            width: 120px; height: 120px; border-radius: 50%;
            background: linear-gradient(135deg, #667eea, #764ba2);
            display: flex; align-items: center; justify-content: center;
            margin: 0 auto 20px; font-size: 48px; font-weight: 700; color: white;
        }}
        .name {{ font-size: 28px; font-weight: 700; color: #fff; margin-bottom: 8px; }}
        .title {{ font-size: 16px; color: #a78bfa; font-weight: 500; margin-bottom: 16px; }}
        .bio {{ font-size: 14px; color: #9ca3af; line-height: 1.7; max-width: 600px; margin: 0 auto; }}
        .contact-link {{
            display: inline-flex; align-items: center; gap: 8px;
            margin-top: 20px; padding: 10px 20px;
            background: rgba(167, 139, 250, 0.15);
            border: 1px solid rgba(167, 139, 250, 0.3);
            border-radius: 10px; color: #a78bfa;
            text-decoration: none; font-size: 14px; font-weight: 500;
            transition: all 0.3s;
        }}
        .contact-link:hover {{ background: rgba(167, 139, 250, 0.25); }}
        .section {{
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 20px; padding: 30px 40px; margin-bottom: 30px;
        }}
        .section-title {{
            font-size: 20px; font-weight: 600; color: #fff;
            margin-bottom: 20px; display: flex; align-items: center; gap: 10px;
        }}
        .skills-grid {{ display: flex; flex-wrap: wrap; gap: 10px; }}
        .skill-tag {{
            padding: 8px 16px;
            background: rgba(167, 139, 250, 0.12);
            border: 1px solid rgba(167, 139, 250, 0.25);
            border-radius: 20px; font-size: 13px; font-weight: 500; color: #c4b5fd;
        }}
        .timeline {{ position: relative; padding-left: 30px; }}
        .timeline::before {{
            content: ''; position: absolute; left: 8px; top: 0;
            width: 2px; height: 100%; background: rgba(167, 139, 250, 0.3);
        }}
        .timeline-item {{ position: relative; margin-bottom: 24px; }}
        .timeline-item:last-child {{ margin-bottom: 0; }}
        .timeline-dot {{
            position: absolute; left: -26px; top: 6px;
            width: 12px; height: 12px; border-radius: 50%;
            background: #a78bfa; border: 2px solid #302b63;
        }}
        .timeline-content h3 {{ font-size: 16px; font-weight: 600; color: #fff; }}
        .timeline-content .org {{ font-size: 14px; color: #a78bfa; margin: 4px 0; }}
        .timeline-content .period {{ font-size: 12px; color: #6b7280; margin-bottom: 6px; }}
        .timeline-content .desc {{ font-size: 13px; color: #9ca3af; line-height: 1.6; }}
        .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #4b5563; }}
        @media (max-width: 600px) {{
            .profile-card, .section {{ padding: 24px 20px; }}
            .name {{ font-size: 22px; }}
        }}
    </style>
</head>
<body>
    <div class="nav">
        <a href="/" class="active">👤 Profil</a>
        <a href="/sensus">📊 Sensus BMN</a>
    </div>
    <div class="container">
        <div class="profile-card">
            <div class="avatar">{PROFILE["name"][0]}</div>
            <div class="name">{PROFILE["name"]}</div>
            <div class="title">{PROFILE["title"]}</div>
            <div class="bio">{PROFILE["bio"]}</div>
            <a href="mailto:{PROFILE["email"]}" class="contact-link">
                ✉️ {PROFILE["email"]}
            </a>
        </div>
        <div class="section">
            <div class="section-title">💡 Keahlian</div>
            <div class="skills-grid">{skills_html}</div>
        </div>
        <div class="section">
            <div class="section-title">💼 Pengalaman Kerja</div>
            <div class="timeline">{experience_html}</div>
        </div>
        <div class="section">
            <div class="section-title">🎓 Pendidikan</div>
            <div class="timeline">{education_html}</div>
        </div>
        <div class="footer">
            Built with FastAPI &amp; deployed on Vercel 🚀
        </div>
    </div>
</body>
</html>"""


# ========== SENSUS PAGE (Client-Side Rendering) ==========

SENsus_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sensus BMN - PLUVIO</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
            color: #e0e0e0; min-height: 100vh;
        }
        .nav {
            display: flex; justify-content: center; gap: 16px;
            padding: 20px; background: rgba(0,0,0,0.3);
            position: sticky; top: 0; z-index: 100;
            backdrop-filter: blur(10px);
        }
        .nav a {
            padding: 10px 24px; border-radius: 10px;
            text-decoration: none; color: #a78bfa; font-weight: 500;
            transition: all 0.3s;
        }
        .nav a:hover, .nav a.active { background: rgba(167, 139, 250, 0.2); color: #fff; }
        .container { max-width: 1000px; margin: 0 auto; padding: 40px 20px; }
        h1 { text-align: center; font-size: 28px; color: #fff; margin-bottom: 10px; }
        .subtitle { text-align: center; color: #9ca3af; margin-bottom: 30px; font-size: 14px; }
        .loading { text-align: center; padding: 60px; color: #a78bfa; font-size: 16px; }
        .loading .spinner {
            width: 40px; height: 40px; border: 3px solid rgba(167,139,250,0.2);
            border-top-color: #a78bfa; border-radius: 50%;
            animation: spin 1s linear infinite; margin: 0 auto 16px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .stats {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 16px; margin-bottom: 30px;
        }
        .stat-card {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 14px; padding: 20px; text-align: center;
        }
        .stat-card .num { font-size: 32px; font-weight: 700; }
        .stat-card .label { font-size: 12px; color: #9ca3af; margin-top: 4px; }
        .category {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 20px; padding: 24px; margin-bottom: 24px;
        }
        .category-header {
            display: flex; align-items: center; justify-content: space-between;
            cursor: pointer; padding: 10px 0;
        }
        .category-title {
            font-size: 18px; font-weight: 600; color: #fff;
            display: flex; align-items: center; gap: 10px;
        }
        .category-badge {
            padding: 4px 12px; border-radius: 20px;
            font-size: 13px; font-weight: 600;
        }
        .badge-green { background: rgba(74, 222, 128, 0.2); color: #4ade80; }
        .badge-yellow { background: rgba(250, 204, 21, 0.2); color: #facc15; }
        .badge-orange { background: rgba(251, 146, 60, 0.2); color: #fb923c; }
        .badge-red { background: rgba(248, 113, 113, 0.2); color: #f87171; }
        .kodif-group { margin-bottom: 16px; }
        .kodif-header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 10px 14px;
            background: rgba(255,255,255,0.04);
            border-radius: 10px; cursor: pointer;
            transition: background 0.2s;
        }
        .kodif-header:hover { background: rgba(255,255,255,0.08); }
        .kodif-label { font-weight: 600; font-size: 14px; color: #c4b5fd; }
        .kodif-count {
            font-size: 12px; color: #6b7280;
            background: rgba(255,255,255,0.08);
            padding: 2px 10px; border-radius: 12px;
        }
        .kodif-list { margin-top: 8px; overflow-x: auto; }
        .kodif-list table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .kodif-list th {
            text-align: left; padding: 8px 12px;
            color: #9ca3af; border-bottom: 1px solid rgba(255,255,255,0.1);
            font-weight: 500; font-size: 12px;
        }
        .kodif-list td {
            padding: 8px 12px;
            border-bottom: 1px solid rgba(255,255,255,0.04);
            color: #d1d5db;
        }
        .kodif-list td.nup { color: #a78bfa; font-weight: 600; white-space: nowrap; }
        .kodif-list td.kodif { color: #60a5fa; font-size: 12px; white-space: nowrap; }
        .kodif-group.collapsed .kodif-list { display: none; }
        .search-box {
            width: 100%; padding: 12px 20px; border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.15);
            background: rgba(255,255,255,0.05); color: #fff;
            font-size: 14px; margin-bottom: 24px; outline: none;
            font-family: 'Inter', sans-serif;
        }
        .search-box::placeholder { color: #6b7280; }
        .search-box:focus { border-color: rgba(167, 139, 250, 0.5); }
        .footer { text-align: center; padding: 20px; font-size: 12px; color: #4b5563; }
        .error { text-align: center; padding: 40px; color: #f87171; }
        @media (max-width: 600px) {
            .stats { grid-template-columns: 1fr 1fr; }
            .category { padding: 16px; }
        }
    </style>
</head>
<body>
    <div class="nav">
        <a href="/">👤 Profil</a>
        <a href="/sensus" class="active">📊 Sensus BMN</a>
    </div>
    <div class="container">
        <h1>📊 Sensus BMN</h1>
        <p class="subtitle">Perbandingan data CSV (databmnbuku.csv) dengan Google Sheet sensus</p>
        <div id="stats"></div>
        <div id="content">
            <div class="loading">
                <div class="spinner"></div>
                Memuat data sensus...
            </div>
        </div>
        <div class="footer">Built with FastAPI &amp; deployed on Vercel 🚀</div>
    </div>
    <script>
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
            return Object.keys(g).sort().map(k => ({name: k, items: g[k]}));
        }
        function renderGroups(groups) {
            if (groups.length === 0) return '<p style="color:#6b7280;padding:10px;">Tidak ada data</p>';
            return groups.map(g => `
                <div class="kodif-group collapsed">
                    <div class="kodif-header" onclick="this.parentElement.classList.toggle('collapsed')">
                        <span class="kodif-label">📁 ${g.name}</span>
                        <span class="kodif-count">${g.items.length} item</span>
                    </div>
                    <div class="kodif-list">
                        <table>
                            <thead><tr><th>No</th><th>NUP</th><th>Judul</th><th>Kodifikasi</th></tr></thead>
                            <tbody>${g.items.map((item, i) => `
                                <tr>
                                    <td>${i+1}</td>
                                    <td class="nup">${item.nup}</td>
                                    <td>${item.judul.length > 60 ? item.judul.substring(0,60)+'...' : item.judul}</td>
                                    <td class="kodif">${item.kodifikasi}</td>
                                </tr>`).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            `).join('');
        }
        function renderCategory(icon, badgeClass, title, count, groups) {
            return `
                <div class="category">
                    <div class="category-header" onclick="this.parentElement.querySelector('.category-body').style.display = this.parentElement.querySelector('.category-body').style.display === 'none' ? 'block' : 'none'">
                        <div class="category-title">
                            <span class="category-badge ${badgeClass}">${icon}</span>
                            ${title}
                            <span class="category-badge ${badgeClass}">${count}</span>
                        </div>
                    </div>
                    <div class="category-body">${renderGroups(groups)}</div>
                </div>`;
        }
        function filterAll(q) {
            q = q.toLowerCase();
            document.querySelectorAll('.kodif-list tr').forEach(row => {
                if (row.parentElement.tagName === 'THEAD') return;
                row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
            });
            if (q.length > 0) {
                document.querySelectorAll('.kodif-group.collapsed').forEach(g => g.classList.remove('collapsed'));
                document.querySelectorAll('.category-body').forEach(b => b.style.display = 'block');
            }
        }
        async function loadData() {
            try {
                const resp = await fetch('/api/sensus');
                if (!resp.ok) throw new Error('API error');
                const data = await resp.json();
                const sd = data.sensus_ditemukan || [];
                const sbd = data.sensus_belum_ditemukan || [];
                const ds = data.di_sheet_tanpa_sensus || [];
                const bs = data.belum_sensus || [];
                document.getElementById('stats').innerHTML = `
                    <div class="stats">
                        <div class="stat-card"><div class="num" style="color:#60a5fa">${sd.length+sbd.length+ds.length+bs.length}</div><div class="label">Total CSV</div></div>
                        <div class="stat-card"><div class="num" style="color:#4ade80">${sd.length}</div><div class="label">Sensus & Ditemukan</div></div>
                        <div class="stat-card"><div class="num" style="color:#facc15">${sbd.length}</div><div class="label">Sensus, Belum Ditemukan</div></div>
                        <div class="stat-card"><div class="num" style="color:#fb923c">${ds.length}</div><div class="label">Di Sheet, Tanpa Sensus</div></div>
                        <div class="stat-card"><div class="num" style="color:#f87171">${bs.length}</div><div class="label">Belum di Google Sheet</div></div>
                    </div>
                    <input type="text" class="search-box" placeholder="🔍 Cari NUP atau Judul..." oninput="filterAll(this.value)">
                    ${renderCategory('✅','badge-green','Sensus & Ditemukan',sd.length,groupByKodifikasi(sd))}
                    ${renderCategory('⚠️','badge-yellow','Sensus, Belum Ditemukan',sbd.length,groupByKodifikasi(sbd))}
                    ${renderCategory('📋','badge-orange','Di Sheet, Tanpa Status Sensus',ds.length,groupByKodifikasi(ds))}
                    ${renderCategory('❌','badge-red','Belum di Google Sheet',bs.length,groupByKodifikasi(bs))}
                `;
            } catch(e) {
                document.getElementById('content').innerHTML = `<div class="error">❌ Gagal memuat data: ${e.message}</div>`;
            }
        }
        loadData();
    </script>
</body>
</html>"""

@app.get("/sensus", response_class=HTMLResponse)
async def sensus_page():
    return SENsus_HTML


# ========== API ==========

@app.get("/api/profile")
async def get_profile():
    return PROFILE

@app.get("/api/sensus")
async def get_sensus_data():
    csv_items = read_csv_local()
    sheet_items = await read_google_sheet()
    return compare_data(csv_items, sheet_items)
