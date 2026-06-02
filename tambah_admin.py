import sqlite3
from werkzeug.security import generate_password_hash

def tambah_admin():
    conn = sqlite3.connect('dashboard_tugas.db')
    cursor = conn.cursor()

    # Data admin pertama lu
    username_admin = 'mey'
    password_admin = 'iqmacantik123'
    nama_lengkap = 'Iqma Yuliasari'
    role = 'admin'

    # Mengacak password biar aman
    hashed_password = generate_password_hash(password_admin)

    try:
        cursor.execute("INSERT INTO users (username, password, nama_lengkap, role) VALUES (?, ?, ?, ?)",
                       (username_admin, hashed_password, nama_lengkap, role))
        conn.commit()
        print("Berhasil! Akun admin udah masuk ke database.")
    except sqlite3.IntegrityError:
        print("Gagal: Username 'admin' sudah ada di database.")
    
    conn.close()

if __name__ == '__main__':
    tambah_admin()