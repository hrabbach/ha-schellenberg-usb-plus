<!-- generated-by: gsd-doc-writer -->
# Development Guide

This guide covers everything needed to contribute code to the Schellenberg USB Integration.
For system architecture see [ARCHITECTURE.md](ARCHITECTURE.md). For test detail see
[TESTING.md](TESTING.md) (generated separately). For environment variable reference see
[CONFIGURATION.md](CONFIGURATION.md).

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | >= 3.13.2 | Runtime and toolchain |
| uv | >= 0.9.5 | Package manager, venv management |
| Git | any | Version control |
| WSL (Windows only) | 2 | Running the test suite (Linux required) |

On Windows, WSL is required for tests because `homeassistant/runner.py` imports the
Unix-only `fcntl` module. `uv run pytest` natively fails with
`ModuleNotFoundError: No module named 'fcntl'`. Do not try to fix this — it is a
fundamental platform constraint.

## Getting the Repo

```bash
git clone https://github.com/hrabbach/ha-schellenberg-usb-plus.git
cd ha-schellenberg-usb-plus
```

## Dual Venv Setup (Critical)

This project uses **two separate virtual environments** that must never be mixed:

| Venv | Path | Purpose | Where to run |
|------|------|---------|--------------|
| `$HOME/.venvs/schellenberg_usb` | WSL ext4 (`/home/holgerr/.venvs/schellenberg_usb`) | pytest and runtime deps | WSL only (via `.wsl_exec.sh`) |
| `.venv-win` | Native Windows venv | ruff, mypy, codespell | Windows only |

### Why two venvs?

The WSL venv lives on ext4 (`$HOME/.venvs/schellenberg_usb`), not on the `/mnt/c`
DrvFs mount. This lets uv hardlink from its ext4 cache — `uv sync --frozen` finishes
in seconds instead of the ~30 min a DrvFs copy-mode sync required. Running native
Windows `uv` against this WSL venv corrupts it (deletes `bin/`, cannot remove `lib64`
symlink). Keep them strictly separated.

### Creating the WSL venv

Run from PowerShell (not Git Bash — see note below):

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/<you>/Coding/schellenberg_usb/.wsl_exec.sh "uv sync --frozen"
```

### Creating the Windows venv

Run from PowerShell or native Windows terminal:

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv sync --group lint
```

## Running Tests

**Tests must run through `.wsl_exec.sh`.** Never invoke `uv run pytest` directly on
Windows — it will either corrupt the venv or produce results from an unrelated checkout.

### The helper script

`.wsl_exec.sh` does four things that make the test suite reliable:

1. Sets `HOME=/home/holgerr` — the Windows-inherited `HOME` is mangled to `C:Users…`
   and breaks uv inside WSL.
2. Sets `UV_PROJECT_ENVIRONMENT=$HOME/.venvs/schellenberg_usb` — keeps the venv on ext4
   so uv can hardlink from its cache. `uv sync --frozen` completes in ~7s; a killed
   sync cannot corrupt it.
3. Serializes every invocation through an `flock` mutex — concurrent `uv` calls queue
   instead of racing and corrupting the env. The lock lives on ext4 where `flock` is
   reliable.
4. `cd`s to the hardcoded project path — so tests always run on the main checkout, not a
   git worktree.

### Run the full suite

From **PowerShell**:

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh "uv run --no-sync pytest -p no:cacheprovider -q"
```

`--no-sync` skips uv's implicit env sync on every run. Run `uv sync --frozen` explicitly
only when dependencies change.

From **Git Bash**, prefix `MSYS_NO_PATHCONV=1` to prevent MSYS path mangling:

```bash
MSYS_NO_PATHCONV=1 wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh "uv run --no-sync pytest -p no:cacheprovider -q"
```

### Run a single file or test

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh "uv run --no-sync pytest tests/test_cover.py -p no:cacheprovider -q"
```

### Venv health check

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh "uv run --no-sync python -c 'import homeassistant.helpers, pytest'"
```

### Repair a corrupted venv

If `uv run` prints "Resolved/Prepared/Installed N packages" on a repeat run (it should
be instant), the venv is being rebuilt. Stop, check you went through the helper, then:

```powershell
wsl -e env -u HOME -u WSLENV bash /mnt/c/Users/holger.rabbach/Coding/schellenberg_usb/.wsl_exec.sh "uv sync --frozen"
```

## Linting and Type Checking

Lint and types run **natively on Windows** using the `.venv-win` venv.

### ruff (lint + format)

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run ruff check custom_components/schellenberg_usb/ tests/
uv run ruff format --check custom_components/schellenberg_usb/ tests/
```

Auto-fix lint violations:

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run ruff check --fix custom_components/schellenberg_usb/ tests/
uv run ruff format custom_components/schellenberg_usb/ tests/
```

From Bash (e.g., CI):

```bash
UV_PROJECT_ENVIRONMENT=.venv-win uv run ruff check custom_components/schellenberg_usb/ tests/
UV_PROJECT_ENVIRONMENT=.venv-win uv run ruff format --check custom_components/schellenberg_usb/ tests/
```

### mypy

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run mypy custom_components/schellenberg_usb/ tests/
```

