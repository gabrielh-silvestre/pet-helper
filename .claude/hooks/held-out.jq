# True when the tool call reads, searches, edits or runs anything naming the held-out eval cases.
[.tool_input | .file_path, .notebook_path, .path, .pattern, .command]
| map(strings) | any(contains("evals/held_out"))
