import sqlite3

def init_database():
    # Menghubungkan ke file database (akan dibuat otomatis jika belum ada)
    conn = sqlite3.connect('dashboard_tugas.db')
    cursor = conn.cursor()

    # 1. Tabel Users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            nama_lengkap TEXT NOT NULL,
            role TEXT CHECK(role IN ('admin', 'reviewer', 'member')) NOT NULL
        )
    ''')

    # 2. Tabel Tugas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tugas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            judul_tugas TEXT NOT NULL,
            deskripsi TEXT,
            deadline DATE,
            status TEXT CHECK(status IN ('To-Do', 'In Progress', 'Ready for Review', 'Revision Needed', 'Done')) DEFAULT 'To-Do',
            harga_klien REAL DEFAULT 0,
            fee_tim REAL DEFAULT 0,
            member_id INTEGER,
            reviewer_id INTEGER,
            FOREIGN KEY (member_id) REFERENCES users (id),
            FOREIGN KEY (reviewer_id) REFERENCES users (id)
        )
    ''')

    # 3. Tabel Submissions (File Upload & Versioning)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tugas_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            versi INTEGER NOT NULL,
            catatan_member TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tugas_id) REFERENCES tugas (id)
        )
    ''')

    # 4. Tabel Reviews
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            komentar TEXT,
            hasil TEXT CHECK(hasil IN ('Approved', 'Rejected')) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (submission_id) REFERENCES submissions (id)
        )
    ''')

    # 5. Tabel Cash Flow
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cash_flow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tugas_id INTEGER,
            tipe TEXT CHECK(tipe IN ('Pemasukan', 'Pengeluaran')) NOT NULL,
            nominal REAL NOT NULL,
            keterangan TEXT,
            tanggal TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tugas_id) REFERENCES tugas (id)
        )
    ''')

    conn.commit()
    conn.close()
    print("Database 'dashboard_tugas.db' berhasil dibuat dengan 5 tabel utama.")

if __name__ == '__main__':
    init_database()