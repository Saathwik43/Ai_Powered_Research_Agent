"""HTTP layer, one module per domain.

Importing this package first pulls in ``core.config`` so ``.env`` is loaded and
logging is configured before any route module imports a settings-reading service.
"""

from core import config  # noqa: F401
