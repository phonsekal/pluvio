from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Profile - Hapsari Wirastuti S")

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
            color: #e0e0e0;
            min-height: 100vh;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            padding: 40px 20px;
        }}
        .profile-card {{
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 20px;
            padding: 40px;
            margin-bottom: 30px;
            text-align: center;
        }}
        .avatar {{
            width: 120px; height: 120px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea, #764ba2);
            display: flex; align-items: center; justify-content: center;
            margin: 0 auto 20px;
            font-size: 48px; font-weight: 700; color: white;
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
            border-radius: 20px;
            padding: 30px 40px;
            margin-bottom: 30px;
        }}
        .section-title {{
            font-size: 20px; font-weight: 600; color: #fff;
            margin-bottom: 20px;
            display: flex; align-items: center; gap: 10px;
        }}
        .section-title .icon {{ font-size: 22px; }}
        .skills-grid {{
            display: flex; flex-wrap: wrap; gap: 10px;
        }}
        .skill-tag {{
            padding: 8px 16px;
            background: rgba(167, 139, 250, 0.12);
            border: 1px solid rgba(167, 139, 250, 0.25);
            border-radius: 20px;
            font-size: 13px; font-weight: 500;
            color: #c4b5fd;
        }}
        .timeline {{ position: relative; padding-left: 30px; }}
        .timeline::before {{
            content: ''; position: absolute; left: 8px; top: 0;
            width: 2px; height: 100%;
            background: rgba(167, 139, 250, 0.3);
        }}
        .timeline-item {{ position: relative; margin-bottom: 24px; }}
        .timeline-item:last-child {{ margin-bottom: 0; }}
        .timeline-dot {{
            position: absolute; left: -26px; top: 6px;
            width: 12px; height: 12px; border-radius: 50%;
            background: #a78bfa;
            border: 2px solid #302b63;
        }}
        .timeline-content h3 {{ font-size: 16px; font-weight: 600; color: #fff; }}
        .timeline-content .org {{ font-size: 14px; color: #a78bfa; margin: 4px 0; }}
        .timeline-content .period {{ font-size: 12px; color: #6b7280; margin-bottom: 6px; }}
        .timeline-content .desc {{ font-size: 13px; color: #9ca3af; line-height: 1.6; }}
        .footer {{
            text-align: center; padding: 20px;
            font-size: 12px; color: #4b5563;
        }}
        @media (max-width: 600px) {{
            .profile-card, .section {{ padding: 24px 20px; }}
            .name {{ font-size: 22px; }}
        }}
    </style>
</head>
<body>
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
            <div class="section-title"><span class="icon">💡</span> Keahlian</div>
            <div class="skills-grid">{skills_html}</div>
        </div>

        <div class="section">
            <div class="section-title"><span class="icon">💼</span> Pengalaman Kerja</div>
            <div class="timeline">{experience_html}</div>
        </div>

        <div class="section">
            <div class="section-title"><span class="icon">🎓</span> Pendidikan</div>
            <div class="timeline">{education_html}</div>
        </div>

        <div class="footer">
            Built with FastAPI &amp; deployed on Vercel 🚀
        </div>
    </div>
</body>
</html>"""


@app.get("/api/profile")
async def get_profile():
    return PROFILE
