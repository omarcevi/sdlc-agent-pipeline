"""Makes `import mdlite` work when pytest runs from outside this directory.

In its default "prepend" import mode pytest inserts the directory of each conftest.py
that has no `__init__.py` next to it into sys.path. This file's directory holds the
`mdlite` package, so `import mdlite` works wherever pytest is started.
"""
