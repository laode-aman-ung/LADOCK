"""
LADOCK License Key Generator (tools/generate_license.py)

HANYA untuk digunakan oleh pemilik LADOCK (La Ode Aman).
Jalankan dari root direktori LADOCK:

  python tools/generate_license.py

Kunci yang dihasilkan dikirim ke pengguna via email.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.license_manager import generate_key, LicenseType
from datetime import date, timedelta


def main():
    print("=" * 60)
    print("  LADOCK License Key Generator")
    print("  Copyright (c) 2024 La Ode Aman")
    print("=" * 60)
    print()

    # Pilih jenis lisensi
    print("Jenis Lisensi:")
    print("  1. Academic Free      (gratis untuk semua s/d 2030-12-31)")
    print("  2. Academic Discount  (diskon pasca-2030)")
    print("  3. Commercial         (berbayar, perpetual)")
    print()

    choice = input("Pilih [1/2/3]: ").strip()
    if choice == "1":
        ltype   = LicenseType.ACADEMIC_FREE
        expires = "2030-12-31"
    elif choice == "2":
        ltype = LicenseType.ACADEMIC_DISCOUNT
        print()
        exp_input = input("Tanggal kadaluarsa (YYYY-MM-DD, kosongkan = 1 tahun): ").strip()
        expires   = exp_input if exp_input else (
            date.today().replace(year=date.today().year + 1).isoformat()
        )
    elif choice == "3":
        ltype   = LicenseType.COMMERCIAL
        expires = None  # perpetual
    else:
        print("Pilihan tidak valid.")
        sys.exit(1)

    print()
    name  = input("Nama penerima / institusi : ").strip()
    email = input("Email institusi           : ").strip()

    if not name or not email:
        print("Nama dan email wajib diisi.")
        sys.exit(1)

    key = generate_key(
        license_type=ltype.value,
        name=name,
        email=email,
        expires=expires,
    )

    print()
    print("=" * 60)
    print("  LICENSE KEY GENERATED")
    print("=" * 60)
    print()
    print(f"  Type    : {ltype.value}")
    print(f"  Name    : {name}")
    print(f"  Email   : {email}")
    print(f"  Expires : {expires or 'Perpetual (no expiry)'}")
    print()
    print("  KEY:")
    print()
    print(f"  {key}")
    print()
    print("=" * 60)
    print()

    # Simpan ke file log
    log_dir  = os.path.join(os.path.dirname(__file__), "issued_licenses")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "issued.txt")

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"Date    : {date.today().isoformat()}\n")
        f.write(f"Type    : {ltype.value}\n")
        f.write(f"Name    : {name}\n")
        f.write(f"Email   : {email}\n")
        f.write(f"Expires : {expires or 'Perpetual'}\n")
        f.write(f"Key     : {key}\n")

    print(f"  Log disimpan di: tools/issued_licenses/issued.txt")
    print()

    # Salin ke clipboard jika tersedia
    try:
        import pyperclip
        pyperclip.copy(key)
        print("  ✅ Kunci disalin ke clipboard.")
    except ImportError:
        print("  (Install pyperclip untuk salin otomatis ke clipboard)")


if __name__ == "__main__":
    main()
