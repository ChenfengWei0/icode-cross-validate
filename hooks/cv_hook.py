"""Entry point of the cv- hooks (README 细则 15.2, 16.1, 5.3): `cv_hook.py record|turn|session_end|guard`."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE if (HERE / "cv_cli.py").exists() else HERE.parent))

from cv_cli import hook  # noqa: E402

sys.exit(hook(sys.argv[1]))
