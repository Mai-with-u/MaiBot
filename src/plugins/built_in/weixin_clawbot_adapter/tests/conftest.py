from pathlib import Path
import sys

# Import this directory as a package, matching the plugin loader's relative imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
