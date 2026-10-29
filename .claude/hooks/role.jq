# The OMC agent type behind a hook payload's agent_type, or "" when none.
# agent_type is the spawn name ({name}-{type}-{slug}) or, unnamed, the type (oh-my-claudecode:executor);
# the first known type in it is the role, so a slug word never wins
[(.agent_type // "")
 | match("(?:^|[-:])(executor|architect|critic|planner|analyst|debugger|designer|document-specialist|code-reviewer|code-simplifier|git-master|qa-tester|scientist|security-reviewer|test-engineer|tracer|verifier|writer|explore)(?=-|$)"; "g")]
| first.captures[0].string // ""
