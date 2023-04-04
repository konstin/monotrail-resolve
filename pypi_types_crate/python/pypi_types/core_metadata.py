import sys

from .pypi_types import core_metadata

vars(sys.modules[__name__]).update(core_metadata.__dict__)
__all__ = core_metadata.__all__
__doc__ = core_metadata.__doc__

del sys
del core_metadata
