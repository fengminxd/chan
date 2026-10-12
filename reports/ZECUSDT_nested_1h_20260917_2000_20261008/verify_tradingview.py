"""Run the installed local browser via CDP; real animation frames verify interactive rendering."""
from pathlib import Path
import subprocess,sys
P=Path(__file__).resolve().parent
r=subprocess.run(['node',str(P/'verify_cdp.mjs'),sys.argv[1] if len(sys.argv)>1 else 'verify'],timeout=55)
sys.exit(r.returncode)
