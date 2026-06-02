import os
import requests
import json
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, request, session, redirect, url_for, render_template, flash

from mesin_db import get_db_connection

# 🛡️ Inisialisasi Blueprint Khusus Customer (Terisolasi dari Admin)
client_bp = Blueprint('client', __name__)

# ==========================================
# 📧 MESIN PENGIRIM MAGIC LINK (BREVO API)
# ==========================================
def kirim_email_magic_link(email_tujuan, nama_klien, token):
    # MASUKIN API KEY BREVO LU DI SINI
    BREVO_API_KEY = "x"

    # Pengaturan pengirim (Bisa pakai email apa aja asalkan lu punya akses, atau email default Brevo)
    SENDER_EMAIL = "a" # Ganti dengan email Skripsikuu (nggak harus email valid kalau buat testing awal di Brevo, tapi idealnya email yg terverifikasi di Brevo)
    SENDER_NAME = "A"

    link_login = f"https://skripsikuu.pythonanywhere.com/client/verify?token={token}"

    # Kita susun body emailnya biar cakep (pakai HTML biar rapi)
    html_content = f"""
    <html>
    <body>
        <h2>Kunci Masuk Portal Skripsikuu</h2>
        <p>Halo {nama_klien},</p>
        <p>Silakan klik tombol di bawah ini untuk mengakses dashboard pesanan Anda secara otomatis:</p>
        <p>
            <a href="{link_login}" style="background-color: #0d6efd; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">
                Masuk ke Portal Skripsikuu
            </a>
        </p>
        <p><i>Link ini hanya berlaku selama 15 menit dan hanya bisa digunakan satu kali.</i></p>
        <p>Jika tombol tidak berfungsi, salin tautan berikut ke browser Anda:</p>
        <p><small>{link_login}</small></p>
        <br>
        <p>Salam hangat,<br>Tim Skripsikuu</p>
    </body>
    </html>
    """

    # Struktur data buat API Brevo (Format JSON)
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }
    data = {
        "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
        "to": [{"email": email_tujuan, "name": nama_klien}],
        "subject": "Akses Masuk - Skripsikuu Portal",
        "htmlContent": html_content
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(data))

        if response.status_code in [200, 201, 202]:
             return "SUKSES"
        else:
             error_detail = response.json()
             return f"Gagal API: {error_detail}"

    except Exception as e:
        return str(e)


