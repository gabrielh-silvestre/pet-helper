# True when the tool call reaches the reserved eval cases: a path form of the folder in any string of a
# read, search or exec tool, the folder named in a location field of an edit tool, a glob under evals,
# or a recursive search, listing or git read over the repository root or evals that does not exclude it.
# shortcut: shell commands are split on ; & | and read by token, not parsed; `cd evals` then a listing, or an
# unlisted tool, still gets through; the git history (a merge commit, the PR that added the cases) is
# reachable through hosted tools that take no path (accepted residual risk, see docs/directives/loop-melhoria.md).
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
# the project folder without a worktree suffix (`pet-helper.feat-x` is a checkout of `pet-helper`)
def project_name: ($ENV.CLAUDE_PROJECT_DIR // "" | split("/") | last | split(".")[0]);
# a path that holds the folder below it: the repository root, evals, or their absolute forms, with
# $PWD, ~, ./ and x/.. resolved; any checkout of the project counts (the session may run in a worktree)
def ancestor:
  gsub("\\$\\{?PWD\\}?"; ($ENV.CLAUDE_PROJECT_DIR // ""))
  | sub("^~"; ($ENV.HOME // ""))
  | gsub("/\\./"; "/") | sub("^\\./"; "")
  | until(test("(^|/)[^/]+/\\.\\./") | not; sub("(?<head>^|/)[^/]+/\\.\\./"; "\(.head)"))
  | sub("/+$"; "")
  | . as $path
  | ($path | split("/")) as $parts
  | IN("", ".", "..", "evals")
    or ($path | startswith("/") and (project_name != "") and (
         ($parts | last | startswith(project_name))
         or (($parts | last) == "evals" and ($parts[-2] | startswith(project_name)))));
# the words of a shell segment, without the rtk prefix and the quotes
def words:
  sub("^\\s*(rtk\\s+(proxy\\s+)?)?"; "") | [scan("\\S+") | gsub("['\"]"; "")];
# the git subcommand and its arguments, skipping the global options that take a value
def git_call:
  .[1:] as $rest
  | ($rest | to_entries | map(select(.key == 0 or ($rest[.key - 1] | IN("-C", "-c", "--git-dir", "--work-tree") | not)))
     | map(.value) | map(select(startswith("-") | not))) as $plain
  | {sub: ($plain[0] // ""), args: $rest};
# the pathspec of a git command: what follows `--`
def git_pathspec:
  (index("--")) as $sep | if $sep == null then null else .[$sep + 1:] end;
# a git read that reaches the folder: no pathspec at all, or a pathspec over an ancestor
def git_reads_folder:
  (words | git_call) as $call
  | ($call.args | git_pathspec) as $pathspec
  | (if $pathspec == null then true else ($pathspec | length == 0 or any(ancestor)) end) as $wide
  # `<rev>:<path>` reads one object: fine unless the path is an ancestor of the folder
  | ($call.args | map(select(test("^[^-].*:")) | split(":")[1:] | join(":"))) as $object_paths
  | ($object_paths | length > 0 and all(ancestor | not)) as $one_object
  | ($object_paths | any(ancestor)) as $object_over_ancestor
  | if $call.sub | IN("grep", "show", "cat-file", "archive") then $object_over_ancestor or ($wide and ($one_object | not))
    elif $call.sub == "log" then $wide and ($call.args | any(IN("-p", "--patch", "-u", "--full-diff") or test("^-[A-Za-z0-9]*p")))
    elif $call.sub == "diff" then
      $wide and (($call.args | (if $pathspec == null then . else .[:index("--")] end) | map(select(startswith("-") | not)) | .[1:] | length) > 0)
    else false end;
# a shell segment that searches, lists or reads recursively over an ancestor without excluding the folder
def recursive_over_ancestor:
  . as $segment
  | ($segment | excludes_folder) as $excludes
  | if $excludes then false else
    ($segment | words) as $words
    | ($words[1:]) as $args
    | ($args | map(select(startswith("-") | not))) as $plain
    | ($args | any(. == "-e" or . == "-f" or startswith("--regexp"))) as $pattern_given
    | if ($words[0] | IN("grep", "egrep", "fgrep")) and ($args | any(test("^-[A-Za-z]*[rR]") or . == "--recursive")) then
        ($plain | if $pattern_given then . else .[1:] end) | (length == 0 or any(ancestor))
      elif $words[0] == "rg" and ($args | any(test("^-[A-Za-z]*u") or startswith("--no-ignore") or . == "--unrestricted")) then
        ($plain | if $pattern_given then . else .[1:] end) | (length == 0 or any(ancestor))
      elif $words[0] == "find" then
        ($args | map(test("^[-(!]")) | index(true)) as $first_flag
        | (if $first_flag == null then $args else $args[:$first_flag] end) | (length == 0 or any(ancestor))
      elif ($words[0] == "ls" and ($args | any(test("^-[A-Za-z]*R") or . == "--recursive"))) or $words[0] == "tree" then
        $plain | (length == 0 or any(ancestor))
      elif $words[0] == "git" then $segment | git_reads_folder
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
      # a glob right under evals reaches the folder without naming it
      or ([$input | .. | strings] | any(test("(^|[^A-Za-z0-9_.-])evals/[^/ ]*[*?\\[]")))
      or ($call.tool_name == "Grep"
          and (($input.path // "") | ancestor)
          and (($call.tool_input.glob // "") | test("^!.*held_out") | not))
      or ([$input | .. | objects | (.command, .code) | strings | split("[;&|\n]+"; null) | .[]] | any(recursive_over_ancestor))
    )
  )
