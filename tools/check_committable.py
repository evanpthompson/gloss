"""Gate what may be committed to this repository at all.

`origin` carries two push URLs, so one `git push` publishes to GitLab *and* to
the public GitHub mirror at the same moment. There is no window between
"committed" and "public", and a later `git rm` does not unpublish anything.
The stake is prep packs above all: they name a company and a person, and the
documents behind them routinely carry a salary floor and a walk-away number.

Three checks, all of which fail closed:

1. **The path allow-list.** Anything not named here is refused, rather than a
   list of bad patterns that cannot enumerate what nobody has invented yet.
   `.gitignore` already covers the case somebody thought about; this covers
   `git add -f`, and a new directory nobody has ignored.
2. **Credential shapes**, over every added line of every file.
3. **Prep-pack leak patterns**, over `sessions/` only, reusing the same table
   `tools/check_pack.py` gates live packs with.

Run it two ways, and both must exist for it to be a gate:

    uv run python tools/check_committable.py --staged   # the pre-commit hook
    uv run python tools/check_committable.py --tree     # CI, over every file

`--no-verify` is one flag, so the hook alone protects nothing that matters;
`--tree` is the same check in the environment where merges happen.

Exit code 0 only if everything passes.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_pack import LEAK_PATTERNS  # the one table, not a second copy of it

# --- 1. what may be committed ----------------------------------------------
#
# Globs against the repo-relative POSIX path. Adding a row here is a deliberate
# act and should read like one: it is the moment somebody decided a new kind of
# file belongs in a public repository.
ALLOWED = [
    # Source. Flat by design — every module sits at the root.
    "*.py",
    "display.html",
    # Tests and tools.
    "tests/*.py",
    "tools/*.py",
    "tools/*.html",
    "tools/*.js",
    "tools/results/*.json",
    # Docs.
    "README.md",
    "SPEC.md",
    "PHASE-3-PLAN.md",
    "LICENSE",
    # The prep-pack directory, narrowly. The example pack is written to be
    # public; a real one never is, and this is the line that says so.
    "sessions/README.md",
    "sessions/example/*.md",
    # Build, CI and config.
    "Dockerfile",
    "pyproject.toml",
    "uv.lock",
    ".dockerignore",
    ".gitignore",
    ".gitlab-ci.yml",
    ".env.example",
    "hooks/pre-commit",
]

# Refusals worth explaining rather than just reporting, because the fix differs.
HINTS = [
    ("sessions/", "Real prep packs are private and .gitignore'd. Only "
                  "sessions/README.md and sessions/example/ may be committed."),
    (".env", "A .env holds live keys. Commit .env.example with placeholder "
             "values instead."),
    ("kb/", "The knowledge base is built by tools/build_kb.py from books that "
            "are not ours to redistribute. Reproducible, so not source."),
]

# Files over this are refused unless named below. A pasted transcript, an audio
# capture or a vendored binary all arrive as "one large file nobody looked at".
MAX_BYTES = 512 * 1024
LARGE_OK = {"uv.lock"}

# --- 2. credential shapes ---------------------------------------------------
#
# A deny-list, and therefore the weaker half — it is second, never first. Each
# row is a shape this project could plausibly produce.
SECRET_PATTERNS = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
    (r"\bsk-ant-[A-Za-z0-9_\-]{16,}", "an Anthropic key"),
    (r"\bsk-[A-Za-z0-9]{32,}", "an OpenAI-shaped key"),
    (r"\bAIza[0-9A-Za-z_\-]{35}", "a Google/Gemini key"),
    (r"\bglpat-[A-Za-z0-9_\-]{20,}", "a GitLab personal access token"),
    (r"\bghp_[A-Za-z0-9]{36}", "a GitHub token"),
    # A long hex literal on a line that also says key/token/secret. Deepgram
    # keys are 40 hex characters, and this rule has no exemption path on
    # purpose: the first version of this file let exactly that through,
    # because `a3f9c2e8...` reads as a bare identifier.
    #
    # Note what the name matching has to allow. A key name is almost always a
    # SUFFIX — DEEPGRAM_API_KEY, GITLAB_REPO_TOKEN — and `\b` before `api`
    # never matches there, because `_` is a word character. That single `\b`
    # was the second hole a planted key walked through.
    (
        r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Za-z0-9]+[_-])?"
        r"(?:api[_-]?key|token|secret|credential)\b[^\n]*?\b[0-9a-f]{32,}\b",
        "a hex API key",
    ),
    (
        r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Za-z0-9]+[_-])?"
        r"(?:api[_-]?key|token|secret|password|passwd|credential)"
        r"\s*[:=]\s*[\"']?(?P<val>[A-Za-z0-9/+_\-]{16,})",
        "a credential assignment",
    ),
]

# `api_key=DEEPGRAM_API_KEY` is the correct way to pass a secret and must not
# be reported as one: a reference is not a literal, and the whole point of the
# code under it is that the secret lives somewhere else.
#
# The first version of this exemption was `^[A-Za-z_][A-Za-z0-9_]*$`, which a
# planted 40-character hex key walked straight through — it starts with a
# letter and is alphanumeric, so it *is* a valid identifier. So a reference now
# has to look like one written by a person: snake_case with an underscore, or
# letters with no digits in them at all. A hex blob has digits and no
# underscore and is refused.
#
# The remaining gap, stated rather than hidden: an all-letter secret with no
# digits is still exempt. Real keys have digits; if one does not, the reading
# layer in SPEC.md § Open items is what would catch it.
REFERENCE = re.compile(
    r"^(?:"
    r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"   # snake_case / SCREAMING_SNAKE
    r"|[A-Za-z_]+"                              # letters only, no digits
    r")$"
)

# An env-var-shaped assignment of an opaque value: SCREAMING_SNAKE, then `=`
# or `:`, then something long with no spaces in it. This is the shape rather
# than the name, which is the point — a keyword list only refuses names
# somebody already thought of, and the line that leaks will be called
# something nobody listed.
#
# The value side is what keeps it usable. `MAX_CARDS = 3`, `HOST: "0.0.0.0"`
# and `TTL = float(...)` are the overwhelming majority of SCREAMING_SNAKE
# lines in any repo, so a rule that fired on the name alone would be noise,
# and a check that is noise gets turned off — which is worse than not having
# it, because it is believed to be running.
ENV_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+|[-\s]*)?(?P<name>[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)"
    r"\s*[:=]\s*"
    r"[\"\']?(?P<val>[^\s\"\']{16,}?)[\"\']?\s*$"
)

# The value has to look like an opaque token, and this is an allow-list of
# shape rather than a list of things to skip. Measured against this repo, the
# entire false-positive population of a name-only rule was ordinary code:
#
#   DEEPGRAM_ENVIRONMENT = DeepgramClientEnvironment.PRODUCTION
#   CARD_SCHEMA = card_schema(MAX_CARDS)
#   CONNECTION_ERRORS = _connection_error_types()
#   E2E_REGISTRY: registry.gitlab.com/navetoocool/gloss-e2e
#
# Every one contains a character a secret never does — a parenthesis, a dot, a
# path separator in a hostname. A key is one run of token characters, so that
# is what this matches, plus a digit and a letter to rule out a word.
#
# The known gap: a JWT has dots in it and is missed here. It is caught by
# nothing else either, and saying so is better than widening this until it
# fires on every attribute access in the repo and gets switched off.
OPAQUE_VALUE = re.compile(r"^[A-Za-z0-9_\-+/=]{16,}$")

# Names whose values are legitimately long and opaque. A git SHA and an API key
# are the same forty hex characters, and nothing in the string tells them
# apart — only the name does.
ENV_NAME_EXEMPT = re.compile(
    r"(?i)_(?:REF|SHA|COMMIT|VERSION|DIGEST|TAG|URL|URI|PATH|IMAGE|HOST|DIR|REGISTRY)$"
)

# Lines that are allowed to look like a credential, because saying "put your
# key here" requires writing something key-shaped.
SECRET_EXEMPT = re.compile(
    r"(?i)your[-_ ]?key|<[a-z-]+>|xxx|placeholder|example|unused-mock|"
    r"op://|\$\{?[A-Z_]+\}?|\.\.\."
)


def run(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def staged_paths() -> list[str]:
    out = run("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.split("\0") if p]


def added_lines(path: str, staged: bool) -> list[tuple[int, str]]:
    """The lines this commit introduces, not the whole file: a rule that fires
    on untouched history makes every commit a cleanup task and gets turned off.
    In --tree mode there is no diff, so the whole file is 'added'."""
    if not staged:
        try:
            text = Path(path).read_text(errors="replace")
        except (OSError, UnicodeError):
            return []
        return list(enumerate(text.splitlines(), start=1))

    diff = run("diff", "--cached", "-U0", "--", path)
    out, lineno = [], 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((lineno, line[1:]))
            lineno += 1
    return out


def path_problem(path: str) -> str | None:
    if any(fnmatch.fnmatch(path, pattern) for pattern in ALLOWED):
        return None
    for prefix, hint in HINTS:
        if path.startswith(prefix) or Path(path).name.startswith(prefix):
            return f"{path}\n      {hint}"
    return (
        f"{path}\n      Not on the allow-list in tools/check_committable.py. "
        f"If this file belongs in a public\n      repository, add it there in "
        f"the same commit — that is the decision being made."
    )


def size_problem(path: str) -> str | None:
    if path in LARGE_OK:
        return None
    try:
        size = Path(path).stat().st_size
    except OSError:
        return None
    if size > MAX_BYTES:
        return (f"{path} is {size // 1024}KB, over the {MAX_BYTES // 1024}KB cap. "
                f"Large files\n      arrive as things nobody read.")
    return None


def content_problems(path: str, staged: bool) -> list[str]:
    problems = []
    lines = added_lines(path, staged)

    for lineno, text in lines:
        for pattern, what in SECRET_PATTERNS:
            m = re.search(pattern, text)
            if not m or SECRET_EXEMPT.search(text):
                continue
            value = m.groupdict().get("val")
            if value and REFERENCE.match(value):
                continue
            problems.append(f"{path}:{lineno} looks like {what}")
            break

    for lineno, text in lines:
        m = ENV_ASSIGNMENT.match(text)
        if not m or SECRET_EXEMPT.search(text):
            continue
        name, value = m.group("name"), m.group("val")
        if ENV_NAME_EXEMPT.search(name) or REFERENCE.match(value):
            continue
        if not OPAQUE_VALUE.match(value):
            continue
        if not (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)):
            continue
        problems.append(
            f"{path}:{lineno} sets {name} to a long opaque literal — if that is "
            f"a real value it does not belong here"
        )

    # Prep-pack patterns apply where prep-pack content lives. Elsewhere this
    # table produces false positives on prose that discusses the risk — SPEC.md
    # says "salary floor" precisely because it is explaining this gate. Prose
    # that paraphrases a real number is what the second, reading layer is for;
    # no regex reaches it, and pretending otherwise would be worse than the gap.
    # sessions/README.md is the documentation *about* packs — it has to say
    # "a salary floor" to explain what must never be in one. The packs
    # themselves live in sessions/<name>/.
    if path.startswith("sessions/") and path != "sessions/README.md":
        for lineno, text in lines:
            for pattern, what in LEAK_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    problems.append(f"{path}:{lineno} reads like {what}")
                    break
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="what this commit adds")
    mode.add_argument("--tree", action="store_true", help="every tracked file (CI)")
    args = ap.parse_args()

    try:
        paths = staged_paths() if args.staged else [
            p for p in run("ls-files", "-z").split("\0") if p
        ]
    except FileNotFoundError:
        # Caught separately because it is the one that actually happened: the
        # CI image (uv:python3.13-bookworm-slim) has no git binary, since
        # GitLab clones with a helper container and the job container never
        # needs one. A gate that could not run is not a pass, and it has to
        # say why in one line rather than in a traceback.
        print(
            "REFUSED — `git` is not on PATH, so this check could not run.\n"
            "  A check that could not run is not a pass. Install git in this\n"
            "  environment; do not skip the step.",
            file=sys.stderr,
        )
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"REFUSED — git would not answer: {exc}", file=sys.stderr)
        return 1

    if not paths:
        print("nothing staged" if args.staged else "no tracked files")
        return 0

    refused, flagged = [], []
    for path in paths:
        problem = path_problem(path) or size_problem(path)
        if problem:
            refused.append(problem)
            continue          # an off-list file is refused; its contents are moot
        flagged.extend(content_problems(path, args.staged))

    if not refused and not flagged:
        print(f"check_committable: {len(paths)} file(s) OK")
        return 0

    print("REFUSED — this must not reach a public repository.\n", file=sys.stderr)
    if refused:
        print("  Paths not allowed:", file=sys.stderr)
        for item in refused:
            print(f"    - {item}", file=sys.stderr)
    if flagged:
        print("\n  Content:", file=sys.stderr)
        for item in flagged:
            print(f"    - {item}", file=sys.stderr)
    print(
        "\n  Nothing was committed. Fix the file, or add the path to ALLOWED in\n"
        "  tools/check_committable.py in the same commit if it truly belongs in\n"
        "  public. To override once, deliberately: git commit --no-verify — and\n"
        "  note CI runs this same check with --tree, so an override here is\n"
        "  visible there rather than silent.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
