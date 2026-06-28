# Contributing

Thanks for helping improve the Schellenberg USB integration. This document is the
canonical guide to the local quality gate every change must pass before it is merged.

## Development Setup

- **Python 3.13.2+**
- **[uv](https://docs.astral.sh/uv/)** for dependency management (`uv sync` installs the
  `dev` group, which pulls in both `test` and `lint`).
- **Windows contributors:** the test suite must run under **WSL2 (Ubuntu)** — see
  [Why WSL for tests?](#why-wsl-for-tests) below. Linting and type-checking run natively
  on Windows in a separate `.venv-win` environment.

## Quality Gate

There is no pre-commit hook — the gate is run manually. All four checks must pass before a
change is merged. Tests run under Linux/WSL; lint, type-check, and spell-check run natively.

### 1. Tests (Linux / WSL)

On Linux:

```sh
uv run pytest -p no:cacheprovider -q
```

On **Windows**, run the same command inside WSL — the Home Assistant test harness needs a
Linux environment. Keep the venv on the WSL **ext4** filesystem (not `/mnt/c`, which is slow
DrvFs) so uv can hardlink from its cache and sync in seconds. The maintainer wraps this in a
small `.wsl_exec.sh` helper (which sets a clean `HOME`, points `UV_PROJECT_ENVIRONMENT` at an
ext4 venv, serializes calls with `flock`, and `cd`s into the repo) and invokes it as:

```powershell
wsl -e env -u HOME -u WSLENV bash .wsl_exec.sh "uv run --no-sync pytest -p no:cacheprovider -q"
```

`--no-sync` skips uv's implicit env sync on every run; run `uv sync --frozen` explicitly only
when dependencies change.

### 2. Lint (native Windows / `.venv-win`)

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run ruff check custom_components/schellenberg_usb/ tests/
uv run ruff format --check custom_components/schellenberg_usb/ tests/
```

### 3. Type check (native Windows / `.venv-win`)

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run mypy custom_components/schellenberg_usb/ tests/
```

### 4. Spell check (native Windows / `.venv-win`)

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run codespell custom_components/schellenberg_usb/ tests/ README.md CONTRIBUTING.md
```

codespell configuration (the ignore-words list for intentional domain terms) lives in the
`[tool.codespell]` section of `pyproject.toml`. If codespell flags a legitimate term, add
it to `ignore-words-list` there.

## Why WSL for tests?

The Home Assistant test harness imports the Unix-only `fcntl` module
(`homeassistant/runner.py`), so the suite cannot run on native Windows — `uv run pytest`
there fails with `ModuleNotFoundError: No module named 'fcntl'`. Running the tests inside
WSL (or on any Linux/macOS host) avoids this. Lint, type-check, and spell-check have no
such constraint and run natively for speed.

## Commit Conventions

- Keep commits focused — one logical change per commit.
- Use conventional-commit prefixes (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`,
  `test:`, `style:`).
- Run the full quality gate before pushing.

## Code Style

All style rules are configured in `pyproject.toml`:

- **Formatter / linter:** `ruff` — max line length 80, `ruff-format` for formatting.
- **Type checker:** `mypy` (Python 3.13 strict-ish mode, `follow_imports = "silent"`).
- **Spell checker:** `codespell` — domain-specific ignore list in `[tool.codespell]`; add
  legitimate terms there rather than using `# noqa`.

Run the commands from the [Quality Gate](#quality-gate) section. There is no pre-commit hook,
so you must run them manually.

## Pull Request Process

The repository lives at **https://github.com/hrabbach/ha-schellenberg-usb-plus**.

1. **Fork** the repository and create a feature branch from `main`:
   `git checkout -b feat/my-change`
2. Make your changes and run the full quality gate (all four checks must pass).
3. Open a pull request against `main` on GitHub.
4. Include in the PR description:
   - What the change does and why.
   - Which quality-gate checks you ran and that they passed.
   - Any manual testing performed (hardware UAT is noted as post-merge for hardware-dependent
     changes).
5. PRs should contain **code only** — do not include `.planning/` or `.claude/` directories
   (these are local-only and blocked by the repo's pre-push hook).
6. A maintainer will review and merge with a merge commit once checks pass.

## Reporting Issues

File bugs and feature requests at:
**https://github.com/hrabbach/ha-schellenberg-usb-plus/issues**

When reporting a bug, include:

- Home Assistant version.
- Integration version (from **Settings → Devices & Services → Schellenberg USB**).
- Steps to reproduce, expected behaviour, and actual behaviour.
- Relevant log output (enable debug logging for `custom_components.schellenberg_usb` if
  possible).
