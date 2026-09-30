# taskcli

A tiny command-line task manager. Tasks live in a JSON file (`--file`, `$TASKCLI_FILE`, or `~/.taskcli.json`).

    python -m taskcli add "Buy milk" --due tomorrow --tag home
    python -m taskcli list --sort due
    python -m taskcli done 1
