# run.py
import sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

# Remove old cache that could conflict
import glob
for f in glob.glob("__pycache__/*.pyc") + glob.glob("**/__pycache__/*.pyc", recursive=True):
    try: os.remove(f)
    except: pass

from web.app import app, _init_models

print("Initializing models...")
try:
    _init_models()
    print("Models ready.")
except Exception as e:
    print(f"Init warning: {e}")

print("Starting on http://127.0.0.1:5000")
app.run(debug=False, host="0.0.0.0", port=5000)
