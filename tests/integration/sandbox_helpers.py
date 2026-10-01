"""Checks shared by the Docker-only and the cloud sandbox tests."""

import re

CREDENTIAL_NAME = re.compile(r"TOKEN|SECRET|KEY|CREDENTIAL|PASSWORD|GOOGLE_")
# Set by the python base image: the public fingerprint of the key that signs CPython
# releases. It is not a credential; any other value under this name is a leak.
IMAGE_PUBLIC_VALUES = {"GPG_KEY": "7169605F62C751356D054A26A821E680E5FA6305"}
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def credential_like_variables(env_output: str) -> list[str]:
    """Names (never values) of the variables in `env` output whose name looks like a
    credential, except an image variable that still holds its public value."""
    found = []
    for line in env_output.splitlines():
        name, _, value = line.partition("=")
        if not _VARIABLE.fullmatch(name):
            continue  # the continuation of a multi-line value
        if CREDENTIAL_NAME.search(name) and IMAGE_PUBLIC_VALUES.get(name) != value:
            found.append(name)
    return sorted(found)
