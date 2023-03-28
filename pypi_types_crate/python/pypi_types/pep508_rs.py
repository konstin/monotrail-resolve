import sys

from .pypi_types import pep508_rs

vars(sys.modules[__name__]).update(pep508_rs.__dict__)
__all__ = pep508_rs.__all__
__doc__ = pep508_rs.__doc__

del sys
del pep508_rs
