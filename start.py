#!/usr/bin/env python3
"""
AeroScore AI — Startup Script
Run this instead of app.py for automatic diagnostics and clear error messages.
"""
import sys, os, subprocess

print("=" * 55)
print("  AeroScore AI — Startup Check")
print("=" * 55)

# ── 1. Python version ──────────────────────────────────────
print(f"\n[1/4] Python {sys.version.split()[0]}", end=" ")
if sys.version_info < (3, 8):
    print("✗  Need Python 3.8+")
    sys.exit(1)
print("✓")

# ── 2. Required packages ───────────────────────────────────
print("[2/4] Checking packages...")
REQUIRED = {
    "flask":     ("Flask",     "pip install flask"),
    "cv2":       ("OpenCV",    "pip install opencv-python"),
    "mediapipe": ("MediaPipe", "pip install mediapipe"),
    "numpy":     ("NumPy",     "pip install numpy"),
    "jwt":       ("PyJWT",     "pip install PyJWT"),
}
missing = []
for mod, (name, install) in REQUIRED.items():
    try:
        __import__(mod)
        print(f"      {name} ✓")
    except ImportError:
        print(f"      {name} ✗  →  run:  {install}")
        missing.append(install)

if missing:
    print("\n✗  Missing packages. Install them and try again:")
    for cmd in missing:
        print(f"      {cmd}")
    sys.exit(1)

# ── 3. Port availability ───────────────────────────────────
import socket
PORT = 5050
print(f"[3/4] Checking port {PORT}...", end=" ")
with socket.socket() as s:
    if s.connect_ex(("localhost", PORT)) == 0:
        print(f"✗  Port {PORT} is already in use.")
        print(f"      Stop whatever is using port {PORT} and try again,")
        print(f"      or edit start.py and app.py to use a different port.")
        sys.exit(1)
print("✓ free")

# ── 4. Data directories ────────────────────────────────────
print("[4/4] Preparing directories...", end=" ")
BASE = os.path.dirname(os.path.abspath(__file__))
for d in ["db", "static/uploads"]:
    os.makedirs(os.path.join(BASE, d), exist_ok=True)
print("✓")

# ── Launch ─────────────────────────────────────────────────
print("\n" + "=" * 55)
print("  ✓  All checks passed — starting server")
print("=" * 55)
print(f"\n  Open this file in your browser:")
print(f"  ➜  file://{os.path.join(BASE, 'templates', 'index.html')}")
print(f"\n  OR visit:  http://localhost:{PORT}")
print(f"\n  Login:  admin@aeroscore.ai  /  admin123")
print(f"          coach@aeroscore.ai  /  coach123")
print(f"          judge@aeroscore.ai  /  judge123")
print("\n  Press Ctrl+C to stop\n")

os.chdir(BASE)
os.execv(sys.executable, [sys.executable, os.path.join(BASE, "app.py")])