# ==========================================
# 🚪 JALUR LOGIN & REGISTRASI CUSTOMER
# ==========================================
@client_bp.route('/login', methods=['GET', 'POST'])
def login_client():
    if 'client_id' in session:
        return redirect(url_for('client.dashboard_client'))

    if request.method == 'POST':
        email_input = request.form.get('email').strip().lower()

        conn = get_db_connection()

        # Cek apakah customer udah daftar
        klien = conn.execute('SELECT * FROM clients WHERE email = ?', (email_input,)).fetchone()

        if not klien:
            conn.close()
            flash('Email belum terdaftar! Silakan daftar terlebih dahulu.', 'warning')
            return redirect(url_for('client.register_client'))

        nama_klien = klien['nama_lengkap']

        # Bikin Token Unik
        token_baru = secrets.token_urlsafe(32)
        waktu_kadaluarsa = datetime.now() + timedelta(minutes=15)

        conn.execute('INSERT INTO magic_links (email, token, kadaluarsa) VALUES (?, ?, ?)',
                     (email_input, token_baru, waktu_kadaluarsa.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()

        # Tembak Email ke Customer
        status_kirim = kirim_email_magic_link(email_input, nama_klien, token_baru)

        if status_kirim == "SUKSES":
            flash('Akses masuk sudah dikirim! Cek Inbox atau folder Spam email Anda.', 'success')
        else:
            flash(f'Gagal ngirim email! Server bilang: {status_kirim}', 'danger')

        return redirect(url_for('client.login_client'))

    return render_template('client/login.html')


@client_bp.route('/register', methods=['GET', 'POST'])
def register_client():
    if 'client_id' in session:
        return redirect(url_for('client.dashboard_client'))

    if request.method == 'POST':
        email_input = request.form.get('email').strip().lower()
        nama_input = request.form.get('nama_lengkap').strip()

        conn = get_db_connection()

        # 1. Cek apakah email udah ada
        klien_cek = conn.execute('SELECT * FROM clients WHERE email = ?', (email_input,)).fetchone()

        if klien_cek:
            conn.close()
            flash('Email sudah terdaftar bos! Langsung login aja.', 'warning')
            return redirect(url_for('client.login_client'))

        # 2. Buat akun baru di database
        conn.execute('INSERT INTO clients (email, nama_lengkap) VALUES (?, ?)', (email_input, nama_input))
        conn.commit()

        # 3. Langsung bikinin Magic Link buat login pertama kalinya
        token_baru = secrets.token_urlsafe(32)
        waktu_kadaluarsa = datetime.now() + timedelta(minutes=15)

        conn.execute('INSERT INTO magic_links (email, token, kadaluarsa) VALUES (?, ?, ?)',
                     (email_input, token_baru, waktu_kadaluarsa.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()

        # 4. Tembak Email
        status_kirim = kirim_email_magic_link(email_input, nama_input, token_baru)

        if status_kirim == "SUKSES":
            flash('Pendaftaran berhasil! Cek email untuk masuk ke Dashboard.', 'success')
            return redirect(url_for('client.login_client'))
        else:
            # Biar error API-nya juga kelihatan kalau gagal pas register
            flash(f'Pendaftaran berhasil, tapi gagal ngirim email! Server bilang: {status_kirim}', 'danger')
            return redirect(url_for('client.login_client'))

    return render_template('client/register.html')


# ==========================================
# 🔑 JALUR VERIFIKASI TOKEN (SATPAM MAGIC LINK)
# ==========================================
@client_bp.route('/verify')
def verify_magic_link():
    token = request.args.get('token')
    if not token:
        return "Akses Ditolak: Token tidak ditemukan.", 403

    conn = get_db_connection()
    data_token = conn.execute('SELECT * FROM magic_links WHERE token = ?', (token,)).fetchone()

    # 1. Validasi Token Ada atau Tidak
    if not data_token:
        conn.close()
        return "Akses Ditolak: Token tidak valid.", 403

    # 2. Validasi Apakah Sudah Pernah Dipakai
    if data_token['status_pakai'] == 1:
        conn.close()
        return "Akses Ditolak: Link ini sudah pernah digunakan. Silakan minta link baru.", 403

    # 3. Validasi Waktu Kadaluarsa (15 Menit)
    waktu_sekarang = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if waktu_sekarang > data_token['kadaluarsa']:
        conn.close()
        return "Akses Ditolak: Link ini sudah kadaluarsa. Silakan minta link baru.", 403

    # 4. TOKEN VALID! Berikan Akses Masuk
    klien = conn.execute('SELECT * FROM clients WHERE email = ?', (data_token['email'],)).fetchone()

    # Kunci token biar gak bisa dipake 2x
    conn.execute('UPDATE magic_links SET status_pakai = 1 WHERE token = ?', (token,))
    conn.commit()
    conn.close()

    # Buat Sesi Khusus Customer (Terpisah dari session['id'] milik admin)
    session.permanent = True
    session['client_id'] = klien['id']
    session['client_email'] = klien['email']
    session['client_nama'] = klien['nama_lengkap']

    return redirect(url_for('client.dashboard_client'))


# ==========================================
# 🏠 JALUR DASHBOARD CUSTOMER (TERKUNCI)
# ==========================================
@client_bp.route('/dashboard')
def dashboard_client():
    if 'client_id' not in session:
        return redirect(url_for('client.login_client'))
    
    conn = get_db_connection()
    # Tarik data pesanan milik klien yang lagi login
    daftar_pesanan = conn.execute(
        'SELECT * FROM orders WHERE client_id = ? ORDER BY id DESC', 
        (session['client_id'],)
    ).fetchall()
    conn.close()
    
    return render_template('client/dashboard.html', pesanan=daftar_pesanan)


#=====================
# Jalur pesanan
#=====================

@client_bp.route('/pesanan/<int:id>')
def detail_pesanan(id):
    if 'client_id' not in session:
        return redirect(url_for('client.login_client'))
    
    conn = get_db_connection()
    # 1. Tarik data pesanan (Pastikan ini orderan milik dia)
    pesanan = conn.execute(
        'SELECT * FROM orders WHERE id = ? AND client_id = ?', 
        (id, session['client_id'])
    ).fetchone()
    
    # Kalau pesanan ga ketemu (atau dia nyoba ngintip orderan orang)
    if not pesanan:
        conn.close()
        flash('Pesanan tidak ditemukan atau Anda tidak memiliki akses.', 'danger')
        return redirect(url_for('client.dashboard_client'))
    
    # 2. Nanti di sini kita tarik data chat nego & invoice (sementara kita kosongin dulu buat UI)
    chat_nego = [] 
    
    conn.close()
    return render_template('client/detail_pesanan.html', pesanan=pesanan, chat=chat_nego)

@client_bp.route('/logout')
def logout_client():
    # Hapus sesi HANYA untuk customer
    session.pop('client_id', None)
    session.pop('client_email', None)
    session.pop('client_nama', None)
    return redirect(url_for('client.login_client'))
