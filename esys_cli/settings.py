"""Per-user writable state, independent of package and vendor installations."""
import os
from pathlib import Path


def state_root():
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
    else:
        base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return base.resolve() / 'esys-cli'
