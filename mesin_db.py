import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_db_connection():
    db_path = os.path.join(BASE_DIR, 'dashboard_tugas.db')
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def catat_kas_otomatis(tugas_id):
    conn = get_db_connection()
    sudah_ada = conn.execute('SELECT id FROM cash_flow WHERE tugas_id = ?', (tugas_id,)).fetchone()
    if not sudah_ada:
        tugas = conn.execute('SELECT * FROM tugas WHERE id = ?', (tugas_id,)).fetchone()

        # 1. CATAT UANG MASUK DARI KLIEN
        conn.execute('INSERT INTO cash_flow (tugas_id, tipe, nominal, keterangan) VALUES (?, "Pemasukan", ?, ?)',
                     (tugas_id, tugas['harga_klien'], f"Pelunasan: {tugas['judul_tugas']}"))

        # 2. CATAT UANG KELUAR BUAT GAJI TIM
        if tugas['fee_tim'] and int(tugas['fee_tim']) > 0:
            conn.execute('INSERT INTO cash_flow (tugas_id, tipe, nominal, keterangan) VALUES (?, "Pengeluaran", ?, ?)',
                         (tugas_id, tugas['fee_tim'], f"Fee Tim (Pengerja): {tugas['judul_tugas']}"))

        #PENCATATAN FEE ADMIN DIHAPUS DARI SINI BIAR GAK JADI PENGELUARAN
        conn.commit()
    conn.close()

def catat_log(user_id, aksi):
    conn = get_db_connection()
    conn.execute('INSERT INTO logs (user_id, aksi) VALUES (?, ?)', (user_id, aksi))
    conn.commit()
    conn.close()