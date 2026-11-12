"""Allows running AutoForge as a module: ``python -m autoforge run <issue>``.

It simply forwards to the CLI in ``autoforge.app.cli`` and exits with its
return code (0 = success, 1 = run failed, 2 = configuration error).
"""

import sys

from autoforge.app.cli import main

sys.exit(main())
