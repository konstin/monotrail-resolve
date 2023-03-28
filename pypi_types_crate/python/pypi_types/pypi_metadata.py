import sys

from .pypi_types import pypi_metadata

vars(sys.modules[__name__]).update(pypi_metadata.__dict__)
__all__ = pypi_metadata.__all__
__doc__ = pypi_metadata.__doc__

del sys
del pypi_metadata
