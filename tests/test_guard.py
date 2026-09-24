import importlib.util
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location("guard", os.environ.get("GUARD") or Path(__file__).parents[1] / ".claude/hooks/guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

# No network or git in tests: PR 1 touches the harness, PR 2 only a strategy. Current branch is a feature branch.
guard.pr_files = lambda target: {"1": ["harness/costs.py", "README.md"], "2": ["strategies/H-001/spec.yaml"]}.get(
    (target or ["2"])[0]
)
guard.current_branch = lambda: "m0-archives"

BLOCK = [
    "gh pr merge 1 --squash",
    "cd x && gh pr merge 1",
    "gh pr merge --subject 'fix' 1",
    "gh pr merge 99",  # unknown PR: fail closed
    "git push origin main",
    "git push origin HEAD:main",
    "git push -f origin +feature:refs/heads/main",
    "git push --all",
    "gh api -X PUT repos/FXKrueger/SISCO/pulls/1/merge",
    "gh api repos/FXKrueger/SISCO/rulesets --method DELETE",
    "gh api repos/FXKrueger/SISCO/contents/harness/x.py -f content=abc",
    "gh api -X PATCH repos/FXKrueger/SISCO -f default_branch=x",
    "gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'",
    "gh repo edit --default-branch x",
    "gh auth token",
    "curl -X POST https://api.github.com/repos/FXKrueger/SISCO/merges",
    "sed -i '' 's/x/y/' .claude/hooks/guard.py",
    "rm .github/CODEOWNERS",
    "cat /dev/null > .claude/settings.json",
    "rm " + str(Path(__file__).parents[1] / ".claude/settings.json"),
    "echo $(gh pr merge 1)",
    'echo "`gh pr merge 1`"',
    "true; gh pr merge 1",
]
ALLOW = [
    "gh pr merge 2 --squash",
    "gh pr merge",  # current branch's PR, strategy only
    "git push -u origin m0-archives",
    "git push",
    "gh pr create --title x --body y",
    "gh api repos/FXKrueger/SISCO/pulls/1",
    "gh api -X POST repos/FXKrueger/SISCO/issues -f title=H-002",
    "gh api repos/FXKrueger/SISCO/pulls/1/comments -f body=hi",
    "cat .claude/hooks/guard.py",
    "git add .claude/settings.json",
    "python -m ingestion",
    "docker run --rm img python -c \"import x; print('a && b | c')\"",
    "grep -n foo .claude/hooks/guard.py | head -5",
    "node ~/.claude/plugins/codex/scripts/companion.mjs task",
    "echo note >> /Users/someone/.claude/projects/x/memory/MEMORY.md",
]


def test_guard():
    for cmd in BLOCK:
        assert guard.check(cmd), f"should block: {cmd}"
    for cmd in ALLOW:
        assert not guard.check(cmd), f"should allow: {cmd}: {guard.check(cmd)}"


if __name__ == "__main__":
    test_guard()
    print("ok")
