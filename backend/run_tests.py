import os
import subprocess
import sys

backend_dir = r"C:\Users\HP\OneDrive\Documents\GitHub\fieldops-projects\fieldops-projects\backend"
os.environ["PYTHONPATH"] = backend_dir

args = [
    r"C:\Users\HP\OneDrive\Documents\GitHub\fieldops-projects\fieldops-projects\backend\.venv\Scripts\pytest.exe",
]
if len(sys.argv) > 1:
    args.extend(sys.argv[1:])
else:
    args.append(backend_dir)

result = subprocess.run(args, cwd=backend_dir)
sys.exit(result.returncode)
