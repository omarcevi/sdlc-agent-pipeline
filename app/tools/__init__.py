"""Agent tool sets."""

from app.tools.files import edit_file, grep, list_dir, read_file, write_file
from app.tools.shell import bash

READ_ONLY_TOOLS = (read_file, list_dir, grep)
CODER_TOOLS = (*READ_ONLY_TOOLS, edit_file, write_file, bash)
WRITE_TOOL_NAMES = frozenset({"edit_file", "write_file"})
