# True when the tool call reaches the reserved eval cases: a path form of the folder in any string of a
# read, search or exec tool, the folder named in a location field of an edit tool, or a recursive
# search over the repository root or evals that does not exclude it.
# shortcut: shell commands are split on ; & | and read by token, not parsed; `cd evals` then a listing, or an
# unlisted tool, still gets through (accepted residual risk, see the PR).
# the exclusion spellings a search uses to skip the folder: they carry the path form themselves
def exclusion_patterns:
  ["--exclude(-dir)?[= ]\\S+",
   "(-not|!)\\s+-i?(path|name|wholename)\\s+('[^']*'|\"[^\"]*\"|\\S+)",
   "-path\\s+('[^']*'|\"[^\"]*\"|\\S+)\\s+-prune"];
def strip_exclusions:
  reduce exclusion_patterns[] as $pattern (.; gsub($pattern; ""));
# an exclusion counts only when the removed fragment itself names the folder
def excludes_folder:
  . as $text
  | exclusion_patterns
  | any(. as $pattern | [$text | match($pattern; "g").string] | any(test("held_out")));
# a path that holds the folder below it
def ancestor:
  sub("/+$"; "")
  | (IN("", ".", "..", "evals", "./evals") or . == $ENV.CLAUDE_PROJECT_DIR or . == ($ENV.CLAUDE_PROJECT_DIR + "/evals"));
# a shell segment that searches recursively over an ancestor without excluding the folder
def recursive_over_ancestor:
  . as $segment
  | ($segment | excludes_folder) as $excludes
  | if $excludes then false else
    ($segment | sub("^\\s*(rtk\\s+(proxy\\s+)?)?"; "") | [scan("\\S+") | gsub("['\"]"; "")]) as $words
    | ($words[1:]) as $args
    | ($args | map(select(startswith("-") | not))) as $plain
    | if ($words[0] | IN("grep", "egrep", "fgrep")) and ($args | any(test("^-[A-Za-z]*[rR]") or . == "--recursive")) then
        ($args | any(. == "-e" or . == "-f" or startswith("--regexp"))) as $pattern_given
        | ($plain | if $pattern_given then . else .[1:] end) | (length == 0 or any(ancestor))
      elif $words[0] == "find" then
        ($args | map(test("^[-(!]")) | index(true)) as $first_flag
        | (if $first_flag == null then $args else $args[:$first_flag] end) | (length == 0 or any(ancestor))
      else false end
    end;

. as $call
| ($call.tool_name | IN("Write", "Edit", "MultiEdit", "NotebookEdit")) as $edits
# an excluding glob names the folder on purpose
| ($call.tool_input | if (.glob // "") | startswith("!") then del(.glob) else . end) as $input
| ([$input | .file_path, .notebook_path, .path, .glob | strings] | any(test("(^|[^A-Za-z0-9_])held_out($|[^A-Za-z0-9_])")))
  or (
    ($edits | not) and (
      ([$input | .. | strings | strip_exclusions] | any(test("/held_out|held_out/")))
      or ($call.tool_name == "Grep"
          and (($input.path // "") | ancestor)
          and (($call.tool_input.glob // "") | test("^!.*held_out") | not))
      or ([$input | .. | objects | (.command, .code) | strings | split("[;&|\n]+"; null) | .[]] | any(recursive_over_ancestor))
    )
  )
