from __future__ import annotations

import os


_NATIVE_THREAD_ENV_DEFAULTS = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}

for _name, _value in _NATIVE_THREAD_ENV_DEFAULTS.items():
    os.environ.setdefault(_name, _value)