### codespell

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run codespell custom_components/schellenberg_usb/ tests/ README.md CONTRIBUTING.md
```

The ignore-words list for domain-specific terms (e.g., `hass`, `ser`, German tech terms)
is configured in the `[tool.codespell]` section of `pyproject.toml`. Add new legitimate
terms there if codespell flags them.

## Quality Gate

No pre-commit hook is installed (`pre-commit install` was never run), so `git commit`
fires no hooks. Run the following manually before every commit:

1. `ruff check` — lint (native Windows, `.venv-win`)
2. `ruff format --check` — formatting (native Windows, `.venv-win`)
3. `mypy` — type checking (native Windows, `.venv-win`)
4. `codespell` — spell check (native Windows, `.venv-win`)
5. `pytest` — test suite (WSL, via `.wsl_exec.sh`)

All five must pass before pushing. See [CONTRIBUTING.md](../CONTRIBUTING.md) for the
canonical gate commands.

## Build Commands

| Command | Venv | Description |
|---------|------|-------------|
| `uv sync --frozen` | WSL ext4 venv | Install/repair the WSL test venv |
| `uv sync --group lint` | `.venv-win` (Windows) | Install/repair the Windows lint venv |
| `uv run --no-sync pytest -p no:cacheprovider -q` | WSL ext4 via helper | Run full test suite |
| `uv run ruff check custom_components/schellenberg_usb/ tests/` | `.venv-win` | Lint |
| `uv run ruff format --check custom_components/schellenberg_usb/ tests/` | `.venv-win` | Check formatting |
| `uv run ruff check --fix …` | `.venv-win` | Auto-fix lint violations |
| `uv run ruff format …` | `.venv-win` | Auto-format |
| `uv run mypy custom_components/schellenberg_usb/ tests/` | `.venv-win` | Type check |
| `uv run codespell custom_components/schellenberg_usb/ tests/ README.md CONTRIBUTING.md` | `.venv-win` | Spell check |

## Code Style

- **Formatter:** ruff-format (configured in `pyproject.toml`)
- **Linter:** ruff (rules configured in `pyproject.toml` `[tool.ruff.lint.pycodestyle]`)
- **Max line length:** 80 characters (`[tool.ruff.lint.pycodestyle]` in `pyproject.toml`)
- **Type checker:** mypy 1.18.2+ (configured in `pyproject.toml` `[tool.mypy]`)
- **Spell checker:** codespell 2.4.1+ (ignore-words list in `pyproject.toml` `[tool.codespell]`)

### Key conventions

- `from __future__ import annotations` at the top of every module
- `| None` syntax, not `Optional[X]`
- `dict[str, X]` not `Dict[str, X]`
- Full type hints on all parameters and return values (including `-> None`)
- `snake_case` for functions, variables, and module names
- `UPPER_SNAKE_CASE` for constants
- Leading underscore for private attributes and callbacks (`self._transport`, `_handle_message`)
- `async_` prefix for async functions (`async_setup_entry`, `async_dispatcher_connect`)
- `@callback` decorator on synchronous dispatcher callbacks
- `%s` placeholders in log messages, never f-strings (log formatting is lazy)
- Relative imports within the integration: `from .const import DOMAIN`
- Absolute imports for HA framework: `from homeassistant.core import HomeAssistant`

### Logging

```python
_LOGGER = logging.getLogger(__name__)

# Correct — lazy formatting
_LOGGER.debug("Setup entry called for entry: %s", entry.entry_id)

# Wrong — do not use f-strings in log calls
_LOGGER.debug(f"Setup entry called for entry: {entry.entry_id}")
```

## Module Layout

```
custom_components/schellenberg_usb/
├── __init__.py                  # Integration setup/teardown, subentry tracking
├── api.py                       # Serial connection, protocol encoding/decoding,
│                                # command queue, pairing, device enumeration
├── config_flow.py               # Initial hub setup (serial port selection)
├── const.py                     # Constants, type aliases, dispatcher signal names
├── cover.py                     # Cover entities; position tracking; calibration
│                                # persistence (HA storage)
├── options_flow.py              # Hub options (change serial port)
├── options_flow_calibration.py  # Manual open/close time measurement flow
├── options_flow_pairing.py      # Device pairing workflow and subentry creation
├── options_flow_timed_calibration.py  # Timed calibration variant
├── sensor.py                    # USB stick status sensors
├── switch.py                    # LED switch entity
├── manifest.json                # Integration metadata and version
├── strings.json                 # UI string keys
└── translations/                # Localized UI strings

tests/                           # pytest test suite (WSL only)
```

For a deeper explanation of how these modules interact see [ARCHITECTURE.md](ARCHITECTURE.md).

## Branch Conventions

Feature branches follow the pattern `feat/<short-description>` or `fix/<short-description>`.
Phase branches use `gsd/phase-NN-<name>` (these are local workflow branches and are never
pushed directly to origin).

Submit changes as pull requests against `main`. No convention is enforced by tooling —
follow the pattern of existing branches visible in `git branch -a`.

## PR Process

- Open a PR against `main` on GitHub.
- Ensure all four quality-gate checks pass locally before requesting review (ruff, ruff
  format, mypy, pytest).
- The PR description should explain the motivation for the change and any non-obvious
  implementation decisions.
- Reviewers check correctness, HA integration conventions, and test coverage.
- Merge with a merge commit (not squash) to preserve history.

For releasing a merged PR as a HACS update, bump `manifest.json` version in the PR
(semver: new feature → minor, fix-only → patch, breaking → major), then tag the merge
commit `vX.Y.Z` and push the tag. The release workflow publishes automatically.
