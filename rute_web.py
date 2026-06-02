import os
import time
import csv
import io
import shutil
import platform
import urllib.parse
import glob
from flask import send_file
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from flask import render_template, request, redirect, url_for, session, send_from_directory, flash, make_response, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from PIL import Image
from mesin_db import get_db_connection, catat_kas_otomatis, catat_log
from storage_manager import pindah_ke_cloud

# ==========================================
# ⚙️ KONFIGURASI ZONA WAKTU & SISTEM
# ==========================================
# PAKSA SERVER JADI WAKTU JAKARTA (WIB)
os.environ['TZ'] = 'Asia/Jakarta'
try:
    time.tzset()
except AttributeError:
    pass

# Daftar file yang diizinkan (Satpam Pintu Upload)
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'zip', 'rar', 'jpg', 'jpeg', 'png', 'xls', 'xlsx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

satpam = Limiter(key_func=get_remote_address)

# ==========================================
# PUSAT REGISTRASI RUTE APLIKASI
# ==========================================
def daftarkan_rute(app):
    satpam.init_app(app)
    app.permanent_session_lifetime = timedelta(hours=1)

    # ----------------------------------------
    # CONTEXT PROCESSORS (INJEKSI DATA KE HTML)
    # ----------------------------------------
    @app.context_processor
    def inject_banners():
        banner_folder = os.path.join(app.root_path, 'static', 'banners')
        if os.path.exists(banner_folder):
            banners = [f for f in os.listdir(banner_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))]
        else:
            banners = []
        return dict(daftar_banner=banners)

    @app.context_processor
    def inject_notifications():
        if 'loggedin' not in session: return dict(notif_count=0)

        conn = get_db_connection()
        role, user_id = session['role'], session['id']
        notif_count = 0

        try:
            if role == 'member':
                tersedia = conn.execute('SELECT COUNT(id) as jml FROM tugas WHERE member_id IS NULL').fetchone()['jml']
                revisi = conn.execute('SELECT COUNT(id) as jml FROM tugas WHERE member_id = ? AND status = "Revision Needed"', (user_id,)).fetchone()['jml']
                notif_count = tersedia + revisi
            elif role == 'reviewer':
                ready = conn.execute('SELECT COUNT(id) as jml FROM tugas WHERE reviewer_id = ? AND status = "Ready for Review"', (user_id,)).fetchone()['jml']
                notif_count = ready
            elif role in ['admin', 'superadmin']:
                ready = conn.execute('SELECT COUNT(id) as jml FROM tugas WHERE status = "Ready for Review"').fetchone()['jml']
                notif_count = ready
        except:
            pass
        finally:
            conn.close()

        return dict(notif_count=notif_count)


    # ----------------------------------------
    # AUTHENTICATION (LOGIN & LOGOUT)
    # ----------------------------------------
    @app.route('/')
    def home():
        if 'loggedin' in session:
            return redirect(url_for('dashboard'))
        return redirect(url_for('login'))

    @app.route('/login', methods=['GET', 'POST'])
    @satpam.limit("10 per minute", methods=["POST"])
    def login():
        if request.method == 'POST':
            conn = get_db_connection()
            user = conn.execute('SELECT * FROM users WHERE username = ?', (request.form['username'],)).fetchone()
            conn.close()

            if user and check_password_hash(user['password'], request.form['password']):
                session.permanent = True
                session['loggedin'] = True
                session['id'] = user['id']
                session['username'] = user['username']
                session['nama_lengkap'] = user['nama_lengkap']
                session['foto'] = user['foto']

                # INI YANG BIKIN LU TETEP JADI BOS BESAR (ID 1 = SUPERADMIN)
                session['role'] = 'superadmin' if user['id'] == 1 or user['username'] == 'arya' else user['role']

                catat_log(user['id'], "Berhasil Login ke sistem")
                return redirect(url_for('dashboard'))

            flash('Username atau password salah bos! Coba cek lagi deh.', 'danger')
            return redirect(url_for('login'))

        return render_template('login.html')

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))


    # ----------------------------------------
    # DASHBOARD & STATISTIK UMUM
    # ----------------------------------------
    @app.route('/dashboard')
    def dashboard():
        if 'loggedin' not in session: return redirect(url_for('login'))

        conn = get_db_connection()
        role, user_id = session['role'], session['id']

        query_base = '''
            SELECT t.*, m.nama_lengkap AS nama_member, r.nama_lengkap AS nama_reviewer
            FROM tugas t
            LEFT JOIN users m ON t.member_id = m.id
            LEFT JOIN users r ON t.reviewer_id = r.id
        '''

        tugas_list, tugas_tersedia = [], []
        total_tugas, tugas_selesai, tugas_revisi = 0, 0, 0
        labels_member, data_member = [], []
        pendapatan_member = 0
        labels_bulan, data_bulan = [], []

        if role in ['admin', 'superadmin']:
            tugas_list = conn.execute(query_base + ' WHERE t.status != "Done" ORDER BY t.deadline ASC').fetchall()
            total_tugas = conn.execute("SELECT COUNT(id) as total FROM tugas").fetchone()['total']
            tugas_selesai = conn.execute("SELECT COUNT(id) as total FROM tugas WHERE status = 'Done'").fetchone()['total']
            tugas_revisi = conn.execute("SELECT COUNT(id) as total FROM tugas WHERE status = 'Revision Needed'").fetchone()['total']

            #  INI RUMUS YANG UDAH DISINKRONISASI SAMA LEADERBOARD
            member_stats = conn.execute('''
                SELECT m.nama_lengkap, COUNT(t.id) as jumlah
                FROM users m
                LEFT JOIN tugas t ON m.id = t.member_id AND t.status = 'Done'
                WHERE m.role = 'member'
                GROUP BY m.id
                ORDER BY jumlah DESC
            ''').fetchall()

            labels_member = [row['nama_lengkap'] for row in member_stats]
            data_member = [row['jumlah'] for row in member_stats]

            stats_bulan = conn.execute("SELECT strftime('%Y-%m', deadline) as bulan, COUNT(id) as jumlah FROM tugas WHERE status = 'Done' GROUP BY bulan ORDER BY bulan ASC LIMIT 6").fetchall()
            labels_bulan = [row['bulan'] for row in stats_bulan]
            data_bulan = [row['jumlah'] for row in stats_bulan]

        elif role == 'member':
            tugas_list = conn.execute(query_base + ' WHERE t.status != "Done" AND t.member_id = ? ORDER BY t.deadline ASC', (user_id,)).fetchall()
            tugas_tersedia = conn.execute(query_base + ' WHERE t.member_id IS NULL').fetchall()

            tugas_beres = conn.execute("SELECT COUNT(id) as total, SUM(fee_tim) as cuan FROM tugas WHERE member_id = ? AND status = 'Done'", (user_id,)).fetchone()
            tugas_selesai = tugas_beres['total'] or 0
            pendapatan_member = tugas_beres['cuan'] or 0

            stats_bulan = conn.execute("SELECT strftime('%Y-%m', deadline) as bulan, COUNT(id) as jumlah FROM tugas WHERE member_id = ? AND status = 'Done' GROUP BY bulan ORDER BY bulan ASC LIMIT 6", (user_id,)).fetchall()
            labels_bulan = [row['bulan'] for row in stats_bulan]
            data_bulan = [row['jumlah'] for row in stats_bulan]

        elif role == 'reviewer':
            tugas_list = conn.execute(query_base + ' WHERE t.status != "Done" AND t.reviewer_id = ? ORDER BY t.deadline ASC', (user_id,)).fetchall()

        conn.close()

        # Sekarang bertindak sebagai pengukur waktu real-time sampai ke menitnya
        hari_ini = datetime.now().strftime('%Y-%m-%d %H:%M')

        # Besok diset default ke jam 23:59 buat hiasan badge besok
        besok = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d 23:59')

        return render_template(
            'dashboard.html',
            tugas=tugas_list,
            tersedia=tugas_tersedia,
            total_tugas=total_tugas,
            tugas_selesai=tugas_selesai,
            tugas_revisi=tugas_revisi,
            labels_member=labels_member,
            data_member=data_member,
            pendapatan_member=pendapatan_member,
            labels_bulan=labels_bulan,
            data_bulan=data_bulan,
            hari_ini=hari_ini,
            besok=besok
        )

    @app.route('/statistik')
    def statistik():
        if 'loggedin' not in session:
            return redirect(url_for('login'))

        conn = get_db_connection()
        role = session['role']
        user_id = session['id']

        # 1. TARIK DATA UNTUK GRAFIK STATUS & DEADLINE
        if role in ['admin', 'superadmin', 'reviewer']:
            tugas_stat = conn.execute(
            'SELECT status, deadline FROM tugas'
        ).fetchall()
        else:
            tugas_stat = conn.execute(
            '''
            SELECT status, deadline
            FROM tugas
            WHERE member_id = ? OR member_id IS NULL
            ''',
            (user_id,)
        ).fetchall()

    # ==========================================
    # DOUGHNUT CHART STATUS
    # ==========================================
        status_count = {
            "To-Do": 0,
            "In Progress": 0,
            "Ready for Review": 0,
            "Revision Needed": 0,
            "Done": 0
        }

        for t in tugas_stat:
            if t['status'] in status_count:
                status_count[t['status']] += 1

        labels_status = list(status_count.keys())
        data_status = list(status_count.values())

    # ==========================================
    # PIE CHART DEADLINE
    # ==========================================
        hari_ini = datetime.now().strftime('%Y-%m-%d')

        deadline_count = {
            "Terlambat": 0,
            "Hari Ini": 0,
            "Aman/Mendatang": 0,
            "Tanpa Deadline": 0
        }

        for t in tugas_stat:
            if t['status'] != 'Done':

                if not t['deadline']:
                    deadline_count['Tanpa Deadline'] += 1
                elif str(t['deadline'])[:10] < hari_ini:
                    deadline_count['Terlambat'] += 1
                elif str(t['deadline'])[:10] == hari_ini:
                    deadline_count['Hari Ini'] += 1
                else:
                    deadline_count['Aman/Mendatang'] += 1

        labels_deadline = list(deadline_count.keys())
        data_deadline = list(deadline_count.values())

    # ==========================================
    # GRAFIK BULANAN 6 BULAN TERAKHIR
    # ==========================================
        today = datetime.now()

        labels_bulan = []
        data_bulan = []

        for i in range(5, -1, -1):
            m = today.month - i
            y = today.year

        if m <= 0:
            m += 12
            y -= 1

        nama_bulan = datetime(1900, m, 1).strftime('%b')
        labels_bulan.append(f"{nama_bulan} {y}")

        bln_str = f"{y:04d}-{m:02d}"

        if role in ['admin', 'superadmin', 'reviewer']:

            jml = conn.execute(
                '''
                SELECT COUNT(id) as jml
                FROM tugas
                WHERE status = "Done"
                AND deadline LIKE ?
                ''',
                (bln_str + '%',)
            ).fetchone()['jml']

        else:

            jml = conn.execute(
                '''
                SELECT COUNT(id) as jml
                FROM tugas
                WHERE status = "Done"
                AND member_id = ?
                AND deadline LIKE ?
                ''',
                (user_id, bln_str + '%')
            ).fetchone()['jml']

        data_bulan.append(jml)

        conn.close()

        return render_template(
            'statistik.html',
            labels_bulan=labels_bulan,
            data_bulan=data_bulan,
            labels_status=labels_status,
            data_status=data_status,
            labels_deadline=labels_deadline,
            data_deadline=data_deadline
        )


    # ----------------------------------------
    # MANAJEMEN TUGAS & KANBAN
    # ----------------------------------------

    @app.route('/daftar_tugas')
    def daftar_tugas():
        if 'loggedin' not in session:
            return redirect(url_for('login'))

        conn = get_db_connection()
        role = session['role']
        user_id = session['id']

        if role in ['admin', 'superadmin']:
            tugas = conn.execute(
                'SELECT * FROM tugas WHERE status != "Done" ORDER BY deadline ASC'
            ).fetchall()

        elif role == 'reviewer':
            tugas = conn.execute(
                '''
                SELECT * FROM tugas
                WHERE status IN (
                    "Ready for Review",
                    "In Progress",
                    "Revision Needed"
                )
                ORDER BY deadline ASC
                '''
            ).fetchall()

        else:
            tugas = conn.execute(
                '''
                SELECT * FROM tugas
                WHERE (member_id = ? OR member_id IS NULL)
                AND status != "Done"
                ORDER BY deadline ASC
                ''',
                (user_id,)
            ).fetchall()

        conn.close()

        hari_ini = datetime.now().strftime('%Y-%m-%d')
        besok = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

        return render_template(
            'daftar_tugas.html',
            tugas=tugas,
            hari_ini=hari_ini,
            besok=besok
        )

    @app.route('/kanban')
    def kanban():
        if 'loggedin' not in session: return redirect(url_for('login'))

        conn = get_db_connection()
        role, user_id = session['role'], session['id']

        # Tarik data tugas sekalian nama member dan reviewer-nya
        query_base = '''
            SELECT t.*, m.nama_lengkap AS nama_member, r.nama_lengkap AS nama_reviewer
            FROM tugas t
            LEFT JOIN users m ON t.member_id = m.id
            LEFT JOIN users r ON t.reviewer_id = r.id
        '''

        if role in ['admin', 'superadmin']:
            tugas_list = conn.execute(query_base + ' ORDER BY t.deadline ASC').fetchall()
        elif role == 'member':
            tugas_list = conn.execute(query_base + ' WHERE t.member_id = ? OR t.member_id IS NULL ORDER BY t.deadline ASC', (user_id,)).fetchall()
        elif role == 'reviewer':
            tugas_list = conn.execute(query_base + ' WHERE t.reviewer_id = ? ORDER BY t.deadline ASC', (user_id,)).fetchall()

        conn.close()

        # Pisahin tugas ke masing-masing kotak berdasarkan statusnya
        todo = [t for t in tugas_list if t['status'] == 'To-Do']
        in_progress = [t for t in tugas_list if t['status'] == 'In Progress']
        revisi = [t for t in tugas_list if t['status'] == 'Revision Needed']
        review = [t for t in tugas_list if t['status'] == 'Ready for Review']
        done = [t for t in tugas_list if t['status'] == 'Done']

        return render_template('kanban.html', todo=todo, in_progress=in_progress, revisi=revisi, review=review, done=done)

    @app.route('/update_status_kanban', methods=['POST'])
    def update_status_kanban():
        if 'loggedin' not in session:
            return jsonify({'status': 'error', 'pesan': 'Silakan login kembali.'})

        data = request.get_json()
        tugas_id = data.get('tugas_id')
        status_baru = data.get('status_baru')
        user_id = session['id']
        role = session['role']

        conn = get_db_connection()
        tugas = conn.execute('SELECT * FROM tugas WHERE id = ?', (tugas_id,)).fetchone()

        if not tugas:
            conn.close()
            return jsonify({'status': 'error', 'pesan': 'Tugas tidak ditemukan.'})

        # SATPAM HAK AKSES (Biar member gak sembarangan narik ke status "Done")
        bisa_update = False
        pesan_error = ""

        if role in ['admin', 'superadmin']:
            bisa_update = True  # Admin bebas geser ke mana aja
        elif role == 'member' and tugas['member_id'] == user_id:
            # Member cuma boleh narik ke In Progress atau Ready for Review
            if status_baru in ['In Progress', 'Ready for Review']:
                bisa_update = True
            else:
                pesan_error = "Member hanya boleh memindahkan ke 'In Progress' atau 'Review'."
        elif role == 'reviewer' and tugas['reviewer_id'] == user_id:
            # Reviewer cuma boleh narik ke Revision Needed atau Done
            if status_baru in ['Revision Needed', 'Done']:
                bisa_update = True
            else:
                pesan_error = "Reviewer hanya boleh memberi 'Revisi' atau 'Done'."
        else:
            pesan_error = "Anda tidak berhak menggeser tugas ini!"

        # EKSEKUSI DATABASE
        if bisa_update:
            conn.execute('UPDATE tugas SET status = ? WHERE id = ?', (status_baru, tugas_id))
            conn.commit()
            conn.close()
            return jsonify({'status': 'success'})
        else:
            conn.close()
            return jsonify({'status': 'error', 'pesan': pesan_error})

    @app.route('/arsip')
    def arsip_tugas():
        if 'loggedin' not in session: return redirect(url_for('login'))

        conn = get_db_connection()
        role, user_id = session['role'], session['id']

        query_base = '''
            SELECT t.*, m.nama_lengkap AS nama_member, r.nama_lengkap AS nama_reviewer
            FROM tugas t
            LEFT JOIN users m ON t.member_id = m.id
            LEFT JOIN users r ON t.reviewer_id = r.id
            WHERE t.status = "Done"
        '''

        if role in ['admin', 'superadmin']:
            tugas_arsip = conn.execute(query_base + ' ORDER BY t.id DESC').fetchall()
        elif role == 'member':
            tugas_arsip = conn.execute(query_base + ' AND t.member_id = ? ORDER BY t.id DESC', (user_id,)).fetchall()
        elif role == 'reviewer':
            tugas_arsip = conn.execute(query_base + ' AND t.reviewer_id = ? ORDER BY t.id DESC', (user_id,)).fetchall()

        conn.close()
        return render_template('arsip.html', tugas=tugas_arsip)

    @app.route('/ambil_tugas/<int:id>')
    def ambil_tugas(id):
        if 'loggedin' not in session or session['role'] != 'member':
            return redirect(url_for('login'))

        conn = get_db_connection()
        user_id = session['id']
        hari_ini = datetime.now().strftime('%Y-%m-%d %H:%M')

        # 1. SATPAM ANTI-DOSA (Cek apakah ada tugas dia yang udah lewat deadline tapi belum kelar)
        cek_telat = conn.execute('''
            SELECT COUNT(id) as jumlah_telat FROM tugas
            WHERE member_id = ? AND status != 'Done' AND deadline < ?
        ''', (user_id, hari_ini)).fetchone()

        if cek_telat['jumlah_telat'] > 0:
            conn.close()
            flash('Woy! Lu masih punya tugas yang LEWAT DEADLINE. Beresin dulu "dosa" lu baru ambil tugas baru!', 'danger')
            return redirect(url_for('dashboard'))

        # 2. SATPAM ANTI-RAKUS (Cek apakah dia udah pegang 2 tugas aktif)
        cek_aktif = conn.execute('''
            SELECT COUNT(id) as jumlah_aktif FROM tugas
            WHERE member_id = ? AND status != 'Done'
        ''', (user_id,)).fetchone()

        if cek_aktif['jumlah_aktif'] >= 2:
            conn.close()
            flash('Sabar bos! Maksimal pegang 2 tugas aktif bersamaan. Fokus beresin yang ada dulu!', 'warning')
            return redirect(url_for('dashboard'))

        # 3. KALAU LOLOS DUA SATPAM DI ATAS, BARU CEK KETERSEDIAAN TUGAS
        tugas = conn.execute('SELECT member_id FROM tugas WHERE id = ?', (id,)).fetchone()

        if tugas and tugas['member_id'] is None:
            conn.execute('UPDATE tugas SET member_id = ?, status = "In Progress" WHERE id = ?', (user_id, id))
            conn.commit()
            flash('Mantap! Tugas berhasil lu ambil. Jangan sampai telat ya!', 'success')
        else:
            flash('Yah! Telat sedetik, tugas ini udah diambil orang lain barusan.', 'danger')

        conn.close()
        return redirect(url_for('dashboard'))


    # ----------------------------------------
    # CREATE & UPDATE TUGAS (KHUSUS ADMIN)
    # ----------------------------------------
    @app.route('/tambah_tugas', methods=['GET', 'POST'])
    def tambah_tugas():
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            return redirect(url_for('dashboard'))

        conn = get_db_connection()

        if request.method == 'POST':
            member_id = request.form.get('member_id')
            if not member_id: member_id = None

            # --- SUNTIKAN PEMERSIH FORMAT JAM DEADLINE ---
            deadline_raw = request.form.get('deadline', '')
            deadline_bersih = deadline_raw.replace('T', ' ')

            # --- MESIN PENGHITUNG OTOMATIS (ANTI-DESIMAL + SUBSIDI MINIMUM 50K) ---
            harga_klien_raw = str(request.form.get('harga_klien', '0'))
            if ',' in harga_klien_raw:
                harga_klien_raw = harga_klien_raw.split(',')[0]
            if '.' in harga_klien_raw and len(harga_klien_raw.split('.')[-1]) == 1:
                harga_klien_raw = harga_klien_raw.split('.')[0]

            harga_klien_str = harga_klien_raw.replace('.', '').replace(',', '')
            harga_klien_int = int(harga_klien_str) if harga_klien_str.isdigit() else 0

            if harga_klien_int <= 50000:
                fee_admin = 0
                fee_tim = harga_klien_int
            else:
                fee_admin = round((harga_klien_int * 0.15) / 1000) * 1000
                fee_tim = harga_klien_int - fee_admin

            # PENANGANAN FILE ADMIN (LANGSUNG TEMBAK KE CLOUD)
            files_admin = request.files.getlist('file_admin')
            filenames_admin = []

            for f in files_admin:
                if f and f.filename != '':
                    filename = "ADMIN_" + secure_filename(f.filename)
                    path_lokal = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                    # 1. Simpan di server lokal bentar
                    f.save(path_lokal)

                    # 2. Langsung lempar ke Cloudinary
                    try:
                        link_cloud = pindah_ke_cloud(path_lokal, f"Bahan_Awal_{filename}")
                        if link_cloud:
                            filenames_admin.append(link_cloud) # Simpan link http-nya
                            os.remove(path_lokal) # 3. Hapus file aslinya dari server lu
                        else:
                            filenames_admin.append(filename) # Kalau gagal, tetap pakai nama lokal
                    except Exception as e:
                        print(f"Gagal lempar bahan admin ke cloud: {e}")
                        filenames_admin.append(filename)

            hasil_file_admin = ','.join(filenames_admin) if filenames_admin else None
            status_awal = "In Progress" if member_id else "To-Do"

            conn.execute('''
                INSERT INTO tugas (judul_tugas, deskripsi, deadline, harga_klien, fee_tim, fee_admin, member_id, reviewer_id, status, file_admin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (request.form['judul_tugas'], request.form['deskripsi'], deadline_bersih,
                  harga_klien_int, fee_tim, fee_admin, member_id, request.form['reviewer_id'], status_awal, hasil_file_admin))

            conn.commit()
            conn.close()

            # Notifikasi WhatsApp Rebutan
            if not member_id:
                judul = request.form['judul_tugas']
                deskripsi = request.form['deskripsi']

                pesan = f"[INFO] *TUGAS BARU MASUK!*\n\n> *Judul:* {judul}\n> *Deskripsi:* {deskripsi}\n> *Deadline Jam:* {deadline_bersih}\n\n *Gass login ke web buat ambil tugasnya*\n\n https://...com"
                pesan_url = urllib.parse.quote(pesan)
                link_wa = f"https://wa.me/?text={pesan_url}"

                flash(link_wa, 'wa_ready')
            else:
                flash('Tugas berhasil di-assign langsung ke member!', 'success')

            return redirect(url_for('dashboard'))

        members = conn.execute("SELECT id, nama_lengkap FROM users WHERE role = 'member'").fetchall()
        reviewers = conn.execute("SELECT id, nama_lengkap FROM users WHERE role = 'reviewer'").fetchall()
        conn.close()

        return render_template('tambah_tugas.html', members=members, reviewers=reviewers)

    @app.route('/edit_tugas/<int:id>', methods=['GET', 'POST'])
    def edit_tugas(id):
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            return redirect(url_for('dashboard'))

        conn = get_db_connection()
        tugas = conn.execute('SELECT * FROM tugas WHERE id = ?', (id,)).fetchone()

        if request.method == 'POST':
            status_baru = request.form['status']
            member_id = request.form.get('member_id')
            if not member_id: member_id = None

            # --- SUNTIKAN PEMERSIH FORMAT JAM DEADLINE ---
            deadline_raw = request.form.get('deadline', '')
            deadline_bersih = deadline_raw.replace('T', ' ')

            # --- MESIN PENGHITUNG OTOMATIS (ANTI-DESIMAL + SUBSIDI MINIMUM 50K) ---
            harga_klien_raw = str(request.form.get('harga_klien', '0'))
            if ',' in harga_klien_raw:
                harga_klien_raw = harga_klien_raw.split(',')[0]
            if '.' in harga_klien_raw and len(harga_klien_raw.split('.')[-1]) == 1:
                harga_klien_raw = harga_klien_raw.split('.')[0]

            harga_klien_str = harga_klien_raw.replace('.', '').replace(',', '')
            harga_klien_int = int(harga_klien_str) if harga_klien_str.isdigit() else 0

            # Penerapan Logika Subsidi 50k
            if harga_klien_int <= 50000:
                fee_admin = 0
                fee_tim = harga_klien_int
            else:
                fee_admin = round((harga_klien_int * 0.15) / 1000) * 1000
                fee_tim = harga_klien_int - fee_admin

            # Penanganan File Admin
            files_admin = request.files.getlist('file_admin')
            filenames_admin = []

            for f in files_admin:
                if f and f.filename != '':
                    filename = "ADMIN_EDIT_" + secure_filename(f.filename)
                    path_lokal = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                    # 1. Simpan sementara di server
                    f.save(path_lokal)

                    # 2. Langsung lempar ke Cloudinary
                    try:
                        link_cloud = pindah_ke_cloud(path_lokal, f"Bahan_Revisi_{filename}")
                        if link_cloud:
                            filenames_admin.append(link_cloud) # Simpan link http-nya
                            os.remove(path_lokal) # 3. Hapus file aslinya dari server
                        else:
                            filenames_admin.append(filename) # Kalau gagal, tetap pakai nama lokal
                    except Exception as e:
                        print(f"Gagal lempar bahan admin ke cloud: {e}")
                        filenames_admin.append(filename)

            # --- PROSES SIMPAN KE DATABASE ---
            if len(filenames_admin) > 0:
                hasil_file_admin = ','.join(filenames_admin)
                conn.execute('''
                    UPDATE tugas SET
                        judul_tugas=?, deskripsi=?, deadline=?, status=?, member_id=?,
                        reviewer_id=?, file_admin=?, harga_klien=?, fee_tim=?, fee_admin=?
                    WHERE id = ?
                ''', (request.form['judul_tugas'], request.form['deskripsi'], deadline_bersih,
                      status_baru, member_id, request.form['reviewer_id'], hasil_file_admin,
                      harga_klien_int, fee_tim, fee_admin, id))
            else:
                conn.execute('''
                    UPDATE tugas SET
                        judul_tugas=?, deskripsi=?, deadline=?, status=?, member_id=?,
                        reviewer_id=?, harga_klien=?, fee_tim=?, fee_admin=?
                    WHERE id = ?
                ''', (request.form['judul_tugas'], request.form['deskripsi'], deadline_bersih,
                      status_baru, member_id, request.form['reviewer_id'],
                      harga_klien_int, fee_tim, fee_admin, id))

            conn.commit()

            # Jalankan pencatatan keuangan & cloud backup jika status Done
            if status_baru == 'Done':
                catat_kas_otomatis(id)

                # EKSEKUSI CLOUD ADAPTER
                try:
                    latest_sub = conn.execute('SELECT id, file_path FROM submissions WHERE tugas_id = ? ORDER BY versi DESC LIMIT 1', (id,)).fetchone()
                    if latest_sub and latest_sub['file_path']:
                        daftar_file = latest_sub['file_path'].split(',')
                        daftar_link_cloud = []

                        for f_name in daftar_file:
                            if not f_name.startswith('http'):
                                path_lokal = os.path.join(app.config['UPLOAD_FOLDER'], f_name)
                                if os.path.exists(path_lokal):
                                    link_cloud = pindah_ke_cloud(path_lokal, f"Selesai_{id}_{f_name}")
                                    if link_cloud:
                                        daftar_link_cloud.append(link_cloud)
                                        os.remove(path_lokal)
                                    else:
                                        daftar_link_cloud.append(f_name)
                                else:
                                    daftar_link_cloud.append(f_name)
                            else:
                                daftar_link_cloud.append(f_name)

                        if daftar_link_cloud:
                            hasil_link = ','.join(daftar_link_cloud)
                            conn.execute('UPDATE submissions SET file_path = ? WHERE id = ?', (hasil_link, latest_sub['id']))
                            conn.commit()
                except Exception as e:
                    print(f"Error Cloud Backup di edit_tugas: {e}")

            conn.close()
            # RESPON BERHASIL DIREKSTRUKTUR: Harus di dalam blok POST biar Javascript Fetch nerima data JSON ini
            return jsonify({"status": "success", "message": "Mantap bos! Perubahan tugas berhasil disimpan secara real-time."})

        # --- JALUR GET (NAMPILIN HALAMAN FORM) ---
        members = conn.execute("SELECT id, nama_lengkap FROM users WHERE role = 'member'").fetchall()
        reviewers = conn.execute("SELECT id, nama_lengkap FROM users WHERE role = 'reviewer'").fetchall()
        conn.close()

        return render_template('edit_tugas.html', tugas=tugas, members=members, reviewers=reviewers)

    @app.route('/hapus_tugas/<int:id>')
    def hapus_tugas(id):
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return redirect(url_for('dashboard'))

        catat_log(session['id'], f"Menghapus tugas ID: {id}")

        conn = get_db_connection()
        conn.execute('DELETE FROM tugas WHERE id = ?', (id,))
        conn.execute('DELETE FROM cash_flow WHERE tugas_id = ?', (id,))
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard'))


    # ----------------------------------------
    # DETAIL TUGAS & DISKUSI
    # ----------------------------------------
    @app.route('/tugas/<int:id>', methods=['GET', 'POST'])
    def detail_tugas(id):
        if 'loggedin' not in session: return redirect(url_for('login'))

        conn = get_db_connection()

        if request.method == 'POST':
            aksi = request.form.get('aksi')

            # --- JALUR MEMBER (UPLOAD FILE) ---
            if aksi == 'upload':
                files = request.files.getlist('file_tugas')
                daftar_nama_file = []

                for file in files:
                    if file and file.filename != '' and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        path_lokal = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                        # 1. Simpan file sementara di server
                        file.save(path_lokal)

                        # 2. LANGSUNG LEMPAR KE CLOUD
                        try:
                            link_cloud = pindah_ke_cloud(path_lokal, f"Tugas_{id}_{filename}")
                            if link_cloud:
                                daftar_nama_file.append(link_cloud)
                                os.remove(path_lokal) # Hapus dari server lokal
                            else:
                                daftar_nama_file.append(filename)
                        except Exception as e:
                            print(f"Gagal lempar file tugas ke cloud: {e}")
                            daftar_nama_file.append(filename)

                if daftar_nama_file:
                    hasil_file = ','.join(daftar_nama_file)
                    conn.execute('''
                        INSERT INTO submissions (tugas_id, file_path, versi, catatan_member)
                        VALUES (?, ?, (SELECT COUNT(*)+1 FROM submissions WHERE tugas_id=?), ?)
                    ''', (id, hasil_file, id, request.form.get('catatan')))

                    conn.execute("UPDATE tugas SET status = 'Ready for Review' WHERE id = ?", (id,))
                    conn.commit()

                    catat_log(session['id'], f"Member upload {len(daftar_nama_file)} file tugas: {hasil_file}")
                    flash(f'{len(daftar_nama_file)} File berhasil dikirim dan di-backup ke Cloud bos!', 'success')
                else:
                    flash('Woy! File dilarang atau kosong! Cuma boleh PDF, Office, ZIP, atau Gambar.', 'danger')

            # --- JALUR REVIEWER (KASIH NILAI) ---
            elif aksi == 'review':
                tugas_cek = conn.execute('SELECT status FROM tugas WHERE id = ?', (id,)).fetchone()

                if tugas_cek['status'] == 'Done':
                    conn.close()
                    return "Akses Ditolak: Tugas sudah Done."

                if tugas_cek['status'] == 'Revision Needed':
                    conn.close()
                    return "Akses Ditolak: Tunggu member upload versi terbaru dulu bos!"

                hasil = request.form['hasil']
                conn.execute('INSERT INTO reviews (submission_id, komentar, hasil) VALUES (?, ?, ?)',
                             (request.form['submission_id'], request.form['komentar'], hasil))

                status_baru = 'Done' if hasil == 'Approved' else 'Revision Needed'
                conn.execute('UPDATE tugas SET status = ? WHERE id = ?', (status_baru, id))
                conn.commit()

                catat_log(session['id'], f"Memberikan review: {hasil} pada tugas ID {id}")

                if hasil == 'Approved':
                    catat_kas_otomatis(id)

                    # EKSEKUSI CLOUD ADAPTER
                    try:
                        latest_sub = conn.execute('SELECT id, file_path FROM submissions WHERE tugas_id = ? ORDER BY versi DESC LIMIT 1', (id,)).fetchone()
                        if latest_sub and latest_sub['file_path']:
                            daftar_file = latest_sub['file_path'].split(',')
                            daftar_link_cloud = []

                            for f_name in daftar_file:
                                if not f_name.startswith('http'):
                                    path_lokal = os.path.join(app.config['UPLOAD_FOLDER'], f_name)
                                    if os.path.exists(path_lokal):
                                        link_cloud = pindah_ke_cloud(path_lokal, f"Selesai_{id}_{f_name}")
                                        if link_cloud:
                                            daftar_link_cloud.append(link_cloud)
                                            os.remove(path_lokal) # Hapus dari server lokal
                                        else:
                                            daftar_link_cloud.append(f_name)
                                    else:
                                        daftar_link_cloud.append(f_name)
                                else:
                                    daftar_link_cloud.append(f_name)

                            # Timpa DB dengan URL hasil Cloud Backup
                            if daftar_link_cloud:
                                hasil_link = ','.join(daftar_link_cloud)
                                conn.execute('UPDATE submissions SET file_path = ? WHERE id = ?', (hasil_link, latest_sub['id']))
                                conn.commit()
                    except Exception as e:
                        print(f"Error Cloud Backup di detail_tugas: {e}")

            # --- JALUR DISKUSI / KOMENTAR ---
            elif aksi == 'komentar':
                pesan = request.form.get('pesan')
                if pesan:
                    conn.execute('INSERT INTO diskusi (tugas_id, user_id, pesan) VALUES (?, ?, ?)', (id, session['id'], pesan))
                    conn.commit()
                    catat_log(session['id'], f"Mengirim pesan diskusi di tugas ID {id}")
                    flash('Pesan berhasil dikirim!', 'success')

            conn.close()
            return redirect(url_for('detail_tugas', id=id))

        # Pengambilan Data GET
        tugas = conn.execute('''
            SELECT t.*, m.nama_lengkap AS nama_member, r.nama_lengkap AS nama_reviewer
            FROM tugas t
            LEFT JOIN users m ON t.member_id = m.id
            LEFT JOIN users r ON t.reviewer_id = r.id
            WHERE t.id = ?
        ''', (id,)).fetchone()

        submissions = conn.execute('SELECT * FROM submissions WHERE tugas_id = ? ORDER BY versi DESC', (id,)).fetchall()
        reviews = conn.execute('''
            SELECT r.*, s.versi FROM reviews r
            JOIN submissions s ON r.submission_id = s.id
            WHERE s.tugas_id = ? ORDER BY r.created_at DESC
        ''', (id,)).fetchall()

        diskusi = conn.execute('''
            SELECT d.*, u.nama_lengkap, u.role, datetime(d.waktu, '+7 hours') as waktu_indo
            FROM diskusi d
            JOIN users u ON d.user_id = u.id
            WHERE d.tugas_id = ?
            ORDER BY d.waktu ASC
        ''', (id,)).fetchall()

        conn.close()
        return render_template('detail_tugas.html', tugas=tugas, submissions=submissions, reviews=reviews, diskusi=diskusi)


    # ----------------------------------------
    # MANAJEMEN PENGGUNA (USERS)
    # ----------------------------------------
    @app.route('/users', methods=['GET', 'POST'])
    def kelola_user():
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            return redirect(url_for('dashboard'))

        conn = get_db_connection()

        if request.method == 'POST':
            role_input = request.form['role']
            if session['role'] == 'admin' and role_input in ['admin', 'superadmin']:
                conn.close()
                return "Woy! Lu admin biasa brow, dilarang keras nyiptain akun bos! Wkwkwk."

            role_db = 'admin' if role_input == 'superadmin' else role_input

            try:
                conn.execute('INSERT INTO users (username, password, nama_lengkap, role) VALUES (?, ?, ?, ?)',
                             (request.form['username'], generate_password_hash(request.form['password']), request.form['nama_lengkap'], role_db))
                conn.commit()
            except:
                return "Username sudah ada!"

        users_list = conn.execute('SELECT * FROM users').fetchall()
        conn.close()
        return render_template('kelola_user.html', users=users_list)

    @app.route('/edit_user/<int:id>', methods=['GET', 'POST'])
    def edit_user(id):
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            return redirect(url_for('dashboard'))

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE id = ?', (id,)).fetchone()

        if request.method == 'POST':
            password_baru = request.form['password']
            role_input = request.form['role']
            role_db = 'admin' if role_input == 'superadmin' else role_input

            if password_baru:
                conn.execute('UPDATE users SET nama_lengkap=?, username=?, role=?, password=? WHERE id=?',
                             (request.form['nama_lengkap'], request.form['username'], role_db, generate_password_hash(password_baru), id))
            else:
                conn.execute('UPDATE users SET nama_lengkap=?, username=?, role=? WHERE id=?',
                             (request.form['nama_lengkap'], request.form['username'], role_db, id))

            conn.commit()
            conn.close()
            return redirect(url_for('kelola_user'))

        conn.close()
        return render_template('edit_user.html', user=user)

    @app.route('/hapus_user/<int:id>')
    def hapus_user(id):
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            return redirect(url_for('dashboard'))
        if id == session['id']:
            return "Bos, lu gak bisa ngapus akun lu sendiri!"

        conn = get_db_connection()
        conn.execute('DELETE FROM users WHERE id = ?', (id,))
        conn.commit()
        conn.close()
        return redirect(url_for('kelola_user'))

    @app.route('/profil', methods=['GET', 'POST'])
    def profil():
        if 'loggedin' not in session: return redirect(url_for('login'))

        conn = get_db_connection()
        user_id = session['id']
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

        if request.method == 'POST':
            nama_baru = request.form['nama_lengkap']
            pw_baru = request.form['password_baru']
            file_foto = request.files.get('foto')

            try:
                path_foto_db = user['foto']
            except:
                path_foto_db = None

            # PROSES UPLOAD FOTO
            if file_foto and file_foto.filename != '' and allowed_file(file_foto.filename):
                ext = file_foto.filename.rsplit('.', 1)[1].lower()
                nama_file_baru = f"avatar_{user['username']}.{ext}"

                folder_profil = os.path.join(app.root_path, 'static', 'uploads', 'profile')
                os.makedirs(folder_profil, exist_ok=True)
                file_foto.save(os.path.join(folder_profil, nama_file_baru))
                path_foto_db = f"/static/uploads/profile/{nama_file_baru}"

            # EKSEKUSI UPDATE DATABASE
            if pw_baru:
                hashed_pw = generate_password_hash(pw_baru)
                try:
                    conn.execute('UPDATE users SET nama_lengkap = ?, password = ?, foto = ? WHERE id = ?', (nama_baru, hashed_pw, path_foto_db, user_id))
                except Exception as e:
                    print(f"Warning: Gagal update foto. {e}")
                    conn.execute('UPDATE users SET nama_lengkap = ?, password = ? WHERE id = ?', (nama_baru, hashed_pw, user_id))
            else:
                try:
                    conn.execute('UPDATE users SET nama_lengkap = ?, foto = ? WHERE id = ?', (nama_baru, path_foto_db, user_id))
                except Exception as e:
                    print(f"Warning: Gagal update foto. {e}")
                    conn.execute('UPDATE users SET nama_lengkap = ? WHERE id = ?', (nama_baru, user_id))

            conn.commit()
            session['nama_lengkap'] = nama_baru
            session['foto'] = path_foto_db
            conn.close()

            return redirect(url_for('profil', success=True))

        conn.close()
        return render_template('profil.html', user=user, success=request.args.get('success'))

    @app.route('/leaderboard')
    def leaderboard():
        if 'loggedin' not in session:
            return redirect(url_for('login'))

        conn = get_db_connection()

        peringkat = conn.execute('''
            SELECT u.id, u.nama_lengkap, u.foto, COUNT(t.id) as total_tugas
            FROM users u
            LEFT JOIN tugas t ON u.id = t.member_id AND t.status = 'Done'
            WHERE u.role = 'member'
            GROUP BY u.id
            ORDER BY total_tugas DESC
        ''').fetchall()

        # PERHATIKAN SPASINYA! Harus sejajar sama "peringkat =" di atasnya
        conn.close()
        return render_template('leaderboard.html', peringkat=peringkat)

    # ----------------------------------------
    # KEUANGAN & LAPORAN
    # ----------------------------------------
    @app.route('/keuangan', methods=['GET', 'POST'])
    def keuangan():
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            return redirect(url_for('dashboard'))

        conn = get_db_connection()

        if request.method == 'POST':
            conn.execute('INSERT INTO cash_flow (tipe, nominal, keterangan) VALUES (?, ?, ?)',
                         (request.form['tipe'], request.form['nominal'], request.form['keterangan']))
            conn.commit()

        pemasukan = conn.execute("SELECT SUM(nominal) as total FROM cash_flow WHERE tipe = 'Pemasukan'").fetchone()['total'] or 0
        pengeluaran = conn.execute("SELECT SUM(nominal) as total FROM cash_flow WHERE tipe = 'Pengeluaran'").fetchone()['total'] or 0
        transaksi = conn.execute('SELECT * FROM cash_flow ORDER BY tanggal DESC').fetchall()
        conn.close()

        return render_template('keuangan.html',
                               total_pemasukan=pemasukan,
                               total_pengeluaran=pengeluaran,
                               saldo_bersih=pemasukan-pengeluaran,
                               transaksi=transaksi)

    @app.route('/laporan')
    def laporan():
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            flash('Lu nggak punya akses ke ruang laporan bos!', 'danger')
            return redirect(url_for('dashboard'))

        conn = get_db_connection()
        members = conn.execute("SELECT id, username, nama_lengkap FROM users WHERE role = 'member'").fetchall()
        conn.close()

        return render_template('laporan.html', members=members)

    @app.route('/slip_gaji')
    def slip_gaji():
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            return redirect(url_for('dashboard'))

        member_id = request.args.get('member_id')
        if not member_id:
            flash('Pilih member dulu bos!', 'warning')
            return redirect(url_for('laporan'))

        conn = get_db_connection()
        member = conn.execute("SELECT id, nama_lengkap, username FROM users WHERE id = ?", (member_id,)).fetchone()

        tugas_selesai = conn.execute('''
            SELECT id, judul_tugas, deadline, fee_tim
            FROM tugas
            WHERE member_id = ? AND status = 'Done'
            ORDER BY id DESC
        ''', (member_id,)).fetchall()

        total_gaji = sum([t['fee_tim'] for t in tugas_selesai]) if tugas_selesai else 0
        conn.close()

        if not member:
            flash('Member ga ketemu, database error!', 'danger')
            return redirect(url_for('laporan'))

        return render_template('slip_gaji.html', member=member, tugas_selesai=tugas_selesai, total_gaji=total_gaji)

    @app.route('/laporan_pdf')
    def laporan_pdf():
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            flash('Akses ditolak bos!', 'danger')
            return redirect(url_for('dashboard'))

        conn = get_db_connection()
        tugas_selesai = conn.execute('''
            SELECT judul_tugas, harga_klien, fee_tim, fee_admin, deadline
            FROM tugas WHERE status = 'Done' ORDER BY id DESC
        ''').fetchall()
        conn.close()

        total_pemasukan = sum([t['harga_klien'] for t in tugas_selesai]) if tugas_selesai else 0
        total_pengeluaran = sum([t['fee_tim'] for t in tugas_selesai]) if tugas_selesai else 0
        laba_bersih = sum([t['fee_admin'] for t in tugas_selesai]) if tugas_selesai else 0

        label_grafik = [t['judul_tugas'][:15] + '...' for t in tugas_selesai[:5]]
        data_masuk = [t['harga_klien'] for t in tugas_selesai[:5]]
        data_keluar = [t['fee_tim'] for t in tugas_selesai[:5]]

        return render_template('laporan_pdf.html',
                               tugas=tugas_selesai,
                               tot_masuk=total_pemasukan,
                               tot_keluar=total_pengeluaran,
                               laba=laba_bersih,
                               labels=label_grafik,
                               grafik_masuk=data_masuk,
                               grafik_keluar=data_keluar)

    @app.route('/laporan_performa')
    def laporan_performa():
        # Cuma petinggi yang boleh ngintip rahasia dapur ini
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            flash('Akses ditolak! Cuma Admin & Superadmin yang bisa lihat performa tim.', 'danger')
            return redirect(url_for('dashboard'))

        conn = get_db_connection()

        # Query sakti buat ngerangkum data tiap member dalam 1 tarikan napas
        performa_member = conn.execute('''
            SELECT
                u.id,
                u.nama_lengkap,
                u.username,
                u.foto,
                COUNT(t.id) as total_tugas,
                SUM(CASE WHEN t.status = 'Done' THEN 1 ELSE 0 END) as tugas_selesai,
                SUM(CASE WHEN t.status NOT IN ('Done', 'To-Do') THEN 1 ELSE 0 END) as tugas_aktif,
                SUM(CASE WHEN t.status = 'Done' THEN t.fee_tim ELSE 0 END) as total_pendapatan
            FROM users u
            LEFT JOIN tugas t ON u.id = t.member_id
            WHERE u.role = 'member'
            GROUP BY u.id
            ORDER BY tugas_selesai DESC, total_pendapatan DESC
        ''').fetchall()

        conn.close()

        return render_template('laporan_performa.html', performa=performa_member)

    @app.route('/export_keuangan')
    def export_keuangan():
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            flash('Lu nggak punya akses buat cetak laporan bos!', 'danger')
            return redirect(url_for('dashboard'))

        conn = get_db_connection()
        transaksi = conn.execute('SELECT tanggal, tipe, keterangan, nominal FROM cash_flow ORDER BY tanggal DESC').fetchall()
        conn.close()

        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['Tanggal', 'Tipe Transaksi', 'Keterangan', 'Nominal (Rp)'])

        for tr in transaksi:
            cw.writerow([tr['tanggal'], tr['tipe'], tr['keterangan'], tr['nominal']])

        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=Laporan_Keuangan_Skripsikuu.csv"
        output.headers["Content-type"] = "text/csv"
        return output


    # ----------------------------------------
    #️ SISTEM BANNER & ASET
    # ----------------------------------------
    @app.route('/kelola_banner', methods=['GET', 'POST'])
    def kelola_banner():
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            flash('Lu bukan admin bos!', 'danger')
            return redirect(url_for('dashboard'))

        banner_folder = os.path.join(app.root_path, 'static', 'banners')

        if request.method == 'POST':
            if 'banner_img' not in request.files:
                flash('Pilih gambar dulu bos!', 'warning')
                return redirect(request.url)

            file = request.files['banner_img']
            if file.filename == '' or not allowed_file(file.filename):
                flash('Pilih file gambar yang bener bos (PNG/JPG)!', 'warning')
                return redirect(request.url)

            if file:
                filename = secure_filename(file.filename)
                if not os.path.exists(banner_folder):
                    os.makedirs(banner_folder)

                temp_path = os.path.join(banner_folder, "TEMP_" + filename)
                final_path = os.path.join(banner_folder, filename)

                file.save(temp_path)

                # EKSEKUSI OPERASI SESAR (KOMPRESI)
                with Image.open(temp_path) as img:
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    if img.width > 1920:
                        perbandingan = 1920 / float(img.width)
                        tinggi_baru = int((float(img.height) * float(perbandingan)))
                        img = img.resize((1920, tinggi_baru), Image.LANCZOS)
                    img.save(final_path, optimize=True, quality=60)

                os.remove(temp_path)

                catat_log(session['id'], f"Admin upload & kompres banner: {filename}")
                flash('Banner sukses mengudara dengan ukuran super enteng!', 'success')
                return redirect(url_for('kelola_banner'))

        return render_template('kelola_banner.html')

    @app.route('/hapus_banner/<nama_file>')
    def hapus_banner(nama_file):
        if 'loggedin' not in session or session['role'] not in ['admin', 'superadmin']:
            return redirect(url_for('dashboard'))

        file_path = os.path.join(app.root_path, 'static', 'banners', nama_file)
        if os.path.exists(file_path):
            os.remove(file_path)
            flash('Banner berhasil diturunkan!', 'success')

        return redirect(url_for('kelola_banner'))

    @app.route('/download/<filename>')
    def download_file(filename):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


    # ----------------------------------------
    # MENU SAKTI SUPERADMIN
    # ----------------------------------------
    @app.route('/settings')
    def settings_panel():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Woy! Mau ngapain lu? Jalur ini cuma buat Bos Besar!", 403

        # TARIK DATA KESEHATAN SERVER (Penyimpanan Disk & Info OS)
        total, used, free = shutil.disk_usage("/")
        disk_percent = round((used / total) * 100, 1)

        server_info = {
            'os': platform.system() + " " + platform.release(),
            'python_version': platform.python_version(),
            'disk_total': round(total / (1024**3), 2),
            'disk_used': round(used / (1024**3), 2),
            'disk_percent': disk_percent
        }

        return render_template('settings.html', server=server_info)

    @app.route('/sinkron_keuangan')
    def sinkron_keuangan():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Woy! Cuma Bos Besar yang boleh ke sini!", 403

        conn = get_db_connection()
        tugas_selesai = conn.execute("SELECT * FROM tugas WHERE status = 'Done'").fetchall()
        conn.execute("DELETE FROM cash_flow WHERE tugas_id IS NOT NULL OR keterangan LIKE '%Pelunasan:%' OR keterangan LIKE '%Fee %'")

        for t in tugas_selesai:
            id_tugas = t['id']
            harga_klien = t['harga_klien']

            if harga_klien <= 50000:
                fee_admin_baru = 0
                fee_tim_baru = harga_klien
            else:
                fee_admin_baru = round((harga_klien * 0.15) / 1000) * 1000
                fee_tim_baru = harga_klien - fee_admin_baru

            conn.execute('UPDATE tugas SET fee_tim = ?, fee_admin = ? WHERE id = ?', (fee_tim_baru, fee_admin_baru, id_tugas))

            conn.execute('INSERT INTO cash_flow (tugas_id, tipe, nominal, keterangan) VALUES (?, "Pemasukan", ?, ?)',
                         (id_tugas, harga_klien, f"Pelunasan: {t['judul_tugas']}"))

            if fee_tim_baru > 0:
                conn.execute('INSERT INTO cash_flow (tugas_id, tipe, nominal, keterangan) VALUES (?, "Pengeluaran", ?, ?)',
                             (id_tugas, fee_tim_baru, f"Fee Tim (Pengerja): {t['judul_tugas']}"))

        conn.commit()
        conn.close()
        return "SINKRONISASI & PERBAIKAN SUKSES"

    @app.route('/reset_keuangan', methods=['POST'])
    def reset_keuangan():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return redirect(url_for('dashboard'))

        conn = get_db_connection()
        conn.execute('DELETE FROM cash_flow')
        conn.commit()
        conn.close()
        return redirect(url_for('keuangan'))

    @app.route('/install_diskusi')
    def install_diskusi():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Cuma Bos Besar yang boleh install database!", 403

        conn = get_db_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS diskusi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tugas_id INTEGER,
                user_id INTEGER,
                pesan TEXT,
                waktu TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        return "MANTAP BOS! Tabel Diskusi Berhasil Dibuat. Silakan kembali ke halaman utama."

    @app.route('/install_crm')
    def install_crm():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Akses Ditolak. Otorisasi Superadmin Diperlukan.", 403

        conn = get_db_connection()

        # 1. Tabel Klien (Tanpa sandi untuk keamanan arsitektur Magic Link)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                nama_lengkap TEXT NOT NULL,
                waktu_daftar TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 2. Tabel Token Autentikasi (Penyimpanan token sekali pakai)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS magic_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                kadaluarsa TIMESTAMP NOT NULL,
                status_pakai INTEGER DEFAULT 0
            )
        ''')

        # 3. Tabel Pesanan (Karantina pre-tugas sebelum kesepakatan harga)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                judul_pesanan TEXT NOT NULL,
                deskripsi TEXT NOT NULL,
                file_pendukung TEXT,
                harga_deal INTEGER DEFAULT 0,
                status_order TEXT DEFAULT 'Negosiasi',
                waktu_order TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(client_id) REFERENCES clients(id)
            )
        ''')

        conn.commit()
        conn.close()

        return "Migrasi Basis Data CRM Berhasil Dieksekusi. Tabel clients, magic_links, dan orders telah beroperasi."

    @app.route('/secret28')
    def intip_db():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Woy! Mau ngapain lu? Jalur ini cuma buat Bos Besar!", 403

        conn = get_db_connection()
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_name = request.args.get('table', 'users')
        list_tabel_valid = [t['name'] for t in tables]

        if table_name not in list_tabel_valid:
            table_name = 'users'

        data = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        cursor = conn.execute(f"SELECT * FROM {table_name}")
        columns = [description[0] for description in cursor.description]
        conn.close()

        return render_template('intip_db.html', tables=tables, data=data, table_name=table_name, columns=columns)

    @app.route('/activity_log')
    def view_logs():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Woy! Cuma Bos Besar yang boleh liat CCTV!", 403

        conn = get_db_connection()
        logs = conn.execute('''
            SELECT l.id, l.user_id, l.aksi, datetime(l.waktu, '+7 hours') as waktu,
                   u.username, u.nama_lengkap, u.role
            FROM logs l
            JOIN users u ON l.user_id = u.id
            ORDER BY l.waktu DESC LIMIT 100
        ''').fetchall()
        conn.close()
        return render_template('activity_log.html', logs=logs)

    @app.route('/system_maintenance')
    def system_maintenance():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Akses Ditolak! Area ini khusus (God Mode).", 403

        total, used, free = shutil.disk_usage("/")
        disk_percent = round((used / total) * 100, 1)

        server_info = {
            'os': platform.system() + " " + platform.release(),
            'python_version': platform.python_version(),
            'disk_total': round(total / (1024**3), 2),
            'disk_used': round(used / (1024**3), 2),
            'disk_percent': disk_percent
        }

        return render_template('maintenance.html', server=server_info)

    @app.route('/backup_db')
    def backup_db():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Akses Ditolak!", 403

        db_path = os.path.join(app.root_path, 'dashboard_tugas.db')
        if os.path.exists(db_path):
            return send_file(db_path, as_attachment=True)
        return "Database tidak ditemukan!", 404

    @app.route('/restore_db', methods=['POST'])
    def restore_db():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Akses Ditolak!", 403

        if 'db_file' not in request.files:
            flash('Gagal! File DB tidak ditemukan.', 'danger')
            return redirect(url_for('settings_panel'))

        file = request.files['db_file']
        if file.filename != '' and file.filename.endswith('.db'):
            db_path = os.path.join(app.root_path, 'database.db') # Ganti sesuai nama DB lu
            file.save(db_path)
            flash('Mantap! Database berhasil di-restore.', 'success')
        else:
            flash('Format salah! Harus file .db', 'danger')

        return redirect(url_for('settings_panel'))

    @app.route('/error_log')
    def error_log():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Akses Ditolak!", 403

        log_content = "Aman! Tidak ada error log yang ditemukan."

        # Path log standar PythonAnywhere (ganti 'skripsikuu' dengan username PA lu kalau beda)
        log_paths = ['/var/log/skripsikuu_pythonanywhere_com_error.log', 'error.log']

        for path in log_paths:
            if os.path.exists(path):
                with open(path, 'r') as f:
                    # Cuma narik 100 baris terakhir biar server gak lemot
                    lines = f.readlines()

                log_content = "".join(lines[-100:])
                break

        return render_template('error_log.html', log_content=log_content)

    @app.route('/sweep_garbage')
    def sweep_garbage():
        if 'loggedin' not in session or session['role'] != 'superadmin':
            return "Akses Ditolak!", 403

        conn = get_db_connection()
        users = conn.execute('SELECT foto FROM users WHERE foto IS NOT NULL').fetchall()
        conn.close()

        # Ambil nama file foto yang MASIH DIPAKAI sama user di database
        foto_aktif = [u['foto'].split('/')[-1] for u in users]
        folder_profil = os.path.join(app.root_path, 'static', 'uploads', 'profile')

        file_terhapus = 0
        if os.path.exists(folder_profil):
            for file_path in glob.glob(os.path.join(folder_profil, '*')):
                nama_file = os.path.basename(file_path)

                if nama_file not in foto_aktif and nama_file != 'default_avatar.png':
                    os.remove(file_path)
                    file_terhapus += 1

        flash(f'Sweeper Beraksi! {file_terhapus} file sampah berhasil dihapus.', 'success')
        return redirect(url_for('settings_panel'))

    # ----------------------------------------
    # API, PWA & ERROR HANDLERS
    # ----------------------------------------
    @app.route('/api/tugas_tersedia')
    def api_tugas_tersedia():
        if 'loggedin' not in session or session['role'] != 'member':
            return jsonify([])

        conn = get_db_connection()
        tugas_kosong = conn.execute('SELECT id, judul_tugas, deskripsi, deadline FROM tugas WHERE member_id IS NULL AND status != "Done" ORDER BY deadline ASC').fetchall()
        conn.close()

        hasil = [dict(t) for t in tugas_kosong]
        return jsonify(hasil)

    @app.route('/sw.js')
    def sw():
        response = make_response(send_from_directory('static', 'sw.js'))
        response.headers['Content-Type'] = 'application/javascript'
        return response

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(429)
    def ratelimit_handler(e):
        flash('Woy santai bos! Lu nyoba login terlalu brutal. Tunggu sejam lagi ya baru coba lagi.', 'danger')
        return redirect(url_for('login'))