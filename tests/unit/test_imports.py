"""Every module in the r2r package imports cleanly."""

import importlib
import pkgutil

import r2r


def test_package_imports() -> None:
    assert r2r.__version__


def test_every_submodule_imports() -> None:
    names = [m.name for m in pkgutil.walk_packages(r2r.__path__, prefix="r2r.")]
    assert "r2r.settings" in names
    for name in names:
        importlib.import_module(name)
