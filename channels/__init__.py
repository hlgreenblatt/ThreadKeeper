"""channels package — re-exports CommChannel from src/channels.py."""
import importlib.util as _ilu
import os as _os
import sys as _sys

_src_channels = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src", "channels.py"
)
if _os.path.exists(_src_channels):
    _src_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src")
    if _src_dir not in _sys.path:
        _sys.path.insert(0, _src_dir)
    _spec = _ilu.spec_from_file_location("_src_channels_mod", _src_channels)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    for _name in dir(_mod):
        if not _name.startswith("_"):
            globals()[_name] = getattr(_mod, _name)
