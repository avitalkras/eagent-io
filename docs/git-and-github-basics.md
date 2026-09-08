# Git & GitHub — the workflow we used to build this repo

> You wanted to improve your git skills, so here's the *why* behind each command
> we ran, plus the everyday workflow you'll use from now on.

---

## Git vs. GitHub (they're not the same thing)

- **Git** = the version-control tool that runs **on your laptop**. It tracks
  every change to your files in a local `.git/` folder. Works fully offline.
- **GitHub** = a **website/service** that hosts a *copy* of your git repo in the
  cloud so you can back it up, share it, and collaborate.

You commit locally with **git**, then **push** to **GitHub**.

---

## Authentication: how your laptop proves it's you

We used the **GitHub CLI (`gh`)** and logged in via the **OAuth Device Flow**:

1. `gh` asked GitHub for a one-time code.
2. You entered it at `github.com/login/device` in your browser.
3. GitHub gave `gh` a **scoped, revocable token** (stored in the macOS keyring).

> 🔑 **Best practice:** never put your GitHub *password* in a CLI or a script.
> Use a token (via `gh` or a Personal Access Token). Tokens are scoped (limited
> permissions) and revocable (kill them anytime without changing your password).

---

## The core mental model: 3 areas

```
 working directory      staging area (index)        repository (.git)
 (your edited files) ──▶ (changes marked for   ──▶  (permanent snapshots
      git add            the next commit)             = commits)   git commit
```

- `git add <file>` — stage a change ("I want this in my next snapshot").
- `git commit -m "msg"` — save a permanent snapshot of the staged changes.
- `git push` — upload your commits to GitHub.

---

## Commands we ran to create this repo (annotated)

```bash
git init                       # create a new empty git repo (.git/) here
git add .                      # stage everything (respecting .gitignore)
git commit -m "..."            # first snapshot
gh repo create eagent-io \     # create the repo on GitHub under your account...
   --public --source=. \       #   ...using THIS folder as the source
   --remote=origin --push      #   ...name the remote "origin" and push
```

- **`origin`** is just the conventional nickname for "the GitHub copy". After
  this, `git push` knows where to send commits.
- **`main`** is the default branch name.

---

## Anatomy of a good commit message

```
Phase 1: add star schema DDL and learning docs

- dim/fact tables with idempotency + indexes
- dim_dates populated via generate_series
- teaching notes in docs/
```

- **First line ≤ ~50 chars, imperative mood** ("add", not "added"). Think:
  *"If applied, this commit will ___."*
- Blank line, then a body explaining **why**, not just what.
- Commit **small, logical units** — one idea per commit. Easier to review and to
  undo.

---

## The everyday workflow (feature-branch flow)

This is how professional teams work — practice it even solo:

```bash
git checkout -b phase-2-scraper     # 1. branch off main for a unit of work
# ... edit files ...
git add .
git commit -m "Add job-board scraper"
git push -u origin phase-2-scraper  # 2. push the branch to GitHub
gh pr create                        # 3. open a Pull Request (review + history)
# ... after review ...
gh pr merge                         # 4. merge into main
git checkout main && git pull       # 5. update your local main
```

**Why branch instead of committing straight to `main`?**
- `main` stays always-working. Experiments live on their own branch.
- A **Pull Request** gives you a place to review the diff, run CI (GitHub
  Actions), and keep a record of *why* a change was made.

---

## Useful inspection commands

```bash
git status            # what's changed / staged right now
git log --oneline     # compact history of commits
git diff              # unstaged changes
git diff --staged     # staged changes (what will be committed)
git branch            # list branches; * marks the current one
```

---

## `.gitignore` — keep junk and secrets out of git

Never commit: OS cruft (`.DS_Store`), virtual envs, secrets (`.env`, API keys),
build output. Our [`.gitignore`](../.gitignore) handles this. **Rule:** if it's
generated, secret, or machine-specific, ignore it.
