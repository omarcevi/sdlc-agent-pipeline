"""The version of the agents' prompts. Result rows carry it, and the report refuses to
pool rows of different versions. Bump it with any change to an agent's instruction,
including how the issue text reaches it.

  2a: Weeks 2A and 2B (the issue text was part of the first message).
  2c: Week 2C (the issue text is the delimited `{issue_text}` block in each
      instruction).
"""

PROMPT_VERSION = "2c"
# Result files from before the version existed hold 2A and 2B runs.
LEGACY_PROMPT_VERSION = "2a"
