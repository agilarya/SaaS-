import os
import psutil
import time
import requests
import json
from flask import Flask, request, jsonify, abort, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from threading import Thread
from datetime import datetime
from rute_web import daftarkan_rute

try:
    from flask_talisman import Talisman
except ImportError:
    Talisman = None

from flask_wtf.csrf import CSRFProtect

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

# Keamanan HTTPS (Talisman)
if Talisman:
    Talisman(app, content_security_policy=None, force_https=False)

csrf = CSRFProtect(app)
app.secret_key = os.environ.get('S', 'a')

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =========================================================================
# 🛡️ KEAMANAN, LIMITER & DATABASE BLACKLIST
# =========================================================================
limiter = Limiter(get_remote_address, app=app, default_limits=["500 per day", "120 per hour"], storage_uri="memory://")

BLOCKED_IPS = ['184.72.121.156', '54.175.74.27']
BLOCKED_UAS = ['Mac OS X 10.10.1', 'python-requests', 'scrapy', 'curl']

# Fungsi baca & tulis IP yang di-banned dari Command Center
FILE_BLACKLIST = os.path.join(BASE_DIR, 'blacklist_ips.json')

def load_blacklist():
    if os.path.exists(FILE_BLACKLIST):
        with open(FILE_BLACKLIST, 'r') as f:
            try: return json.load(f)
            except: return []
    return []

def save_blacklist(data):
    with open(FILE_BLACKLIST, 'w') as f:
        json.dump(data, f)

@app.before_request
def bouncer_security():
    raw_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ip = raw_ip.split(',')[0].strip() if raw_ip else '0.0.0.0'

    # Satpam ngecek IP bawaan DAN IP hasil tembakan dari Command Center
    blacklist_dinamis = load_blacklist()
    if ip in BLOCKED_IPS or ip in blacklist_dinamis:
        abort(403)

    ua = request.headers.get('User-Agent', '')
    if any(bot in ua for bot in BLOCKED_UAS):
        abort(403)

# =========================================================================
# 📡 MONITORING & API KONTROL
# =========================================================================
total_trafik = 0
waktu_server_nyala = time.time()

def tembak_ke_command_center(log_data):
    try:
        requests.post("https://itarya.pythonanywhere.com/api/terima_log_skripsikuu", json=log_data, timeout=2)
    except:
        pass

@app.before_request
def catat_trafik():
    if request.path in ['/api/agen-status', '/favicon.ico', '/api/remote_ban'] or request.path.startswith('/static/'):
        return

    global total_trafik
    total_trafik += 1

    # 1. Ambil IP Asli
    raw_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ip_asli = raw_ip.split(',')[0].strip() if raw_ip else '0.0.0.0'

    # 2. Ambil User-Agent (Device)
    user_agent = request.headers.get('User-Agent', 'Unknown Device')

    # 3. INFORMASI EXTRA
    metode_http = request.method
    asal_link = request.referrer or "Direct / Ketik Manual"
    url_lengkap = request.url

    # 4. LOGIKA PENDETEKSI BOT VS MANUSIA
    user_aktif = session.get('username') # Cek apakah dia udah login

    if not user_aktif:
        # Kalau belum login, cek dari jenis browser-nya (User-Agent)
        ua_lower = user_agent.lower()

        # Daftar komprehensif untuk mendeteksi anomali traffic dan bot
        bot_keywords = (
            'bot', 'crawl', 'spider', 'scrap', 'aws', 'curl', 'requests', 'postman', 'mac os x 10.10.1', 'python',
            'nmap', 'zgrab', 'masscan', 'nikto', 'sqlmap', 'burp', 'dirb', 'gobuster',
            'urllib', 'java', 'golang', 'ruby', 'php', 'wget', 'libwww',
            'headless', 'puppeteer', 'phantomjs', 'selenium',
            'googlebot', 'bingbot', 'yandex', 'baidu', 'petalbot'
        )

        if any(bot in ua_lower for bot in bot_keywords):
            user_aktif = "🤖 System / BOT"
        else:
            user_aktif = "👤 Guest (Visitor Asli)"

    # Bungkus datanya
    log_data = {
        "waktu": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ip": ip_asli,
        "rute": request.path,
        "device": user_agent,
        "metode": metode_http,
        "referrer": asal_link,
        "url_full": url_lengkap,
        "user": user_aktif
    }

    # Kirim ke IT Command Center (Monitoring)
    Thread(target=tembak_ke_command_center, args=(log_data,)).start()

@app.route('/api/agen-status')
def agen_status():
    if request.args.get('kunci') != "RAHASIA_SKRIPSIKUU_99":
        abort(403)

    return jsonify({
        "status": "Aman",
        "cpu": psutil.cpu_percent(interval=0.1),
        "ram": psutil.virtual_memory().percent,
        "trafik": total_trafik,
        "uptime": round(time.time() - waktu_server_nyala, 0)
    })

# 🔥 PENERIMA RUDAL DARI COMMAND CENTER
@csrf.exempt
@app.route('/api/remote_ban', methods=['POST'])
def remote_ban():
    data = request.json
    if not data or data.get('kunci') != "RAH":
        abort(403)

    ip_target = data.get('ip')
    if ip_target:
        blacklist = load_blacklist()
        if ip_target not in blacklist:
            blacklist.append(ip_target)
            save_blacklist(blacklist)
        return jsonify({"status": "sukses", "pesan": f"IP {ip_target} berhasil dihanguskan!"}), 200

    return jsonify({"status": "gagal", "pesan": "IP target kosong bos"}), 400

# 🔓 PENERIMA PERINTAH UNBAN DARI COMMAND CENTER
@csrf.exempt
@app.route('/api/remote_unban', methods=['POST'])
def remote_unban():
    data = request.json
    if not data or data.get('kunci') != "RAHASIA_SKRIPSIKUU_99":
        abort(403)

    ip_target = data.get('ip')
    if ip_target:
        blacklist = load_blacklist()
        if ip_target in blacklist:
            blacklist.remove(ip_target)
            save_blacklist(blacklist)
            return jsonify({"status": "sukses", "pesan": f"Pemblokiran IP {ip_target} berhasil dicabut."}), 200
        return jsonify({"status": "sukses", "pesan": f"IP {ip_target} tidak ditemukan dalam daftar blokir."}), 200

    return jsonify({"status": "gagal", "pesan": "Parameter IP tidak valid."}), 400


# =========================================================================
# ⚙️ REGISTRASI RUTE & CORS
# =========================================================================
daftarkan_rute(app)

@app.after_request
def izinkan_cors(response):
    # 1. Bikin daftar VIP (Domain yang boleh nembak API Skripsikuu)
    domain_aman = [
        'https://skripsikuu.pythonanywhere.com',  # Web Skripsikuu sendiri
        'https://itarya.pythonanywhere.com',      # Web Monitoring lu
        'http://localhost:5000',                  # Buat lu testing di komputer lokal
        'http://127.0.0.1:5000'
    ]

    # 2. Cek siapa yang lagi ngetuk pintu
    origin_masuk = request.headers.get('Origin')

    # 3. Kalau dia ada di daftar VIP, bukain pintunya!
    if origin_masuk in domain_aman:
        response.headers['Access-Control-Allow-Origin'] = origin_masuk

    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    return response

#DAFTARKAN PORTAL CUSTOMER (CRM BLUEPRINT)
from rute_client import client_bp
app.register_blueprint(client_bp, url_prefix='/client')

if __name__ == '__main__':
    app.run(debug=True)