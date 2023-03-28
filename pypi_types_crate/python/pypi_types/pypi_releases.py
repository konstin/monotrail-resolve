import sys

from .pypi_types import pypi_releases

vars(sys.modules[__name__]).update(pypi_releases.__dict__)
__all__ = pypi_releases.__all__
__doc__ = pypi_releases.__doc__

del sys
del pypi_releases
