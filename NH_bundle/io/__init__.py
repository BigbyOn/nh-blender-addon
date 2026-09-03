# Minimal package init for the embedded A3OB bundle.
# Keeps only the codec modules used by the NH Blender plugin.
from . import binary_handler  # noqa: F401
from . import compression  # noqa: F401
from . import data_p3d  # noqa: F401
from . import data_paa  # noqa: F401
from . import import_paa  # noqa: F401
from . import import_p3d  # noqa: F401
from . import export_p3d  # noqa: F401
