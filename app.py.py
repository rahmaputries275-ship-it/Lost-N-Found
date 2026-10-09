# app.py - LOST & FOUND
# Sistem Informasi Barang Hilang & Temuan — Universitas Jember
# Created by: Algopro Pascal
# Versi: Public Access Edition

from flask import (Flask, request, redirect, url_for, flash,
                   render_template_string, send_from_directory, session)
import sqlite3
import os
import uuid
import re
import socket
import threading
import time
import subprocess
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'rahasia_lostfound_2025_algopro_pascal_super_secret'
app.permanent_session_lifetime = timedelta(days=30)

# ========== KONFIGURASI ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'lostfound.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
MAX_CONTENT_LENGTH = 5 * 1024 * 1024
ADMIN_PASSWORD = "admin2025"

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# URL publik — diisi otomatis oleh script (lihat bagian bawah)
PUBLIC_URL = None


# ========== DATABASE ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS barang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            jenis TEXT NOT NULL,
            nama TEXT NOT NULL,
            kategori TEXT NOT NULL,
            ciri TEXT NOT NULL,
            lokasi TEXT NOT NULL,
            tanggal TEXT NOT NULL,
            foto TEXT,
            keterangan TEXT,
            status TEXT NOT NULL DEFAULT 'belum',
            pelapor TEXT,
            kontak TEXT,
            linked_id INTEGER,
            notif TEXT,
            owner_token TEXT,
            claimed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute("PRAGMA table_info(barang)")
    cols = [r[1] for r in cursor.fetchall()]
    if 'linked_id' not in cols:
        cursor.execute("ALTER TABLE barang ADD COLUMN linked_id INTEGER")
    if 'notif' not in cols:
        cursor.execute("ALTER TABLE barang ADD COLUMN notif TEXT")
    if 'claimed_at' not in cols:
        cursor.execute("ALTER TABLE barang ADD COLUMN claimed_at TIMESTAMP")
    if 'owner_token' not in cols:
        cursor.execute("ALTER TABLE barang ADD COLUMN owner_token TEXT")
    conn.commit()
    conn.close()


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_file(file):
    if not file or file.filename == '': return None
    if not allowed_file(file.filename): return None
    ext = file.filename.rsplit('.', 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    file.save(filepath)
    return unique_name


# ========== OWNER TOKEN ==========
def get_owner_tokens():
    return session.get('owner_tokens', [])

def add_owner_token(token):
    tokens = session.get('owner_tokens', [])
    if token not in tokens:
        tokens.append(token)
    session['owner_tokens'] = tokens
    session.permanent = True
    session.modified = True

def is_owner(barang):
    if not barang: return False
    ot = barang['owner_token'] if 'owner_token' in barang.keys() else None
    if not ot: return False
    return ot in get_owner_tokens()

def is_admin():
    return session.get('is_admin', False)

def can_edit(barang):
    return is_owner(barang) or is_admin()


# ========== SMART MATCHING ==========
def normalize_text(s):
    if not s: return set()
    s = s.lower()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    stopwords = {'yang','dan','di','ke','dari','untuk','dengan','pada','itu','ini',
                 'ada','barang','warna','sebuah','atau','juga','saya','milik','punya','nya'}
    return {w for w in s.split() if len(w) > 2 and w not in stopwords}

def similarity_score(text_a, text_b):
    a = normalize_text(text_a); b = normalize_text(text_b)
    if not a or not b: return 0.0
    inter = a & b; union = a | b
    return len(inter) / len(union) if union else 0.0

def find_candidates(new_item_id):
    conn = get_db_connection()
    new_item = conn.execute("SELECT * FROM barang WHERE id=?", (new_item_id,)).fetchone()
    if not new_item:
        conn.close(); return []
    lawan = 'hilang' if new_item['jenis'] == 'ditemukan' else 'ditemukan'
    rows = conn.execute("SELECT * FROM barang WHERE jenis=? AND id != ?", (lawan, new_item_id)).fetchall()
    conn.close()
    candidates = []
    for r in rows:
        nS = similarity_score(new_item['nama'], r['nama'])
        cS = similarity_score(new_item['ciri'], r['ciri'])
        kM = 1.0 if new_item['kategori'] == r['kategori'] else 0.0
        lS = similarity_score(new_item['lokasi'], r['lokasi'])
        total = (nS * 0.5) + (kM * 0.2) + (cS * 0.2) + (lS * 0.1)
        candidates.append({'item': r, 'score': round(total, 3)})
    candidates.sort(key=lambda x: x['score'], reverse=True)
    return candidates

def auto_update_status(new_item_id):
    candidates = find_candidates(new_item_id)
    if not candidates: return None
    conn = get_db_connection()
    new_item = conn.execute("SELECT * FROM barang WHERE id=?", (new_item_id,)).fetchone()
    best = candidates[0]
    matched = None
    if best['score'] >= 0.40:
        matched = best['item']
    if matched:
        conn.execute("UPDATE barang SET status='proses', linked_id=?, notif=? WHERE id=?",
                     (matched['id'],
                      f"Otomatis dikaitkan dengan laporan #{matched['id']} ({matched['nama']}). Menunggu konfirmasi pemilik.",
                      new_item_id))
        conn.execute("UPDATE barang SET status='proses', linked_id=?, notif=? WHERE id=?",
                     (new_item_id,
                      f"Otomatis dikaitkan dengan laporan #{new_item_id} ({new_item['nama']}). Menunggu konfirmasi pemilik.",
                      matched['id']))
        conn.commit(); conn.close()
        return {'matched_id': matched['id'], 'matched_nama': matched['nama'], 'score': best['score']}
    conn.close(); return None

def manual_match(id_a, id_b):
    conn = get_db_connection()
    a = conn.execute("SELECT * FROM barang WHERE id=?", (id_a,)).fetchone()
    b = conn.execute("SELECT * FROM barang WHERE id=?", (id_b,)).fetchone()
    if not a or not b:
        conn.close(); return False
    conn.execute("UPDATE barang SET status='proses', linked_id=?, notif=? WHERE id=?",
                 (b['id'], f"Dikonfirmasi terkait dengan #{b['id']} ({b['nama']}). Menunggu konfirmasi pemilik.", a['id']))
    conn.execute("UPDATE barang SET status='proses', linked_id=?, notif=? WHERE id=?",
                 (a['id'], f"Dikonfirmasi terkait dengan #{a['id']} ({a['nama']}). Menunggu konfirmasi pemilik.", b['id']))
    conn.commit(); conn.close()
    return True

def mark_as_claimed(hilang_id):
    conn = get_db_connection()
    hilang = conn.execute("SELECT * FROM barang WHERE id=?", (hilang_id,)).fetchone()
    if not hilang or hilang['jenis'] != 'hilang':
        conn.close(); return None
    if not hilang['linked_id']:
        conn.close(); return {'error': 'Laporan belum terhubung dengan laporan temuan.'}
    temuan = conn.execute("SELECT * FROM barang WHERE id=?", (hilang['linked_id'],)).fetchone()
    if not temuan:
        conn.close(); return {'error': 'Laporan temuan tidak ditemukan.'}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("UPDATE barang SET status='selesai', claimed_at=?, notif=? WHERE id=?",
                 (now, "Pemilik telah konfirmasi barang sudah diterima. Status otomatis SELESAI.", hilang_id))
    conn.execute("UPDATE barang SET status='selesai', claimed_at=?, notif=? WHERE id=?",
                 (now, f"Pemilik barang hilang (#{hilang_id}) telah konfirmasi. Status otomatis SELESAI.", temuan['id']))
    conn.commit(); conn.close()
    return {'hilang_id': hilang_id, 'temuan_id': temuan['id'], 'temuan_nama': temuan['nama']}


# ========== CSS (RINGKAS) ==========
GLOBAL_CSS = '''
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,700;0,9..144,900;1,9..144,600;1,9..144,700;1,9..144,900&display=swap');
    :root { --wine:#A8467A; --wine-deep:#7A2B54; --wine-dark:#5C1F3F;
        --plum:#7B5BB8; --plum-deep:#5A3D94; --burnt:#E07A45; --burnt-deep:#B85326;
        --forest:#3F8F78; --forest-deep:#2A6B58; --gold:#D4AF37; --gold-soft:#E8C87A;
        --gold-pale:#F5E4B8; --ink:#1a1428; --ink-soft:#4a4158; --ink-mute:#8a8299; --ink-light:#c4bed0; }
    * { margin:0; padding:0; box-sizing:border-box; }
    body { font-family:'Outfit',system-ui,sans-serif; background:#F0E8F0;
        min-height:100vh; padding:40px 20px; color:var(--ink); overflow-x:hidden; }
    body::before { content:''; position:fixed; inset:0;
        background:
            radial-gradient(ellipse 800px 700px at 12% 8%, rgba(168,70,122,0.35), transparent 50%),
            radial-gradient(ellipse 700px 600px at 88% 15%, rgba(123,91,184,0.32), transparent 50%),
            radial-gradient(ellipse 800px 700px at 85% 92%, rgba(63,143,120,0.28), transparent 50%),
            radial-gradient(ellipse 700px 600px at 8% 88%, rgba(224,122,69,0.30), transparent 50%),
            linear-gradient(180deg, #FBF6EE 0%, #F5EAEE 100%);
        z-index:-3; }
    .frame-wrapper { position:relative; max-width:1300px; margin:0 auto; padding:3px;
        border-radius:32px; background:linear-gradient(135deg, var(--wine) 0%, var(--plum) 30%, var(--burnt) 55%, var(--forest) 80%, var(--gold) 100%);
        background-size:300% 300%; animation:borderFlow 12s ease infinite;
        box-shadow:0 25px 60px rgba(92,31,63,0.22); }
    @keyframes borderFlow { 0%,100%{background-position:0% 50%;} 50%{background-position:100% 50%;} }
    .container { background:#FFF; border-radius:29px; overflow:hidden; }
    .top-bar { position:absolute; top:20px; left:20px; z-index:10; display:flex; gap:8px; flex-wrap:wrap; }
    .btn-home, .btn-share {
        display:inline-flex; align-items:center; gap:8px; padding:10px 20px;
        background:rgba(255,255,255,0.95); border:1.5px solid rgba(168,70,122,0.25);
        border-radius:100px; color:var(--wine-deep); text-decoration:none;
        font-size:0.82em; font-weight:700; cursor:pointer; font-family:inherit; }
    .btn-home:hover, .btn-share:hover { background:var(--wine); color:#fff; }
    .admin-bar { position:absolute; top:20px; right:20px; z-index:10; display:flex; gap:8px; }
    .admin-badge { padding:8px 16px; background:linear-gradient(135deg, var(--gold), var(--gold-soft));
        color:var(--wine-dark); border-radius:100px; font-size:0.72em; font-weight:800;
        text-transform:uppercase; }
    .btn-admin-logout { padding:8px 14px; background:rgba(168,70,122,0.12);
        color:var(--wine-deep); border-radius:100px; font-size:0.72em;
        font-weight:700; text-decoration:none; }
    .hero-header { padding:60px 40px 44px;
        background:linear-gradient(135deg, #FDF2F6 0%, #F5F0FA 50%, #EEF8F4 100%);
        text-align:center; position:relative; }
    .hero-content { position:relative; z-index:3; }
    .header-badge { display:inline-flex; align-items:center; gap:8px;
        padding:8px 20px; border-radius:100px;
        background:linear-gradient(135deg, rgba(168,70,122,0.15), rgba(123,91,184,0.15));
        color:var(--wine-deep); font-size:0.7em; font-weight:800;
        letter-spacing:2.5px; text-transform:uppercase; margin-bottom:22px; }
    .header-badge .dot-live { width:6px; height:6px; background:#22c55e; border-radius:50%; }
    .brand { font-family:'Fraunces',serif; font-size:4em; font-weight:900;
        letter-spacing:-2.5px; line-height:1; font-style:italic;
        background:linear-gradient(120deg, #7A2B54 0%, #A8467A 25%, #B85326 50%, #7B5BB8 75%, #3F8F78 100%);
        background-size:250% auto; -webkit-background-clip:text; -webkit-text-fill-color:transparent;
        animation:textShimmer 10s ease-in-out infinite; }
    @keyframes textShimmer { 0%,100%{background-position:0% center;} 50%{background-position:100% center;} }
    .divider { display:flex; align-items:center; justify-content:center;
        gap:12px; margin:22px auto 20px; max-width:260px; }
    .divider .line { flex:1; height:1.5px;
        background:linear-gradient(90deg, transparent, var(--gold), transparent); }
    .divider .diamond { color:var(--wine); font-size:0.85em; transform:rotate(45deg); }
    .subtitle { color:var(--ink-soft); font-size:1.05em; line-height:1.6; }
    .subtitle .uni { font-family:'Fraunces',serif; font-style:italic;
        font-weight:700; font-size:1.2em;
        background:linear-gradient(135deg, var(--wine-deep), var(--plum-deep));
        -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
    .main-content { padding:40px; }
    .alert { padding:15px 22px; margin-bottom:20px; border-radius:14px;
        text-align:center; font-weight:500; border-left:4px solid; }
    .success { background:#ECFDF5; color:#065f46; border-color:#10b981; }
    .error { background:#FEF2F2; color:#991b1b; border-color:#ef4444; }
    .info { background:#EFF6FF; color:#1e40af; border-color:#3b82f6; }
    .auto-msg { background:linear-gradient(135deg,#F5F0FA,#FDF2F6); color:var(--plum-deep); border-color:var(--plum); }

    .stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
        gap:18px; margin-bottom:34px; }
    .stat-card { padding:24px 20px; border-radius:20px; text-align:center;
        border:1.5px solid rgba(255,255,255,0.9); transition:all 0.4s; }
    .stat-card:nth-child(1) { background:linear-gradient(135deg, #FDF2F6 0%, #FAD4E4 100%); }
    .stat-card:nth-child(2) { background:linear-gradient(135deg, #FFF3EC 0%, #FFDCC4 100%); }
    .stat-card:nth-child(3) { background:linear-gradient(135deg, #EEF8F4 0%, #C8EAD9 100%); }
    .stat-card:nth-child(4) { background:linear-gradient(135deg, #F5F0FA 0%, #DDD2F0 100%); }
    .stat-card:hover { transform:translateY(-7px) scale(1.02); }
    .stat-icon { font-size:1.9em; margin-bottom:10px; }
    .stat-number { font-family:'Fraunces',serif; font-size:2.5em; font-weight:900;
        line-height:1; margin-bottom:8px; letter-spacing:-1.5px; }
    .stat-card:nth-child(1) .stat-number { color:var(--wine-deep); }
    .stat-card:nth-child(2) .stat-number { color:var(--burnt-deep); }
    .stat-card:nth-child(3) .stat-number { color:var(--forest-deep); }
    .stat-card:nth-child(4) .stat-number { color:var(--plum-deep); }
    .stat-label { color:var(--ink-soft); font-size:0.72em; font-weight:800;
        text-transform:uppercase; letter-spacing:1.4px; }

    .btn-group { display:flex; gap:16px; justify-content:center;
        margin-bottom:34px; flex-wrap:wrap; }
    .btn { padding:16px 34px; border:none; border-radius:100px; cursor:pointer;
        font-size:0.95em; font-weight:700; text-decoration:none;
        display:inline-flex; align-items:center; gap:10px; font-family:'Outfit',sans-serif; }
    .btn-hilang { background:linear-gradient(135deg, var(--wine), var(--wine-deep));
        color:#fff; box-shadow:0 10px 26px rgba(122,43,84,0.40); }
    .btn-temu { background:linear-gradient(135deg, var(--forest), var(--forest-deep));
        color:#fff; box-shadow:0 10px 26px rgba(42,107,88,0.40); }
    .btn-cari { background:linear-gradient(135deg, var(--plum), var(--plum-deep));
        color:#fff; padding:15px 34px; border:none; border-radius:100px;
        cursor:pointer; font-weight:700; width:100%; font-family:'Outfit',sans-serif; }

    .filter-box { background:linear-gradient(135deg, #FDF2F6 0%, #F5F0FA 100%);
        padding:26px; border-radius:22px; margin-bottom:34px;
        border:1.5px solid rgba(168,70,122,0.15); }
    .filter-row { display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));
        gap:12px; margin-bottom:16px; }
    .filter-row input, .filter-row select { padding:13px 16px;
        border:1.5px solid rgba(168,70,122,0.20); border-radius:12px;
        font-size:0.88em; font-family:'Outfit',sans-serif; background:#FFF; }

    .cards { display:grid; grid-template-columns:repeat(auto-fill, minmax(290px, 1fr));
        gap:24px; margin-top:22px; }
    .card { background:#FFF; border-radius:22px; overflow:hidden;
        border:1.5px solid #EDE4EC; display:flex; flex-direction:column;
        position:relative; transition:all 0.4s; }
    .card:hover { transform:translateY(-8px); border-color:var(--wine); }
    .card.owned { border-color:rgba(212,175,55,0.5); }
    .card.owned::before { content:'👑 LAPORAN SAYA'; position:absolute;
        top:14px; right:14px; z-index:5; padding:5px 12px;
        background:linear-gradient(135deg, var(--gold), var(--gold-soft));
        color:var(--wine-dark); font-size:0.62em; font-weight:800; border-radius:100px; }
    .card-img-wrap { width:100%; height:210px; overflow:hidden;
        background:linear-gradient(135deg, #F5F0FA, #FDF2F6); }
    .card-img { width:100%; height:100%; object-fit:cover; }
    .card-img-placeholder { width:100%; height:100%; display:flex;
        align-items:center; justify-content:center; font-size:3.6em; color:var(--plum); }
    .card-body { padding:18px 20px 20px; flex:1; display:flex; flex-direction:column; }
    .card-header-row { display:flex; justify-content:space-between;
        align-items:center; margin-bottom:12px; }
    .badge { padding:5px 14px; border-radius:100px; font-size:0.68em;
        font-weight:800; text-transform:uppercase; letter-spacing:1.1px; }
    .badge-hilang { background:linear-gradient(135deg, var(--wine), var(--wine-deep)); color:#fff; }
    .badge-temu { background:linear-gradient(135deg, var(--forest), var(--forest-deep)); color:#fff; }
    .card-id { font-size:0.7em; color:var(--ink-mute); font-weight:700;
        background:#F5F0FA; padding:3px 10px; border-radius:8px; }
    .card-title { font-family:'Fraunces',serif; font-size:1.18em; font-weight:700;
        margin-bottom:12px; }
    .card-info { font-size:0.82em; color:var(--ink-soft); margin-bottom:6px; font-weight:500; }
    .card-desc { font-size:0.83em; color:var(--ink-soft); margin:12px 0; flex:1;
        overflow:hidden; display:-webkit-box; -webkit-line-clamp:2;
        -webkit-box-orient:vertical; line-height:1.55; }
    .status-pill { display:inline-flex; align-items:center; gap:6px;
        padding:5px 14px; border-radius:100px; font-size:0.72em;
        font-weight:700; margin-top:8px; }
    .status-pill::before { content:''; width:6px; height:6px; border-radius:50%; }
    .status-belum { background:rgba(185,28,28,0.12); color:#991b1b; }
    .status-belum::before { background:#ef4444; }
    .status-proses { background:rgba(217,119,6,0.15); color:#92400e; }
    .status-proses::before { background:#f59e0b; }
    .status-selesai { background:rgba(16,185,129,0.15); color:#065f46; }
    .status-selesai::before { background:#10b981; }
    .card-notif { background:rgba(123,91,184,0.10); color:var(--plum-deep);
        padding:9px 13px; border-radius:10px; font-size:0.76em;
        margin-top:8px; border-left:3px solid var(--plum); }
    .linked-badge { display:inline-flex; align-items:center; gap:5px;
        background:rgba(123,91,184,0.15); color:var(--plum-deep);
        padding:4px 12px; border-radius:100px; font-size:0.7em;
        font-weight:700; margin-top:8px; }
    .card-actions { display:flex; gap:8px; margin-top:16px;
        padding-top:16px; border-top:1.5px dashed rgba(168,70,122,0.15); }
    .card-actions a, .card-actions span { flex:1; text-align:center;
        padding:10px; border-radius:11px; text-decoration:none;
        font-size:0.78em; font-weight:700;
        display:inline-flex; align-items:center; justify-content:center; }
    .btn-detail { background:rgba(123,91,184,0.12); color:var(--plum-deep); }
    .btn-edit { background:rgba(63,143,120,0.12); color:var(--forest-deep); }
    .btn-hapus { background:rgba(168,70,122,0.12); color:var(--wine-deep); flex:0.55 !important; }
    .btn-locked { background:rgba(140,140,160,0.10); color:#9a96a6; flex:0.55 !important; }

    .empty-state { text-align:center; padding:80px 20px; color:var(--ink-mute);
        background:linear-gradient(135deg, #FDF2F6, #F5F0FA);
        border-radius:22px; border:2px dashed rgba(168,70,122,0.30); }
    .empty-state .icon { font-size:5em; margin-bottom:18px; }

    footer { text-align:center; margin-top:45px; padding:32px 24px;
        background:linear-gradient(135deg, #3D2A5C 0%, #5C1F3F 100%);
        border-radius:22px; color:#E8DFF0; font-size:0.9em; }
    footer p { margin:6px 0; }
    .footer-credit { font-family:'Fraunces',serif; font-style:italic;
        font-weight:900; font-size:1.25em;
        background:linear-gradient(120deg, var(--gold-pale), var(--gold), #fff, var(--gold));
        background-size:250% auto; -webkit-background-clip:text;
        -webkit-text-fill-color:transparent; animation:textShimmer 6s ease-in-out infinite; }

    .form-group { margin-bottom:20px; }
    .form-group label { display:block; margin-bottom:8px; font-weight:600; font-size:0.86em; }
    input, select, textarea { width:100%; padding:13px 16px;
        border:1.5px solid rgba(168,70,122,0.20); border-radius:12px;
        font-size:0.9em; font-family:'Outfit',sans-serif; background:#FFF; }
    input:focus, select:focus, textarea:focus { outline:none;
        border-color:var(--wine); box-shadow:0 0 0 4px rgba(168,70,122,0.12); }
    textarea { resize:vertical; min-height:90px; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
    .button-group { display:flex; gap:12px; margin-top:26px; }
    .button-group button, .button-group .btn-cancel { flex:1; padding:15px;
        border:none; border-radius:100px; cursor:pointer; font-size:0.94em;
        font-weight:700; text-align:center; text-decoration:none;
        font-family:'Outfit',sans-serif; display:inline-flex;
        align-items:center; justify-content:center; }
    .button-group button { background:linear-gradient(135deg, var(--plum), var(--plum-deep));
        color:#fff; }
    .button-group .btn-cancel { background:#F5F0FA; color:var(--ink-soft); }

    .jenis-toggle { display:flex; gap:12px; }
    .jenis-toggle label { flex:1; padding:18px; border-radius:18px;
        border:2px solid rgba(168,70,122,0.15); cursor:pointer;
        text-align:center; font-weight:700; color:var(--ink-mute); background:#FFF; }
    .jenis-toggle input[type=radio] { display:none; }
    .jenis-toggle label:has(input[value=hilang]:checked) {
        background:linear-gradient(135deg, var(--wine), var(--wine-deep));
        border-color:var(--wine-deep); color:#fff; }
    .jenis-toggle label:has(input[value=ditemukan]:checked) {
        background:linear-gradient(135deg, var(--forest), var(--forest-deep));
        border-color:var(--forest-deep); color:#fff; }

    .preview-img { max-width:220px; margin-top:14px; border-radius:16px; border:3px solid #fff; }
    .hint { font-size:0.78em; color:var(--ink-mute); margin-top:8px; font-style:italic; }
    .badge-detail { display:inline-flex; padding:8px 20px; border-radius:100px;
        font-size:0.74em; font-weight:800; text-transform:uppercase;
        letter-spacing:1.5px; margin-bottom:18px; }
    .badge-detail.badge-hilang { background:linear-gradient(135deg, var(--wine), var(--wine-deep)); color:#fff; }
    .badge-detail.badge-temu { background:linear-gradient(135deg, var(--forest), var(--forest-deep)); color:#fff; }

    .photo-box { width:100%; max-height:500px; overflow:hidden;
        border-radius:24px; margin:24px 0;
        background:linear-gradient(135deg, #F5F0FA, #FDF2F6);
        display:flex; align-items:center; justify-content:center; border:4px solid #fff; }
    .photo-box img { width:100%; max-height:500px; object-fit:contain; }
    .photo-placeholder { padding:80px 20px; font-size:4.5em; color:var(--plum); }

    .info-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:22px 0; }
    .info-item { padding:20px 22px; border-radius:18px;
        border:1.5px solid rgba(168,70,122,0.12); }
    .info-item:nth-child(1) { background:linear-gradient(135deg, #FDF2F6, #FAD4E4); }
    .info-item:nth-child(2) { background:linear-gradient(135deg, #FFF3EC, #FFDCC4); }
    .info-item:nth-child(3) { background:linear-gradient(135deg, #EEF8F4, #C8EAD9); }
    .info-item:nth-child(4) { background:linear-gradient(135deg, #F5F0FA, #DDD2F0); }
    .info-label { font-size:0.68em; color:var(--ink-mute);
        text-transform:uppercase; font-weight:800; letter-spacing:1.2px; margin-bottom:6px; }
    .info-value { color:var(--ink); font-weight:600; font-size:0.95em; }

    .section { margin:22px 0; padding:22px 26px; border-radius:20px;
        background:linear-gradient(135deg, #FDF2F6 0%, #F5F0FA 100%);
        border:1.5px solid rgba(168,70,122,0.12); }
    .section h3 { color:var(--wine-deep); font-family:'Fraunces',serif;
        margin-bottom:12px; font-size:1.08em; }
    .section p { color:var(--ink-soft); line-height:1.7; }

    .notif-box, .linked-info { background:rgba(123,91,184,0.10);
        color:var(--plum-deep); padding:16px 22px; border-radius:16px;
        margin:18px 0; font-size:0.92em; border-left:4px solid var(--plum); }

    .owner-banner { background:linear-gradient(135deg, rgba(212,175,55,0.15), rgba(232,200,122,0.25));
        color:var(--wine-dark); padding:14px 22px; border-radius:16px;
        margin:18px 0; font-weight:600; border:1.5px solid rgba(212,175,55,0.4); }
    .locked-banner { background:rgba(140,140,160,0.10); color:#6a6578;
        padding:14px 22px; border-radius:16px; margin:18px 0;
        border:1.5px dashed rgba(140,140,160,0.35); }

    .claim-panel { margin:30px 0; padding:32px 28px;
        background:linear-gradient(135deg, #E4F7EE 0%, #C8EAD9 100%);
        border-radius:24px; border:2px solid rgba(42,107,88,0.30); }
    .claim-panel h3 { color:#065f46; margin-bottom:14px;
        font-family:'Fraunces',serif; font-size:1.35em; }
    .btn-claim { display:inline-flex; align-items:center; justify-content:center;
        background:linear-gradient(135deg, #10b981, #059669);
        color:#fff; padding:18px 36px; border-radius:100px; border:none;
        font-family:'Outfit',sans-serif; font-size:1.02em; font-weight:700;
        cursor:pointer; text-decoration:none; width:100%; }
    .claimed-panel { margin:28px 0; padding:26px 28px;
        background:#ECFDF5; border-radius:22px;
        border:2px solid rgba(42,107,88,0.30); }
    .claimed-panel h3 { color:#065f46; margin-bottom:10px;
        font-family:'Fraunces',serif; }

    .modal-overlay { display:none; position:fixed; inset:0;
        background:rgba(61,42,92,0.65); backdrop-filter:blur(12px);
        z-index:9999; align-items:center; justify-content:center; padding:20px; }
    .modal-overlay.active { display:flex; }
    .modal-box { background:#FFF; border-radius:28px; padding:40px 34px;
        max-width:520px; width:100%; text-align:center;
        box-shadow:0 40px 90px rgba(61,42,92,0.45); }
    .modal-box h2 { font-family:'Fraunces',serif; margin-bottom:16px; font-size:1.5em; }
    .modal-box p { color:var(--ink-soft); line-height:1.7; margin-bottom:28px; }
    .modal-actions { display:flex; gap:12px; }
    .modal-actions button, .modal-actions a { flex:1; padding:15px;
        border:none; border-radius:100px; font-family:'Outfit',sans-serif;
        font-weight:700; cursor:pointer; text-decoration:none; text-align:center; }
    .btn-modal-yes { background:linear-gradient(135deg, #10b981, #059669); color:#fff; }
    .btn-modal-no { background:#FFF; color:var(--ink-soft);
        border:1.5px solid rgba(168,70,122,0.20); }

    .match-panel { margin:26px 0; padding:24px;
        background:linear-gradient(135deg, #FFF8E8, #FDF0CE);
        border-radius:22px; border:2px solid rgba(212,175,55,0.40); }
    .match-panel h3 { color:#78350f; margin-bottom:12px;
        font-family:'Fraunces',serif; font-size:1.15em; }
    .match-item { background:#FFF; border-radius:14px; padding:15px 18px;
        margin-bottom:10px; display:flex; justify-content:space-between;
        align-items:center; gap:12px; border-left:4px solid var(--gold);
        flex-wrap:wrap; }
    .match-item.strong { border-left-color:var(--forest-deep); }
    .match-item.medium { border-left-color:var(--burnt); }
    .match-nama { font-weight:700; margin-bottom:4px; font-size:0.92em; }
    .match-meta { font-size:0.76em; color:var(--ink-mute); }
    .match-score { font-size:0.74em; font-weight:800; padding:6px 14px;
        border-radius:100px; margin-right:8px; }
    .score-strong { background:linear-gradient(135deg, var(--forest), var(--forest-deep)); color:#fff; }
    .score-medium { background:linear-gradient(135deg, var(--burnt), var(--burnt-deep)); color:#fff; }
    .score-weak { background:var(--ink-light); color:#fff; }
    .btn-match { background:linear-gradient(135deg, var(--plum), var(--plum-deep));
        color:#fff; padding:9px 18px; border-radius:100px;
        text-decoration:none; font-weight:700; font-size:0.8em; }

    .lock-message { text-align:center; padding:60px 30px;
        background:linear-gradient(135deg, #FDF2F6, #F5F0FA);
        border-radius:24px; border:2px dashed rgba(168,70,122,0.30); }
    .lock-message .lock-icon { font-size:5em; margin-bottom:20px; }
    .lock-message h2 { font-family:'Fraunces',serif; color:var(--wine-deep);
        margin-bottom:14px; font-size:1.6em; }
    .lock-message p { color:var(--ink-soft); margin-bottom:26px; line-height:1.7; }
    .lock-message .btn-back { display:inline-flex; align-items:center; gap:8px;
        padding:14px 32px; background:linear-gradient(135deg, var(--plum), var(--plum-deep));
        color:#fff; border-radius:100px; text-decoration:none; font-weight:700; }

    .share-box { text-align:left; }
    .share-url { background:#F5F0FA; padding:14px 18px; border-radius:12px;
        font-family:monospace; font-size:0.9em; word-break:break-all;
        margin:10px 0; border:1.5px solid rgba(168,70,122,0.15);
        color:var(--wine-deep); font-weight:600; }
    .share-label { font-weight:700; font-size:0.9em; margin-bottom:6px; margin-top:14px; }

    @media (max-width: 768px) {
        body { padding:20px 10px; }
        .hero-header { padding:80px 24px 34px; }
        .main-content { padding:24px 18px; }
        .brand { font-size:2.8em; }
        .cards { grid-template-columns:1fr; }
        .row { grid-template-columns:1fr; }
        .info-grid { grid-template-columns:1fr; }
    }
</style>
'''


# ========== LANDING PAGE ==========
HTML_LANDING = '''
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Lost &amp; Found — Sistem Informasi Barang Hilang &amp; Temuan, Universitas Jember</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,700;0,9..144,900;1,9..144,600;1,9..144,700;1,9..144,900&display=swap');
        * { margin:0; padding:0; box-sizing:border-box; }
        html, body { min-height:100vh; font-family:'Outfit',system-ui,sans-serif;
            color:#fff; overflow-x:hidden; }
        body { background:#2A1B40; display:flex; align-items:center;
            justify-content:center; padding:30px 20px; position:relative; }
        body::before { content:''; position:fixed; inset:0;
            background:
                radial-gradient(ellipse 900px 700px at 15% 20%, rgba(168,70,122,0.65), transparent 55%),
                radial-gradient(ellipse 850px 650px at 85% 30%, rgba(123,91,184,0.60), transparent 55%),
                radial-gradient(ellipse 800px 600px at 50% 90%, rgba(63,143,120,0.40), transparent 55%),
                radial-gradient(ellipse 600px 500px at 60% 10%, rgba(212,175,55,0.25), transparent 60%),
                linear-gradient(160deg, #2A1B40 0%, #1F1430 50%, #3D2A5C 100%);
            z-index:-3; }
        .stars-layer { position:fixed; inset:0; z-index:-1; pointer-events:none; }
        .star { position:absolute; width:3px; height:3px; background:#E8C87A;
            border-radius:50%; box-shadow:0 0 10px rgba(232,200,122,0.8);
            animation:sparkle 4s ease-in-out infinite; }
        @keyframes sparkle { 0%,100%{opacity:0.2;transform:scale(0.8);} 50%{opacity:0.9;transform:scale(1.3);} }
        .landing-wrap { position:relative; z-index:1; max-width:680px; width:100%; }
        .landing-card { position:relative; padding:2.5px; border-radius:34px;
            background:linear-gradient(135deg, #E8C87A 0%, #A8467A 25%, #7B5BB8 50%, #E07A45 75%, #3F8F78 100%);
            background-size:300% 300%; animation:borderFlow 10s ease infinite;
            box-shadow:0 40px 90px rgba(0,0,0,0.45); }
        @keyframes borderFlow { 0%,100%{background-position:0% 50%;} 50%{background-position:100% 50%;} }
        .landing-inner { background:linear-gradient(135deg, rgba(42,27,64,0.94), rgba(61,42,92,0.94));
            backdrop-filter:blur(30px); border-radius:32px;
            padding:62px 50px; text-align:center; position:relative; }
        .landing-badge { display:inline-flex; align-items:center; gap:10px;
            padding:9px 22px; border-radius:100px;
            background:linear-gradient(135deg, rgba(232,200,122,0.14), rgba(168,70,122,0.14));
            border:1px solid rgba(232,200,122,0.35); color:#E8C87A;
            font-size:0.7em; font-weight:800; letter-spacing:3px;
            text-transform:uppercase; margin-bottom:32px; }
        .landing-badge .dot-live { width:7px; height:7px; background:#22c55e;
            border-radius:50%; animation:livePulse 2s ease-in-out infinite; }
        @keyframes livePulse { 0%,100%{opacity:1;transform:scale(1);} 50%{opacity:0.5;transform:scale(1.4);} }
        .landing-title { font-family:'Fraunces',serif; font-style:italic;
            font-weight:900; font-size:4em; line-height:1;
            letter-spacing:-2.5px; margin-bottom:8px;
            background:linear-gradient(120deg, #F5E4B8 0%, #E8C87A 20%, #D89080 40%, #C9A0B8 60%, #B0B0E0 80%, #E8C87A 100%);
            background-size:250% auto; -webkit-background-clip:text;
            -webkit-text-fill-color:transparent; background-clip:text;
            animation:textShimmer 8s ease-in-out infinite; }
        @keyframes textShimmer { 0%,100%{background-position:0% center;} 50%{background-position:100% center;} }
        .landing-divider { display:flex; align-items:center; justify-content:center;
            gap:16px; margin:28px auto 24px; max-width:260px; }
        .landing-divider .line { flex:1; height:1.5px;
            background:linear-gradient(90deg, transparent, rgba(232,200,122,0.7), transparent); }
        .landing-divider .diamond { color:#E8C87A; font-size:0.75em; transform:rotate(45deg); }
        .landing-subtitle { font-size:1.08em; color:#D8CCE8;
            line-height:1.7; margin-bottom:10px; }
        .landing-uni { font-family:'Fraunces',serif; font-style:italic;
            font-weight:700; font-size:1.45em;
            background:linear-gradient(135deg, #E8C87A, #D89080, #C9A0B8, #B0B0E0);
            background-size:250% auto; -webkit-background-clip:text;
            -webkit-text-fill-color:transparent;
            animation:textShimmer 6s ease-in-out infinite;
            margin-bottom:46px; display:inline-block; }
        .btn-enter { display:inline-flex; align-items:center; justify-content:center;
            gap:16px; padding:22px 62px; font-family:'Outfit',sans-serif;
            font-size:1.08em; font-weight:800; letter-spacing:3.5px;
            text-transform:uppercase; color:#2A1B40;
            background:linear-gradient(135deg, #F5E4B8 0%, #E8C87A 40%, #D4AF37 70%, #F5E4B8 100%);
            background-size:250% auto; border:none; border-radius:100px;
            cursor:pointer; text-decoration:none; animation:goldShimmer 4s ease-in-out infinite;
            box-shadow:0 16px 40px rgba(232,200,122,0.40); }
        @keyframes goldShimmer { 0%,100%{background-position:0% center;} 50%{background-position:100% center;} }
        .btn-enter:hover { transform:translateY(-5px) scale(1.03); }
        .landing-footer { margin-top:38px; padding-top:28px;
            border-top:1px solid rgba(232,200,122,0.15);
            font-size:0.78em; color:rgba(216,204,232,0.6); line-height:1.75; }
        .landing-footer .credit { font-family:'Fraunces',serif;
            font-style:italic; font-weight:700; font-size:1.2em;
            background:linear-gradient(120deg, #E8C87A, #fff, #E8C87A);
            background-size:200% auto; -webkit-background-clip:text;
            -webkit-text-fill-color:transparent;
            animation:textShimmer 5s ease-in-out infinite; }
        .info-tambahan { margin-top:20px; padding:14px 20px;
            background:rgba(232,200,122,0.08); border-radius:14px;
            border:1px solid rgba(232,200,122,0.20);
            font-size:0.85em; color:rgba(216,204,232,0.9);
            line-height:1.65; text-align:left; }
        .info-tambahan strong { color:#E8C87A; }
        @media (max-width: 640px) {
            .landing-inner { padding:46px 28px; }
            .landing-title { font-size:2.9em; }
            .landing-uni { font-size:1.2em; }
            .btn-enter { padding:19px 42px; font-size:1em; }
        }
    </style>
</head>
<body>
<div class="stars-layer">
    <div class="star" style="top:8%;  left:12%;"></div>
    <div class="star" style="top:18%; left:82%;"></div>
    <div class="star" style="top:32%; left:25%;"></div>
    <div class="star" style="top:48%; left:75%;"></div>
    <div class="star" style="top:62%; left:15%;"></div>
    <div class="star" style="top:78%; left:88%;"></div>
    <div class="star" style="top:88%; left:45%;"></div>
    <div class="star" style="top:55%; left:55%;"></div>
</div>

<div class="landing-wrap">
    <div class="landing-card">
        <div class="landing-inner">
            <div class="landing-badge">
                <span class="dot-live"></span>
                Selamat Datang
            </div>

            <h1 class="landing-title">Lost &amp; Found</h1>

            <div class="landing-divider">
                <span class="line"></span>
                <span class="diamond">◆</span>
                <span class="line"></span>
            </div>

            <p class="landing-subtitle">Sistem Informasi Barang Hilang &amp; Temuan</p>
            <span class="landing-uni">Universitas Jember</span>

            <div>
                <a href="/app" class="btn-enter">
                    <span>Masuk</span>
                    <span>→</span>
                </a>
            </div>

            <div class="info-tambahan">
                🌍 <strong>Akses dari mana saja!</strong> Bagikan link publik ini ke siapa pun:
                <div style="margin-top:8px; padding:10px 14px; background:rgba(0,0,0,0.3); border-radius:10px; font-family:monospace; font-size:0.9em; color:#E8C87A; word-break:break-all;">
                    {{ public_url }}
                </div>
                <div style="margin-top:8px;">Mereka bisa langsung lapor barang hilang/temuan dari HP atau laptop, di mana pun berada.</div>
            </div>

            <div class="landing-footer">
                <p>© 2025 Lost &amp; Found — Sistem Informasi Barang Hilang &amp; Temuan, Universitas Jember</p>
                <p>Created by: <span class="credit">Algopro Pascal</span></p>
            </div>
        </div>
    </div>
</div>
</body>
</html>
'''


# ========== HALAMAN UTAMA ==========
HTML_INDEX = '''
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Lost & Found — Sistem Informasi Barang Hilang & Temuan Universitas Jember</title>
    ''' + GLOBAL_CSS + '''
</head>
<body>
<div class="frame-wrapper">
    <div class="container">
        <div class="hero-header">
            <div class="top-bar">
                <a href="/" class="btn-home">← Halaman Awal</a>
                <button class="btn-share" onclick="openShare()">📤 Bagikan</button>
            </div>

            {% if is_admin %}
            <div class="admin-bar">
                <span class="admin-badge">👑 Admin</span>
                <a href="/admin/logout" class="btn-admin-logout">Keluar</a>
            </div>
            {% endif %}

            <div class="hero-content">
                <div class="header-badge">
                    <span class="dot-live"></span> Live · Sistem Aktif
                </div>
                <div class="brand">Lost &amp; Found</div>
                <div class="divider">
                    <span class="line"></span><span class="diamond">◆</span><span class="line"></span>
                </div>
                <p class="subtitle">Sistem Informasi Barang Hilang &amp; Temuan<br>— <span class="uni">Universitas Jember</span> —</p>
            </div>
        </div>

        <div class="main-content">
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}{% for category, message in messages %}
                    <div class="alert {{ category }}">{{ message }}</div>
                {% endfor %}{% endif %}
            {% endwith %}

            <div class="stats">
                <div class="stat-card">
                    <div class="stat-icon">📦</div>
                    <div class="stat-number">{{ total }}</div>
                    <div class="stat-label">Total Laporan</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">🔴</div>
                    <div class="stat-number">{{ total_hilang }}</div>
                    <div class="stat-label">Barang Hilang</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">🟢</div>
                    <div class="stat-number">{{ total_temu }}</div>
                    <div class="stat-label">Barang Ditemukan</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">✓</div>
                    <div class="stat-number">{{ total_selesai }}</div>
                    <div class="stat-label">Selesai</div>
                </div>
            </div>

            <div class="btn-group">
                <a href="/tambah?jenis=hilang" class="btn btn-hilang">🔴 Laporkan Barang Hilang</a>
                <a href="/tambah?jenis=ditemukan" class="btn btn-temu">🟢 Laporkan Barang Ditemukan</a>
            </div>

            <div class="filter-box">
                <form method="GET" action="/app">
                    <div class="filter-row">
                        <input type="text" name="q" placeholder="🔎 Cari nama barang / ciri-ciri..." value="{{ q or '' }}">
                        <select name="kategori">
                            <option value="">📂 Semua Kategori</option>
                            {% for k in kategori_list %}
                                <option value="{{ k }}" {% if kategori == k %}selected{% endif %}>{{ k }}</option>
                            {% endfor %}
                        </select>
                        <select name="jenis">
                            <option value="">📋 Semua Jenis</option>
                            <option value="hilang" {% if jenis == 'hilang' %}selected{% endif %}>🔴 Hilang</option>
                            <option value="ditemukan" {% if jenis == 'ditemukan' %}selected{% endif %}>🟢 Ditemukan</option>
                        </select>
                        <select name="status">
                            <option value="">⚡ Semua Status</option>
                            <option value="belum" {% if status == 'belum' %}selected{% endif %}>🔴 Belum ditemukan</option>
                            <option value="proses" {% if status == 'proses' %}selected{% endif %}>🟡 Dalam proses</option>
                            <option value="selesai" {% if status == 'selesai' %}selected{% endif %}>🟢 Selesai</option>
                        </select>
                        <input type="text" name="lokasi" placeholder="📍 Lokasi..." value="{{ lokasi or '' }}">
                    </div>
                    <button type="submit" class="btn-cari">🔍 Terapkan Filter &amp; Cari</button>
                </form>
            </div>

            {% if barang_list %}
            <div class="cards">
                {% for b in barang_list %}
                <div class="card {% if b['_is_owner'] %}owned{% endif %}">
                    <div class="card-img-wrap">
                        {% if b['foto'] %}
                            <img src="/uploads/{{ b['foto'] }}" alt="{{ b['nama'] }}" class="card-img">
                        {% else %}
                            <div class="card-img-placeholder">📦</div>
                        {% endif %}
                    </div>
                    <div class="card-body">
                        <div class="card-header-row">
                            <span class="badge {% if b['jenis'] == 'hilang' %}badge-hilang{% else %}badge-temu{% endif %}">
                                {% if b['jenis'] == 'hilang' %}🔴 Hilang{% else %}🟢 Ditemukan{% endif %}
                            </span>
                            <span class="card-id">#{{ b['id'] }}</span>
                        </div>
                        <div class="card-title">{{ b['nama'] }}</div>
                        <div class="card-info">📂 {{ b['kategori'] }}</div>
                        <div class="card-info">📍 {{ b['lokasi'] }}</div>
                        <div class="card-info">📅 {{ b['tanggal'] }}</div>
                        <div class="card-desc">{{ b['ciri'] }}</div>

                        {% if b['linked_id'] %}
                            <span class="linked-badge">🔗 Terkait #{{ b['linked_id'] }}</span>
                        {% endif %}
                        {% if b['notif'] %}
                            <div class="card-notif">{{ b['notif'] }}</div>
                        {% endif %}

                        <div>
                            {% if b['status'] == 'belum' %}
                                <span class="status-pill status-belum">Belum ditemukan</span>
                            {% elif b['status'] == 'proses' %}
                                <span class="status-pill status-proses">Dalam proses</span>
                            {% else %}
                                <span class="status-pill status-selesai">Selesai</span>
                            {% endif %}
                        </div>
                        <div class="card-actions">
                            <a href="/detail/{{ b['id'] }}" class="btn-detail">👁 Detail</a>
                            {% if b['_is_owner'] or is_admin %}
                                <a href="/edit/{{ b['id'] }}" class="btn-edit">✏️ Edit</a>
                                <a href="/hapus/{{ b['id'] }}" class="btn-hapus"
                                   onclick="return confirm('Hapus laporan ini?')">🗑️</a>
                            {% else %}
                                <span class="btn-locked" title="Hanya pelapor yang bisa mengubah">🔒</span>
                            {% endif %}
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="empty-state">
                <div class="icon">📭</div>
                <p>Belum ada laporan. Yuk tambahkan laporan pertama kamu! ✨</p>
            </div>
            {% endif %}

            <footer>
                <p>© 2025 Lost &amp; Found — Sistem Informasi Barang Hilang &amp; Temuan, Universitas Jember</p>
                <p>Created by: <span class="footer-credit">Algopro Pascal</span></p>
            </footer>
        </div>
    </div>
</div>

<div class="modal-overlay" id="shareModal">
    <div class="modal-box share-box">
        <h2 style="text-align:center;">📤 Bagikan ke Siapa Saja</h2>
        <p style="text-align:center;">Kirim link ini ke teman / dosen / siapa pun. Mereka bisa <strong>akses dari mana saja</strong> — tidak harus WiFi yang sama.</p>
        <div class="share-label">🌍 Link Publik (bisa diakses dari mana saja):</div>
        <div class="share-url">{{ public_url }}</div>
        <p style="font-size:0.82em; color:var(--ink-mute); margin-top:14px; text-align:center;">
            💡 Bila belum ada URL publik, jalankan script <code>start_public.py</code>.
        </p>
        <div class="modal-actions" style="margin-top:20px;">
            <button class="btn-modal-no" onclick="closeShare()">Tutup</button>
        </div>
    </div>
</div>

<script>
function openShare() { document.getElementById('shareModal').classList.add('active'); }
function closeShare() { document.getElementById('shareModal').classList.remove('active'); }
document.getElementById('shareModal').addEventListener('click', function(e) {
    if (e.target === this) closeShare();
});
</script>
</body>
</html>
'''


# ========== FORM ==========
HTML_FORM = '''
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} — Lost & Found Universitas Jember</title>
    ''' + GLOBAL_CSS + '''
</head>
<body>
<div class="frame-wrapper">
    <div class="container" style="max-width:820px;">
        <div class="hero-header">
            <div class="top-bar">
                <a href="/app" class="btn-home">← Kembali</a>
            </div>
            <div class="hero-content">
                <div class="header-badge"><span class="dot-live"></span> Formulir Laporan</div>
                <div class="brand">Lost &amp; Found</div>
                <div class="divider"><span class="line"></span><span class="diamond">◆</span><span class="line"></span></div>
                <p class="subtitle">{{ title }}</p>
            </div>
        </div>
        <div class="main-content">
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}{% for category, message in messages %}
                    <div class="alert {{ category }}">{{ message }}</div>
                {% endfor %}{% endif %}
            {% endwith %}
            {% if is_edit and not can_edit %}
            <div class="lock-message">
                <div class="lock-icon">🔒</div>
                <h2>Akses Ditolak</h2>
                <p>Kamu bukan pelapor laporan ini. Hanya <strong>pelapor asli</strong> yang bisa mengedit data.</p>
                <a href="/app" class="btn-back">← Kembali</a>
            </div>
            {% else %}
            <form method="POST" enctype="multipart/form-data">
                <div class="form-group">
                    <label>Jenis Laporan</label>
                    <div class="jenis-toggle">
                        <label><input type="radio" name="jenis" value="hilang"
                            {% if barang and barang['jenis']=='hilang' %}checked{% endif %}
                            {% if not barang and jenis == 'hilang' %}checked{% endif %} required>
                            <span>🔴 Barang Hilang</span></label>
                        <label><input type="radio" name="jenis" value="ditemukan"
                            {% if barang and barang['jenis']=='ditemukan' %}checked{% endif %}
                            {% if not barang and jenis == 'ditemukan' %}checked{% endif %} required>
                            <span>🟢 Barang Ditemukan</span></label>
                    </div>
                </div>
                <div class="row">
                    <div class="form-group">
                        <label>Nama Barang *</label>
                        <input type="text" name="nama" required value="{{ barang['nama'] if barang else '' }}"
                            placeholder="Contoh: Dompet Kulit Hitam">
                    </div>
                    <div class="form-group">
                        <label>Kategori *</label>
                        <select name="kategori" required>
                            {% set kat = barang['kategori'] if barang else '' %}
                            {% for k in ['Elektronik','Dompet/Tas','Dokumen','Pakaian','Aksesoris','Kunci','Buku/Alat Tulis','Lainnya'] %}
                                <option value="{{ k }}" {% if kat == k %}selected{% endif %}>{{ k }}</option>
                            {% endfor %}
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label>Ciri-ciri Barang *</label>
                    <textarea name="ciri" required placeholder="Warna, merek, ukuran, tanda khusus...">{{ barang['ciri'] if barang else '' }}</textarea>
                </div>
                <div class="row">
                    <div class="form-group">
                        <label>Lokasi *</label>
                        <input type="text" name="lokasi" required value="{{ barang['lokasi'] if barang else '' }}" placeholder="Gedung A Lantai 2">
                    </div>
                    <div class="form-group">
                        <label>Tanggal *</label>
                        <input type="date" name="tanggal" required value="{{ barang['tanggal'] if barang else '' }}">
                    </div>
                </div>
                <div class="row">
                    <div class="form-group">
                        <label>Status Barang</label>
                        <select name="status">
                            {% set st = barang['status'] if barang else 'belum' %}
                            <option value="belum" {% if st=='belum' %}selected{% endif %}>🔴 Belum ditemukan</option>
                            <option value="proses" {% if st=='proses' %}selected{% endif %}>🟡 Dalam proses</option>
                            <option value="selesai" {% if st=='selesai' %}selected{% endif %}>🟢 Selesai</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Nama Pelapor</label>
                        <input type="text" name="pelapor" value="{{ barang['pelapor'] if barang else '' }}" placeholder="Nama Anda">
                    </div>
                </div>
                <div class="form-group">
                    <label>Kontak (HP/Email)</label>
                    <input type="text" name="kontak" value="{{ barang['kontak'] if barang else '' }}" placeholder="08xxx atau email">
                </div>
                <div class="form-group">
                    <label>Foto Barang (JPG/PNG, maks 5MB)</label>
                    <input type="file" name="foto" accept="image/*" onchange="previewImg(event)">
                    <p class="hint">📸 Foto akan tampil untuk semua pengguna.</p>
                    {% if barang and barang['foto'] %}
                        <img src="/uploads/{{ barang['foto'] }}" class="preview-img" id="preview">
                    {% else %}
                        <img id="preview" class="preview-img" style="display:none;">
                    {% endif %}
                </div>
                <div class="form-group">
                    <label>Keterangan Tambahan</label>
                    <textarea name="keterangan" placeholder="Info tambahan...">{{ barang['keterangan'] if barang else '' }}</textarea>
                </div>
                <div class="button-group">
                    <button type="submit">💾 Simpan Laporan</button>
                    <a href="/app" class="btn-cancel">❌ Batal</a>
                </div>
            </form>
            {% endif %}
            <footer>
                <p>© 2025 Lost &amp; Found — Universitas Jember</p>
                <p>Created by: <span class="footer-credit">Algopro Pascal</span></p>
            </footer>
        </div>
    </div>
</div>
<script>
function previewImg(e) {
    const file = e.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = ev => {
            const img = document.getElementById('preview');
            img.src = ev.target.result;
            img.style.display = 'block';
        };
        reader.readAsDataURL(file);
    }
}
</script>
</body>
</html>
'''


# ========== DETAIL ==========
HTML_DETAIL = '''
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Detail — {{ b['nama'] }} | Lost & Found Universitas Jember</title>
    ''' + GLOBAL_CSS + '''
</head>
<body>
<div class="frame-wrapper">
    <div class="container" style="max-width:900px;">
        <div class="hero-header" style="padding:50px 40px 30px;">
            <div class="top-bar">
                <a href="/app" class="btn-home">← Kembali ke Daftar</a>
            </div>
            <div class="hero-content">
                <div class="brand" style="font-size:2.2em;">Lost &amp; Found</div>
            </div>
        </div>
        <div class="main-content">
            <span class="badge-detail {% if b['jenis']=='hilang' %}badge-hilang{% else %}badge-temu{% endif %}">
                {% if b['jenis']=='hilang' %}🔴 Barang Hilang{% else %}🟢 Barang Ditemukan{% endif %}
            </span>
            <h1 style="font-family:'Fraunces',serif; font-size:2em; margin-bottom:8px;">{{ b['nama'] }}</h1>
            <p style="color:var(--ink-mute); font-size:0.85em;">📌 Laporan #{{ b['id'] }} — {{ b['created_at'] }}</p>

            {% if is_owner %}
            <div class="owner-banner">👑 <strong>Ini laporan kamu.</strong> Kamu bisa edit, hapus, dan konfirmasi status.</div>
            {% else %}
            <div class="locked-banner">🔒 Ini bukan laporan kamu. Kamu hanya bisa melihat detail.</div>
            {% endif %}

            {% if b['notif'] %}
            <div class="notif-box">✨ {{ b['notif'] }}</div>
            {% endif %}

            {% if b['linked_id'] %}
            <div class="linked-info">🔗 Terkait dengan <a href="/detail/{{ b['linked_id'] }}">Laporan #{{ b['linked_id'] }}</a>.</div>
            {% endif %}

            <div class="photo-box">
                {% if b['foto'] %}
                    <img src="/uploads/{{ b['foto'] }}" alt="{{ b['nama'] }}">
                {% else %}
                    <div class="photo-placeholder">📦</div>
                {% endif %}
            </div>

            <div class="info-grid">
                <div class="info-item"><div class="info-label">Kategori</div><div class="info-value">📂 {{ b['kategori'] }}</div></div>
                <div class="info-item"><div class="info-label">Tanggal</div><div class="info-value">📅 {{ b['tanggal'] }}</div></div>
                <div class="info-item"><div class="info-label">Lokasi</div><div class="info-value">📍 {{ b['lokasi'] }}</div></div>
                <div class="info-item"><div class="info-label">Status</div><div class="info-value">
                    {% if b['status']=='belum' %}<span class="status-pill status-belum">Belum ditemukan</span>
                    {% elif b['status']=='proses' %}<span class="status-pill status-proses">Dalam proses</span>
                    {% else %}<span class="status-pill status-selesai">Selesai</span>{% endif %}
                </div></div>
            </div>

            <div class="section"><h3>🔎 Ciri-ciri Barang</h3><p>{{ b['ciri'] }}</p></div>
            {% if b['keterangan'] %}
            <div class="section"><h3>📝 Keterangan Tambahan</h3><p>{{ b['keterangan'] }}</p></div>
            {% endif %}
            {% if b['pelapor'] or b['kontak'] %}
            <div class="section">
                <h3>👤 Kontak Pelapor</h3>
                {% if b['pelapor'] %}<p><strong>Nama:</strong> {{ b['pelapor'] }}</p>{% endif %}
                {% if b['kontak'] %}<p><strong>Kontak:</strong> {{ b['kontak'] }}</p>{% endif %}
            </div>
            {% endif %}

            {% if b['jenis'] == 'hilang' and b['linked_id'] and b['status'] != 'selesai' and is_owner %}
            <div class="claim-panel">
                <h3>🎁 Sudahkah Kamu Menerima Barang Kembali?</h3>
                <p style="color:#047857; margin-bottom:18px;">Kalau sudah, klik tombol di bawah. Status kedua laporan otomatis Selesai.</p>
                <button type="button" class="btn-claim" onclick="openConfirmModal()">✨ Saya Sudah Mendapatkan Barangnya</button>
            </div>
            {% elif b['jenis'] == 'hilang' and b['status'] == 'selesai' %}
            <div class="claimed-panel">
                <h3>🎉 Barang Sudah Diterima Kembali</h3>
                <p style="color:#047857;">Pemilik sudah konfirmasi. Status Selesai di kedua laporan terkait.</p>
            </div>
            {% endif %}

            {% if not b['linked_id'] and candidates %}
            <div class="match-panel">
                <h3>✨ Kandidat Barang Cocok Terdeteksi!</h3>
                <p style="color:#92400e; font-size:0.88em; margin-bottom:16px;">Sistem menemukan {{ candidates|length }} laporan lawan jenis yang mungkin terkait.</p>
                {% for c in candidates[:5] %}
                <div class="match-item {% if c.score >= 0.65 %}strong{% elif c.score >= 0.4 %}medium{% endif %}">
                    <div>
                        <div class="match-nama">#{{ c.item['id'] }} — {{ c.item['nama'] }}</div>
                        <div class="match-meta">📂 {{ c.item['kategori'] }} · 📍 {{ c.item['lokasi'] }}</div>
                    </div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span class="match-score {% if c.score >= 0.65 %}score-strong{% elif c.score >= 0.4 %}score-medium{% else %}score-weak{% endif %}">
                            {{ (c.score * 100)|round|int }}%
                        </span>
                        <a href="/detail/{{ c.item['id'] }}" class="btn-match">👁 Lihat</a>
                        <a href="/match/{{ b['id'] }}/{{ c.item['id'] }}" class="btn-match"
                           onclick="return confirm('Kaitkan kedua laporan?')">🔗 Kaitkan</a>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% endif %}

            <div class="button-group" style="flex-wrap:wrap;">
                <a href="/app" class="btn-cancel">⬅ Kembali</a>
                {% if is_owner or is_admin %}
                <a href="/edit/{{ b['id'] }}" class="btn-cancel">✏️ Edit</a>
                <a href="/hapus/{{ b['id'] }}" class="btn-cancel"
                   onclick="return confirm('Hapus laporan ini?')">🗑️ Hapus</a>
                {% else %}
                <span class="btn-cancel" style="cursor:not-allowed;">🔒 Hanya Pelapor</span>
                {% endif %}
            </div>

            <footer>
                <p>© 2025 Lost &amp; Found — Universitas Jember</p>
                <p>Created by: <span class="footer-credit">Algopro Pascal</span></p>
            </footer>
        </div>
    </div>
</div>

<div class="modal-overlay" id="confirmModal">
    <div class="modal-box">
        <h2>🎉 Konfirmasi Barang Sudah Diterima</h2>
        <p>Yakin sudah menerima barangmu kembali? Status <strong>kedua laporan</strong> akan berubah jadi Selesai.</p>
        <div class="modal-actions">
            <button class="btn-modal-no" onclick="closeConfirmModal()">Batal</button>
            <a href="/claim/{{ b['id'] }}" class="btn-modal-yes">Ya, Yakin!</a>
        </div>
    </div>
</div>

<script>
function openConfirmModal() { document.getElementById('confirmModal').classList.add('active'); }
function closeConfirmModal() { document.getElementById('confirmModal').classList.remove('active'); }
document.getElementById('confirmModal').addEventListener('click', function(e) {
    if (e.target === this) closeConfirmModal();
});
</script>
</body>
</html>
'''


# ========== ROUTES ==========
def get_public_url():
    """Ambil URL publik dari file kalau ada, atau dari global"""
    global PUBLIC_URL
    if PUBLIC_URL:
        return PUBLIC_URL
    # Cek file publikasi
    pub_file = os.path.join(BASE_DIR, 'public_url.txt')
    if os.path.exists(pub_file):
        try:
            with open(pub_file, 'r') as f:
                url = f.read().strip()
                if url:
                    PUBLIC_URL = url
                    return url
        except: pass
    return "http://localhost:5000"


@app.route('/')
def landing():
    return render_template_string(HTML_LANDING, public_url=get_public_url())


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        pwd = request.form.get('password', '')
        if pwd == ADMIN_PASSWORD:
            session['is_admin'] = True
            session.permanent = True
            flash('👑 Mode Admin aktif!', 'success')
            return redirect(url_for('index'))
        else:
            flash('❌ Password admin salah!', 'error')
    return '''
    <!DOCTYPE html><html><head><title>Admin Login</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: system-ui; background:#2A1B40; min-height:100vh;
               display:flex; align-items:center; justify-content:center; padding:20px; }
        .box { background:#fff; padding:40px; border-radius:20px; max-width:400px;
               width:100%; box-shadow:0 20px 60px rgba(0,0,0,0.4); text-align:center; }
        h1 { color:#7A2B54; margin-bottom:20px; font-family:Georgia,serif; }
        input { width:100%; padding:14px; border:2px solid #eee; border-radius:10px;
                margin-bottom:15px; font-size:1em; box-sizing:border-box; }
        button { width:100%; padding:14px; background:linear-gradient(135deg,#A8467A,#7A2B54);
                 color:#fff; border:none; border-radius:10px; font-weight:bold;
                 font-size:1em; cursor:pointer; }
        .back { display:inline-block; margin-top:15px; color:#7A2B54;
                text-decoration:none; font-size:0.9em; }
    </style></head><body>
        <div class="box">
            <h1>👑 Admin Login</h1>
            <form method="POST">
                <input type="password" name="password" placeholder="Password admin" required autofocus>
                <button type="submit">Masuk sebagai Admin</button>
            </form>
            <a href="/app" class="back">← Kembali ke aplikasi</a>
        </div>
    </body></html>
    '''


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    flash('👋 Mode Admin dinonaktifkan.', 'info')
    return redirect(url_for('index'))


@app.route('/app')
def index():
    q = request.args.get('q', '').strip()
    kategori = request.args.get('kategori', '').strip()
    jenis = request.args.get('jenis', '').strip()
    status = request.args.get('status', '').strip()
    lokasi = request.args.get('lokasi', '').strip()

    sql = "SELECT * FROM barang WHERE 1=1"
    params = []
    if q:
        sql += " AND (nama LIKE ? OR ciri LIKE ?)"
        params += [f'%{q}%', f'%{q}%']
    if kategori:
        sql += " AND kategori = ?"; params.append(kategori)
    if jenis:
        sql += " AND jenis = ?"; params.append(jenis)
    if status:
        sql += " AND status = ?"; params.append(status)
    if lokasi:
        sql += " AND lokasi LIKE ?"; params.append(f'%{lokasi}%')
    sql += " ORDER BY created_at DESC"

    conn = get_db_connection()
    barang_list_raw = conn.execute(sql, params).fetchall()
    barang_list = []
    for b in barang_list_raw:
        d = dict(b)
        d['_is_owner'] = is_owner(b)
        barang_list.append(d)

    total = conn.execute("SELECT COUNT(*) FROM barang").fetchone()[0]
    total_hilang = conn.execute("SELECT COUNT(*) FROM barang WHERE jenis='hilang'").fetchone()[0]
    total_temu = conn.execute("SELECT COUNT(*) FROM barang WHERE jenis='ditemukan'").fetchone()[0]
    total_selesai = conn.execute("SELECT COUNT(*) FROM barang WHERE status='selesai'").fetchone()[0]
    kategori_rows = conn.execute("SELECT DISTINCT kategori FROM barang ORDER BY kategori").fetchall()
    conn.close()

    kategori_list = [r[0] for r in kategori_rows]

    return render_template_string(
        HTML_INDEX,
        barang_list=barang_list,
        total=total, total_hilang=total_hilang,
        total_temu=total_temu, total_selesai=total_selesai,
        kategori_list=kategori_list,
        q=q, kategori=kategori, jenis=jenis,
        status=status, lokasi=lokasi,
        is_admin=is_admin(),
        public_url=get_public_url()
    )


@app.route('/tambah', methods=['GET', 'POST'])
def tambah():
    if request.method == 'POST':
        jenis = request.form.get('jenis', 'hilang')
        nama = request.form.get('nama', '').strip()
        kategori = request.form.get('kategori', 'Lainnya')
        ciri = request.form.get('ciri', '').strip()
        lokasi = request.form.get('lokasi', '').strip()
        tanggal = request.form.get('tanggal', '')
        status = request.form.get('status', 'belum')
        pelapor = request.form.get('pelapor', '').strip()
        kontak = request.form.get('kontak', '').strip()
        keterangan = request.form.get('keterangan', '').strip()

        if not nama or not ciri or not lokasi or not tanggal:
            flash('❌ Mohon lengkapi field wajib!', 'error')
            return redirect(url_for('tambah', jenis=jenis))

        foto_name = None
        if 'foto' in request.files:
            file = request.files['foto']
            if file and file.filename:
                if not allowed_file(file.filename):
                    flash('❌ Format foto tidak didukung!', 'error')
                    return redirect(url_for('tambah', jenis=jenis))
                foto_name = save_uploaded_file(file)

        owner_token = uuid.uuid4().hex

        conn = get_db_connection()
        cur = conn.execute('''
            INSERT INTO barang
            (jenis, nama, kategori, ciri, lokasi, tanggal, foto, keterangan, status, pelapor, kontak, owner_token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (jenis, nama, kategori, ciri, lokasi, tanggal, foto_name,
              keterangan, status, pelapor, kontak, owner_token))
        new_id = cur.lastrowid
        conn.commit()
        conn.close()

        add_owner_token(owner_token)
        result = auto_update_status(new_id)

        if result:
            flash(f'✅ Laporan berhasil! 🔐 Kamu pemilik — hanya kamu yang bisa edit/hapus. '
                  f'✨ Auto-match dengan Laporan #{result["matched_id"]}.', 'auto-msg')
        else:
            flash('✅ Laporan berhasil! 🔐 Kamu pemilik — hanya kamu yang bisa edit/hapus.', 'success')

        return redirect(url_for('index'))

    jenis = request.args.get('jenis', 'hilang')
    title = '📝 Laporkan Barang Hilang' if jenis == 'hilang' else '📝 Laporkan Barang Ditemukan'
    return render_template_string(HTML_FORM, title=title, barang=None, jenis=jenis,
                                   is_edit=False, can_edit=True)


@app.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit(id):
    conn = get_db_connection()
    b = conn.execute("SELECT * FROM barang WHERE id = ?", (id,)).fetchone()

    if not b:
        conn.close()
        flash('❌ Data tidak ditemukan!', 'error')
        return redirect(url_for('index'))

    if not can_edit(b):
        conn.close()
        return render_template_string(HTML_FORM, title='✏️ Edit Laporan',
                                       barang=b, jenis=b['jenis'],
                                       is_edit=True, can_edit=False)

    if request.method == 'POST':
        if not can_edit(b):
            conn.close()
            flash('❌ Kamu tidak berhak!', 'error')
            return redirect(url_for('index'))

        jenis = request.form.get('jenis', 'hilang')
        nama = request.form.get('nama', '').strip()
        kategori = request.form.get('kategori', 'Lainnya')
        ciri = request.form.get('ciri', '').strip()
        lokasi = request.form.get('lokasi', '').strip()
        tanggal = request.form.get('tanggal', '')
        status = request.form.get('status', 'belum')
        pelapor = request.form.get('pelapor', '').strip()
        kontak = request.form.get('kontak', '').strip()
        keterangan = request.form.get('keterangan', '').strip()

        foto_name = b['foto']
        if 'foto' in request.files:
            file = request.files['foto']
            if file and file.filename and allowed_file(file.filename):
                if b['foto']:
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], b['foto'])
                    if os.path.exists(old_path):
                        try: os.remove(old_path)
                        except: pass
                foto_name = save_uploaded_file(file)

        conn.execute('''
            UPDATE barang SET jenis=?, nama=?, kategori=?, ciri=?, lokasi=?,
                tanggal=?, foto=?, keterangan=?, status=?, pelapor=?, kontak=?
            WHERE id=?
        ''', (jenis, nama, kategori, ciri, lokasi, tanggal, foto_name,
              keterangan, status, pelapor, kontak, id))
        conn.commit()
        conn.close()
        flash('✅ Laporan diupdate!', 'success')
        return redirect(url_for('index'))

    conn.close()
    return render_template_string(HTML_FORM, title='✏️ Edit Laporan',
                                   barang=b, jenis=b['jenis'],
                                   is_edit=True, can_edit=True)


@app.route('/detail/<int:id>')
def detail(id):
    conn = get_db_connection()
    b = conn.execute("SELECT * FROM barang WHERE id = ?", (id,)).fetchone()
    conn.close()
    if not b:
        flash('❌ Data tidak ditemukan!', 'error')
        return redirect(url_for('index'))
    candidates = []
    if not b['linked_id']:
        candidates = find_candidates(id)
    return render_template_string(HTML_DETAIL, b=b, candidates=candidates,
                                   is_owner=is_owner(b), is_admin=is_admin())


@app.route('/match/<int:id_a>/<int:id_b>')
def match(id_a, id_b):
    if manual_match(id_a, id_b):
        flash('🔗 Berhasil dikaitkan!', 'success')
    else:
        flash('❌ Gagal.', 'error')
    return redirect(url_for('detail', id=id_a))


@app.route('/claim/<int:id>')
def claim(id):
    conn = get_db_connection()
    b = conn.execute("SELECT * FROM barang WHERE id=?", (id,)).fetchone()
    conn.close()
    if not b:
        flash('❌ Data tidak ditemukan!', 'error')
        return redirect(url_for('index'))
    if not can_edit(b):
        flash('❌ Hanya pelapor asli yang bisa konfirmasi!', 'error')
        return redirect(url_for('detail', id=id))
    result = mark_as_claimed(id)
    if result and 'error' not in result:
        flash(f'🎉 Konfirmasi berhasil! Laporan #{result["hilang_id"]} & #{result["temuan_id"]} otomatis SELESAI ✨', 'success')
    else:
        err = result.get('error') if result else 'Terjadi kesalahan.'
        flash(f'❌ Gagal: {err}', 'error')
    return redirect(url_for('detail', id=id))


@app.route('/hapus/<int:id>')
def hapus(id):
    conn = get_db_connection()
    b = conn.execute("SELECT * FROM barang WHERE id = ?", (id,)).fetchone()
    if not b:
        conn.close()
        flash('❌ Data tidak ditemukan!', 'error')
        return redirect(url_for('index'))
    if not can_edit(b):
        conn.close()
        flash('🔒 Kamu tidak berhak menghapus!', 'error')
        return redirect(url_for('index'))
    if b['foto']:
        old_path = os.path.join(app.config['UPLOAD_FOLDER'], b['foto'])
        if os.path.exists(old_path):
            try: os.remove(old_path)
            except: pass
    if b['linked_id']:
        conn.execute("UPDATE barang SET linked_id=NULL, notif='⚠️ Laporan terkait telah dihapus.' WHERE id=?",
                     (b['linked_id'],))
    conn.execute("DELETE FROM barang WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    flash('✅ Laporan dihapus!', 'success')
    return redirect(url_for('index'))


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.errorhandler(413)
def too_large(e):
    flash('❌ Ukuran foto terlalu besar! Maksimal 5 MB.', 'error')
    return redirect(url_for('index'))


# ========== MAIN ==========
init_db()

if __name__ == '__main__':
    ip = get_public_url()
    print("=" * 70)
    print("  ✨  LOST & FOUND — Public Access Edition")
    print("  ")
    print("  🌍 URL Publik (bagikan ke siapa saja):")
    print(f"     {ip}")
    print("  ")
    print("  💡 Orang lain bisa akses dari mana saja!")
    print("=" * 70)
    app.run(debug=False, host='0.0.0.0', port=5000)