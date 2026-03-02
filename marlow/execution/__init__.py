"""Marlow Execution — tool implementations and system integrations.

Re-exports from core/ and tools/ for backward compatibility.
Existing imports (from marlow.core.X, from marlow.tools.Y) continue
to work unchanged. This package provides a unified namespace for
the kernel to access all execution capabilities.
"""

# Re-export core modules
from marlow.core import *  # noqa: F401, F403

# Re-export tools modules
from marlow.tools import *  # noqa: F401, F403
