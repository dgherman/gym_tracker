import os
import sys

# Ensure the repository root is importable so tests can `import main`
# (main.py lives at the repo root and is not part of the installed package).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
