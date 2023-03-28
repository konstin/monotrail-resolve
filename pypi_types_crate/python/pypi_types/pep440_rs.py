import sys

from .pypi_types import pep440_rs

vars(sys.modules[__name__]).update(pep440_rs.__dict__)
__all__ = pep440_rs.__all__
__doc__ = pep440_rs.__doc__

del sys
del pep440_rs
