import sys
from pathlib import Path

# Add project root to Python path so src imports work
project_root = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(project_root))
