import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(BASE_DIR, 'dashboard_tugas.db')

conn = sqlite3.connect(db_path)
try:
    conn.execute('ALTER TABLE tugas ADD COLUMN file_admin TEXT')
    print("Kolom file_admin berhasil ditambahkan!")
except:
    print("Kolom mungkin sudah ada.")
conn.commit()
conn.close()