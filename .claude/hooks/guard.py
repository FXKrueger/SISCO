#!/usr/bin/env python3
"""PreToolUse guard for Bash: agents may write protected code on a branch and open a PR,
but may not land it, and may not weaken repo protection.

Blocks:
- `gh pr merge` when the PR touches a protected path (CLAUDE.md hard rule 1),
- `git push` to main, --all, --mirror,
- GitHub API writes other than issues, comments and PR creation (merges, refs, contents,
  rulesets, branch protection, repo settings), `gh repo edit|delete`, `gh auth token`, raw api.github.com,
- shell writes to the guard itself (.claude/, CODEOWNERS, rulesets).

ponytail: string matching on shell commands. It stops a goal-driven agent from walking past
the rule; it does not stop one that is actively trying to evade it (e.g. a script it writes and
then runs). Real enforcement is server-side: GitHub ruleset + CODEOWNERS, see README.
"""

import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED_DIRS = ("harness/", "risk/", "execution/", "registry/", ".github/", ".claude/")
PROTECTED_FILES = ("config/limits.yaml", "CLAUDE.md")
MAIN = ("main", "master")
MERGE_VALUE_FLAGS = {"-t", "--subject", "-b", "--body", "-F", "--body-file", "--match-head-commit", "-A", "--author-email"}
API_WRITE_OK = re.compile(r"^/?repos/[^/]+/[^/]+/(issues(/.*)?|pulls|pulls/\d+/(comments|reviews|requested_reviewers))$")
# This repo's .claude/ (relative, or under the repo path), not ~/.claude/ (plugins, agent memory).
REPO = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()).rstrip("/")
GUARD_FILES = re.compile(r"(?<![\w~/.-])\.claude/|" + re.escape(REPO + "/.claude/") + r"|CODEOWNERS|\.github/rulesets")
READ_ONLY = re.compile(r"^(cat|less|head|tail|grep|rg|ls|wc|diff|git (diff|log|show|status|add|blame))\b")


def protected(path):
    return path.startswith(PROTECTED_DIRS) or path in PROTECTED_FILES


def pr_files(target):
    r = subprocess.run(["gh", "pr", "view", *target, "--json", "files", "--jq", ".files[].path"], capture_output=True, text=True)
    return r.stdout.split() if r.returncode == 0 else None


def current_branch():
    return subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()


def check_segment(t):
    """t: tokens of one simple command. Return a reason to block, or None."""
    seg = " ".join(t)
    if GUARD_FILES.search(seg) and (any(a.startswith(">") for a in t) or not READ_ONLY.match(seg)):
        return "writes to the guard itself (.claude/, CODEOWNERS, rulesets) are for the lead only"
    if "api.github.com" in seg:
        return "use gh, not raw calls to api.github.com"
    # Skip env assignments like GH_TOKEN=x gh ...
    while t and re.match(r"^\w+=", t[0]):
        t = t[1:]
    if t[:2] == ["gh", "auth"] and "token" in t:
        return "handing out the GitHub token is blocked"
    if t[:2] == ["gh", "repo"] and len(t) > 2 and t[2] in ("edit", "delete", "rename", "archive"):
        return "repo settings are for the lead only"
    if t[:3] == ["gh", "pr", "merge"]:
        target, rest = [], t[3:]
        while rest:
            a = rest.pop(0)
            if a in MERGE_VALUE_FLAGS:
                rest = rest[1:]
            elif not a.startswith("-"):
                target = [a]
        files = pr_files(target)
        if files is None:
            return "could not list the PR's files, refusing to merge"
        hit = [f for f in files if protected(f)]
        if hit:
            return f"PR touches protected paths {hit}. The lead reviews and merges it"
    if t[:2] == ["gh", "api"]:
        path = next((a for a in t[2:] if not a.startswith("-") and "/" in a), "")
        method = next((t[i + 1] for i, a in enumerate(t[:-1]) if a in ("-X", "--method")), None)
        writes = method not in (None, "GET") or any(a in ("-f", "-F", "--field", "--raw-field", "--input") for a in t)
        if path == "graphql" or "graphql" in t:
            if "mutation" in seg:
                return "GraphQL mutations are blocked, use gh pr / gh issue"
        elif writes and not API_WRITE_OK.match(path):
            return f"GitHub API write to {path!r} is blocked"
    if "git" in t and "push" in t[t.index("git") :]:
        args = [a for a in t[t.index("push") + 1 :] if not a.startswith("-")]
        if {"--all", "--mirror"} & set(t):
            return "push --all/--mirror is blocked"
        refspecs = args[1:]
        dsts = [r.split(":")[-1].removeprefix("+").removeprefix("refs/heads/") for r in refspecs]
        dsts = [current_branch() if d == "HEAD" else d for d in dsts]
        if any(d in MAIN for d in dsts) or (not refspecs and current_branch() in MAIN):
            return "direct push to main is blocked, open a PR"
    return None


def check(cmd):
    # Command substitutions run too, even inside quotes: check their contents as commands of their own.
    for sub in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", cmd):
        if reason := check("".join(sub)):
            return reason
    try:
        lex = shlex.shlex(cmd.replace("\n", ";"), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return "cannot parse command"
    seg = []
    for tok in tokens + [";"]:
        if tok and set(tok) <= set(";&|()"):
            if seg and (reason := check_segment(seg)):
                return reason
            seg = []
        else:
            seg.append(tok)
    return None


if __name__ == "__main__":
    cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    if reason := check(cmd):
        print(f"Blocked by .claude/hooks/guard.py: {reason}", file=sys.stderr)
        sys.exit(2)
