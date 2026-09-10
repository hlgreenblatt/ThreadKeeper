"""Root conftest — ensures proper sys.path ordering."""
import os, sys

_root = os.path.dirname(os.path.abspath(__file__))
for _p in [_root, os.path.join(_root, "channels"), os.path.join(_root, "Autotests")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
_src = os.path.join(_root, "src")
if _src not in sys.path:
    sys.path.append(_src)
