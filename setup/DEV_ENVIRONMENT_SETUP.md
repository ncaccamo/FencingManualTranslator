# BookTranslator — Developer Environment Setup (Internal Reference)

This is for development, not something that applies to end users. It covers VS Code, Claude Code, and GitHub, written to route around the specific mistakes made getting this set up the first time.

(Contrast with `MANUAL_SECTION_II_HOST_PREREQUISITES.md`, which covers
what a machine needs to *run* the translation pipeline itself — GPU
drivers, Docker, ROCm. This document is about the tools used to *develop*
it.)

---

## 1. VS Code

### Install

Two official options. Snap is simplest; the APT repo is better if you
want everything managed through `apt` like the rest of the system.

**Snap (simplest):**
```bash
sudo snap install code --classic
```

**Official APT repository:**
```bash
sudo apt update
sudo apt install -y wget gpg apt-transport-https
sudo install -m 0755 -d /etc/apt/keyrings
wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /tmp/packages.microsoft.gpg
sudo install -D -o root -g root -m 644 /tmp/packages.microsoft.gpg /etc/apt/keyrings/packages.microsoft.gpg
echo "deb [arch=amd64,arm64,armhf signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" | sudo tee /etc/apt/sources.list.d/vscode.list > /dev/null
rm -f /tmp/packages.microsoft.gpg
sudo apt update
sudo apt install -y code
```

Verify: `code --version`. Open the project: `code ~/BookTranslator`

### Recommended extensions

- **Python** (Microsoft) — IntelliSense, linting, and real breakpoint
  debugging for the pipeline scripts, instead of print-statement debugging.
- **Container Tools** (Microsoft) — sidebar tree of running
  containers/images; right-click a container for **View Logs** or
  **Attach Shell** instead of typing `docker exec ...` by hand every
  time. Also adds syntax highlighting/validation for `Dockerfile` and
  `docker-compose.yml`.

Install via `Ctrl+Shift+X`, search by name, click Install. No
configuration needed for either.

---

## 2. Claude Code

### Install

Native installer, Linux, no Node.js dependency:
```bash
curl -fsSL https://claude.ai/install.sh | bash
claude --version
```

### Two ways to use it — this was a real point of confusion, worth being explicit about

