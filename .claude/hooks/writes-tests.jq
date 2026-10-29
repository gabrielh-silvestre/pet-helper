# True when the tool call writes under tests/: an edit tool on a path there, or a shell write naming it.
# shortcut: shell writes are spotted by keyword, not parsed; a determined agent can still evade it
([.tool_input | .file_path, .notebook_path] | map(strings) | any(contains("tests/")))
or ((.tool_input.command // "") as $c
    | ($c | contains("tests/"))
      and ($c | test("(>|\\btee\\b|\\bsed\\s+-i|\\bmv\\b|\\bcp\\b|\\brm\\b|\\btruncate\\b|\\bperl\\s+-i)")))
