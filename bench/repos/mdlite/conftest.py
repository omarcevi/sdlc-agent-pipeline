"""Makes `import mdlite` work when pytest runs from outside this directory.

pytest's default import mode puts the directory of the top-most conftest.py on
sys.path; without this file the tests could only import `mdlite` from here.
"""