**A. Terminal mode.** Open a normal VS Code terminal (`` Ctrl+` ``) and
type `claude`. The terminal *transforms in place* — same window, but it
now behaves like a chat session instead of a shell. Exit with `Ctrl+D`
or `exit` to get a plain shell back in that same window.

**B. Sidebar extension mode.** `Ctrl+Shift+X` → search "Claude Code" →
Install. This gives a dedicated chat panel, visually separate from any
terminal — no mode-switching to keep track of. **Recommended over
terminal mode** if the "which mode is this window in right now" question
is ever confusing — it removes the ambiguity entirely.

Either way, it's often useful to have a **plain terminal open at the same
time** as Claude (for git commands, docker commands, etc.). Use the `+`
button in the terminal panel to open a second tab rather than repeatedly
exiting/re-entering a chat session.

### Known limitation: VS Code extension + Remote-SSH

If ever developing this project from a *different* machine over
Remote-SSH (rather than sitting at this box directly): the Claude Code
VS Code extension currently has a bug where it uses local paths instead
of the remote machine's paths, and doesn't work correctly. Workaround:
use VS Code + Remote-SSH for the *editor*, but run the Claude Code CLI
directly in VS Code's integrated terminal (which is actually running on
the remote host) — i.e. fall back to terminal mode (A) specifically in
that scenario, not the extension.

### Resuming work in a new session

Claude Code has no memory of past sessions on its own. `docs/STATUS.md`
(plus `git log` for the narrative) exists to solve this — keep STATUS
updated with current state, and point a fresh session at it:

> Read docs/STATUS.md and CLAUDE.md, then look at the actual current
> state of the pipeline scripts to confirm it matches. Let's start with
> [whatever's next].

Telling it to verify against the real files (not just trust the
document) matters — we've had cases where a tracking doc said something
was not-done when it actually already worked. (`HANDOFF.md` and
`MANIFEST.md` were both retired 2026-08-27 in favour of the slim
`docs/STATUS.md` + git history.)

---

## 3. Git identity

Required once, before your first commit — otherwise commits get
attributed to a generic/wrong identity:
```bash
git config --global user.name
git config --global user.email
```
If either comes back empty:
```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

---

## 4. GitHub — the part that actually went wrong, read this carefully

**The core confusion:** signing into GitHub *inside VS Code* (Source
Control panel) and authenticating git *in a plain terminal* are two
separate things. VS Code's sign-in does NOT automatically make terminal
`git push`/`git pull` work. This is exactly what caused the "commits
exist locally but aren't on GitHub" incident.

### What NOT to do

Don't let git prompt you for a GitHub username + password in the
terminal. GitHub disabled plain password authentication for git
operations back in 2021 — it will fail with `Invalid username or token.
Password authentication is not supported for Git operations.` every
time, no matter how correct the password is.

### The fix: GitHub CLI (`gh`)

Once authenticated, `gh` configures itself as git's credential helper —
this fixes terminal `git push`/`pull` **and** anything Claude Code runs
under the hood, since Claude Code's git operations are plain terminal
commands too.

Install (separate commands, not chained — easier to spot exactly where
something fails if it does):
```bash
sudo mkdir -p -m 755 /etc/apt/keyrings
```
```bash
wget -nv -O /tmp/githubcli.gpg https://cli.github.com/packages/githubcli-archive-keyring.gpg
```
```bash
sudo cp /tmp/githubcli.gpg /etc/apt/keyrings/githubcli-archive-keyring.gpg
```
```bash
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
```
```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list
```
```bash
sudo apt update
```
```bash
sudo apt install gh -y
```

Authenticate:
```bash
gh auth login
```
Answer: **GitHub.com** → **HTTPS** → **Login with a web browser**. It
gives a one-time code and a URL — follow it, done. After this, plain
`git push` / `git pull` in any terminal just works, no more prompts.

*(SSH keys are the other standard alternative to `gh` — not covered
here since `gh` is simpler and is what's actually set up on this
machine, but worth knowing it exists if a future situation calls for it,
e.g. a machine without a browser available for the `gh auth login` flow.)*

### Creating a new repo correctly, and — critically — verifying it worked

VS Code's Source Control panel (`Ctrl+Shift+G`) has a **Publish to
GitHub** button when a folder isn't a git repo yet. It handles `git
init`, the first commit, creating the actual repo on GitHub, and the
first push, all in one click.

**Do not just trust that it worked.** Immediately after, run:
```bash
git remote -v
```
This should print real `origin` URLs (fetch and push). If it prints
**nothing at all**, the local repo/commit succeeded but the GitHub link
never actually completed — which is exactly what happened here. Don't
find this out three commits later.

If you ever need to link an *already-existing* local repo to a repo you
created directly on github.com instead:
```bash
git remote add origin https://github.com/<username>/<repo>
git push -u origin main
```

### Commit vs. Push — they are separate steps

**Commit** = save a snapshot locally. **Push** = send it to GitHub.
VS Code does NOT auto-push after every commit by default — check the
status bar for an up/down arrow with a number badge (unpushed commit
count), or click **Sync Changes**.

To stop needing to remember this: open VS Code Settings, search `Git:
Post Commit Command`, set it to `push`. Every commit auto-pushes after
that.

---

## 5. `.gitignore` for this project

Create at the repo root before your first real commit — removing
already-committed large files later is much more annoying than not
committing them in the first place:
```
# Pipeline input/output -- large, derived, not code
books/
processed/

# Python
__pycache__/
*.pyc

# Model files (these live in ~/llm_models anyway, outside this folder,
# but excluding just in case one ever ends up here)
*.gguf

# Environment (no secrets in it right now, but good habit)
.env
```

---

## 6. If pasting multi-line commands into a terminal ever goes wrong again

This machine's terminal had real trouble with multi-line pastes early
on — heredocs (`cat > file << 'EOF' ... EOF`) got silently corrupted in
transit (lines merged, content duplicated, closing delimiters missed),
sometimes producing a file that was still *syntactically valid* Python
but quietly wrong — worse than an outright error, since nothing
complains.

This is now mostly moot day-to-day, since Claude Code edits files
directly and never needs a heredoc paste at all. But if it ever comes up
again (e.g. manually pasting something from documentation, a different
machine, a different terminal app):

- **Prefer single independent commands** over multi-line blocks —
  each one either works or visibly fails, no silent partial corruption.
- **If you must paste multi-line content, use a text editor (`nano
  file.py`), not a heredoc.** Editors just insert whatever bytes arrive;
  they don't try to parse it as shell syntax mid-paste. Watch out for
  `autoindent` if pasting indentation-sensitive code like Python — check
  `nano`'s settings if pasted code comes out mis-indented.
- **Last resort — base64 single-line encoding**, if even editor paste is
  unreliable: encode the content elsewhere as one long base64 string
  (`base64 -w0 file.py`), paste that single line, decode with `base64
  -d`. One line has nothing for line-based paste corruption to
  interleave, at the cost of being unreadable while pasting it.
- **After any manual multi-step edit, verify with something
  independent of what you just typed** — `wc -l` against a known-correct
  count, `python3 -m py_compile`, `diff` against a reference — rather
  than trusting that "no error was printed" means it's correct.

---

## 7. Quick troubleshooting checklist

| Symptom | Likely cause | Check |
|---|---|---|
| Commits not on GitHub, `git status` looks clean | No remote configured, or remote never got linked | `git remote -v` — empty means no link exists |
| `git push` asks for password, then fails | Plain password auth is disabled by GitHub | Use `gh auth login` instead |
| Committed but still not on GitHub | Committed but not pushed (separate steps) | Check status bar arrow/number, or `git log origin/main..main` |
| File edit "worked" but behavior didn't change | Paste corruption produced a valid-but-wrong file | `wc -l` / `diff` against a known-good reference, don't trust "no error" |
| Confused which VS Code terminal is doing what | Terminal mode vs. shell mode look identical at a glance | Use the Claude Code sidebar extension instead of terminal mode |
| Claude Code session doesn't know project context | New sessions have no memory | Point it at `docs/STATUS.md`, ask it to verify against real files |
