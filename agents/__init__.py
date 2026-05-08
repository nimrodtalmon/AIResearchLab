"""Agent registry. Importing this module wires every implemented role into the
dispatcher's registry."""
from . import dummy          # noqa: F401
from . import polisher       # noqa: F401
from . import problem_smith  # noqa: F401
from . import structurer     # noqa: F401
from . import writer         # noqa: F401
