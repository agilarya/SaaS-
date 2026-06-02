import os
import time
import hashlib
import requests

# Konfigurasi Penyimpanan Default
STORAGE_PROVIDER = 'cloudinary' 

# ==========================================
# KONFIGURASI KREDENSIAL CLOUDINARY
# ==========================================
CLOUD_NAME = "dtueufvbz"  # Terdeteksi secara otomatis dari log error Anda
API_KEY = "665732654762523"
API_SECRET = "pMyE8qis-wEhAtFweYvPp3eEdho"

def pindah_ke_cloud(lokasi_file_lokal, nama_file_baru):
    """
    Fungsi utama pengiriman file ke Cloudinary via Direct API (Bypass SDK).
    """
    if STORAGE_PROVIDER != 'cloudinary':
        return None
        
    try:
        # 1. Persiapan stempel waktu
        timestamp = str(int(time.time()))
        
        # 2. Pembuatan Digital Signature sesuai standar API Cloudinary
        # Parameter harus diurutkan secara alfabetis (public_id, lalu timestamp)
        string_to_sign = f"public_id={nama_file_baru}&timestamp={timestamp}{API_SECRET}"
        signature = hashlib.sha1(string_to_sign.encode('utf-8')).hexdigest()
        
        # 3. Penentuan URL Endpoint
        url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/auto/upload"
        
        data = {
            "api_key": API_KEY,
            "timestamp": timestamp,
            "public_id": nama_file_baru,
            "signature": signature
        }
        
        # 4. Penguncian Akses Proxy (Mengatasi Error 111 PythonAnywhere)
        proxy_pa = {
            'http': 'http://proxy.server:3128',
            'https': 'http://proxy.server:3128'
        }
        
        # 5. Eksekusi Pengiriman
        with open(lokasi_file_lokal, 'rb') as f:
            files = {'file': f}
            response = requests.post(url, data=data, files=files, proxies=proxy_pa)
        
        hasil = response.json()
        
        if 'secure_url' in hasil:
            return hasil['secure_url']
        else:
            print(f"[Error Cloudinary API]: {hasil}")
            return None
            
    except Exception as e:
        print(f"[System Error]: {str(e)}")
        return None