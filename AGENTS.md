# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

User-facing docs and design specs: see [docs/README.md](docs/README.md). Keep this file aligned with [CLAUDE.md](CLAUDE.md).

## Commands

```bat
REM Install venv + dependencies
install.bat

REM Run the app (no console window)
run.bat

REM Manual run (dev)
.venv\Scripts\pythonw.exe run.py

REM Run a game subprocess standalone (debugging)
python run.py --game pet <session_in.json>
python run.py --game grid <session_in.json>
python run.py --game word <session_in.json>

REM Fix pygame-ce only (when games fail to start)
fix_game.bat

REM Build redistributable .exe
build.bat

REM Run unit tests (stdlib unittest, offscreen UI regression included)
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- **Python 3.12 or 3.13** recommended. Python 3.14 **must** use `pygame-ce`, not the official `pygame` wheel (requirements.txt already pins `pygame-ce`).
- Test suite lives in `tests/` (stdlib `unittest`, no deps). `tests/test_widget_smoke.py` runs offscreen (`QT_QPA_PLATFORM=offscreen`) and guards the "clicking the goal tree resizes the window" regression. Always run the suite after touching `src/models.py`, `src/task_manager.py`, `src/reward_system.py`, `src/storage.py`, `src/runtime_intervals.py`, or widget geometry.

## Architecture

### Entry point

`run.py` is the single entry point. Without `--game`, it calls `src.main.main()` — the PySide6 Qt desktop app. With `--game`, it dispatches to `games/pet_arena.py`, `games/pixel_tactics.py`, or `games/word_arena.py` as a **subprocess** launched by the main app.

### Threading model

```
QTimer @ 50ms (main thread)
 └─ GetAsyncKeyState 轮询 ──→ _count_op() ──→ OpBridge.op_happened (Qt Signal, AutoConnection)
 └─ Application._on_operation() (main thread)
    ├─ maybe_roll(state)
    ├─ TaskManager.record_operation()
    └─ widget.refresh() + dialog refreshes
```

- **InputMonitor** uses a `QTimer` (50ms) to poll keyboard/mouse state via `GetAsyncKeyState`. No system-wide hooks. Detects first-press transitions (not hold-repeat). Mouse move counts by distance (~80 px → 1 op). VM / Raw Input fallbacks in `win_raw_input.py` / `vm_detect.py`. Falls back to pynput hooks on non-Windows.
- `OpBridge` (a `QObject`) emits a Qt `Signal`; `AutoConnection` handles thread detection automatically (polling = same thread, hook = cross-thread).
- All state mutation happens on the main thread — no manual locking needed.

### Data model (`src/models.py`)

- `AppState` is the single root state object — inventory (gold/diamond/chests/letters), task list, roll history, `roll_runtime`, `ease_chests`, settings dict.
- `Task` has `status: ACTIVE | PAUSED | COMPLETED`. Only **one** ACTIVE task is allowed at a time.
- `Task.current_subtask_id` points at the focused **leaf** subtask (when the task has a subtask tree). `Task.active_focus_path_ids()` returns the root-to-leaf path for UI highlighting.
- `Subtask` forms a tree: **leaf** nodes accumulate ops/time/rewards; **container** nodes (`children` non-empty) rollup from descendants via `rollup_operations()`, `rollup_earned()`, etc. Leaves have `target_seconds`, `pending_rewards`, `done`, `rewards_claimed`.
- `Reward` is gold+diamond plus optional crit multipliers.
- `RollAccum` tracks rewards accumulated *since* the last roll checkpoint (global display).
- `RollRuntime` holds the current roll cycle: `next_roll_at`, `roll_span`, `segment_colors`, `gold_chance` / `diamond_chance`, amount ranges, `last_shuffle_at`. Persisted; `settings` roll fields are migration-only.
- Weekly runtime intervals are **not** in `AppState`; they live in `%APPDATA%\AimLoot\runtime_intervals.json` via `src/runtime_intervals.py`.

### Operation accounting

On each keyboard/mouse op (when an ACTIVE task exists):

1. `maybe_roll(state)` may award gold/diamond (independent gold/diamond rolls at cycle end; ~8% crit with right-skewed multiplier).
2. `TaskManager.record_operation(reward)`:
   - **No subtasks**: increment `task.operations`; apply roll reward to `task.pending_rewards`.
   - **Has subtasks**: only if `task.current_subtask()` is a non-done leaf — increment that leaf's `operations` and apply roll reward to the leaf's `pending_rewards`; otherwise drop subtask rewards.
3. `tick_active_time()` (1s timer): adds seconds to the focused leaf, or to the flat task when no subtasks. Skips when paused, screen off (`power_monitor`), or idle past `settings.idle_pause_minutes` (default 10). Also feeds `RuntimeIntervalTracker` for the week view.

Folder-style accounting: parent task `earned_*` / display totals sync from subtask rollup (`sync_earned_from_subtasks`); do not mirror leaf progress into parent fields when subtasks exist. Global top-bar gold/diamond = inventory only (`AppState.visible_gold_diamond()`).

### Core modules

| Module | Role |
|--------|------|
| `src/main.py` | `Application` class: wires everything — Qt app, tray, widget, input monitor, timers, dialogs |
| `src/widget.py` | `FloatingWidget`: frameless topmost window, drag handles, global stats, roll bar/toast, right-click menu; delegates the goal tree to `GoalTreeArea` |
| `src/task_manager.py` | `TaskManager`: task/subtask CRUD, focus/start/pause/decompose/delete; idle pause; feeds runtime intervals on tick |
| `src/runtime_intervals.py` | Week-view interval log (open/close segments, week query, separate JSON file) |
| `src/reward_system.py` | `maybe_roll(state)`, crit, `reshuffle_roll_params`, random 6–14 op cycles, `RollRuntime` migration |
| `src/chest_opening.py` | Chest unlock timers + letter/currency open RNG; gold speedup / instant open / batch open |
| `src/branding.py` | `APP_NAME` / `APP_NAME_ZH` / tray tooltip / window titles |
| `src/input_monitor.py` | `InputMonitor`: QTimer + GetAsyncKeyState; mouse-move distance; VM fallbacks |
| `src/storage.py` | `load_state()` / `save_state()`: atomic JSON to `%APPDATA%\AimLoot\data.json`; copy-migrate from `%APPDATA%\Adventure`; backups / anchor / snapshots |
| `src/game_launcher.py` | `launch_pet_arena()` / `launch_pixel_tactics()` / `launch_word_arena()` |
| `src/game_protocol.py` | `GameSession` / `GameResult` JSON protocol under `%APPDATA%\AimLoot\game_sessions\` |
| `src/migrate_accounting.py` | Flat-task → nested subtask migration; `detach_subtask_progress_to_legacy` for decompose |

### UI helpers

- `src/ui_goal_tree_area.py` — `GoalTreeArea`: goal-tree region on the floating widget.
- `src/ui_week_runtime.py` — Week grid + legend in task dialog「本周」tab; top-level filter and day/week totals.
- `src/task_dialog.py` — Goal management dialog (includes week tab).
- `src/inventory_dialog.py` — Inventory, chests, letters, game entry buttons; gold speedup / instant / batch open.
- `src/ui_chest_summary.py` — Short result panel after instant / batch open (no per-chest animation).
- `src/ui_task_tree.py` — `TreeRow`, action buttons, GoalBlock QSS.
- `src/ui_goal_tree_panel.py` — `GoalTreePanel` embedded in `TaskCard`.
- `src/goal_actions.py` — `try_complete_goal`, `try_delete_goal` with confirmation.
- `src/ui_confirm.py` — `ask_yes_no`: topmost styled confirm dialog.
- `src/ui_text.py` — amounts (max 1 decimal), durations, tree/history HTML. **No emoji** — Windows default fonts render them as tofu.
- `src/ui_roll_bar.py` — `EasedProgressBar` (258–342s cycle, one closed chest at 100%) and `SegmentedRollBar`.
- `src/ui_odometer.py` / `src/currency_display.py` — top-bar count-up display.
- `src/op_tracker.py` — sliding 60s window of op timestamps (in-memory only).
- `src/active_time.py` — increments focused leaf or flat task `active_seconds` every 1s tick.
- `src/power_monitor.py` — `should_count_time()`: false when display is off.
- `src/sfx.py` — Qt Multimedia. Gold = `roll_gold.*`; diamond = `roll_diamond.*`; grid fill = `grid_full.*` then 8% overlay `ease/`; chest bag = `chest_get.*`; `op/` `ease/` `aim/` folders pick at random. Op tick / grid full / ease full only play when `TaskManager.will_count_operation()` is true (claim chest / complete goal are not gated). Defaults: `sound_op_chance=0.2`, `sound_grid_ease_chance=0.08` (legacy 0.4 / 0.25 migrate on load).
- `src/win_utils.py` — pin to all desktops (pyvda), startup registry. No-ops on non-Windows.

### Game subprocess protocol

1. Main app writes `{session_id}_in.json` → spawns `python run.py --game <pet|grid|word> <in_path>`.
2. Game reads the session file, runs, writes `{session_id}_out.json` (a `GameResult`).
3. Main app reads the result, validates `session_id`, updates inventory.

Entry costs (gold): pet 10, grid 12, word 10.

### Settings (in `data.json` → `settings`)

Key tunables: `roll_interval`, `roll_chance` / `gold_chance`, `gold_min`/`gold_max`, `diamond_chance`, `diamond_min`/`diamond_max`, `subtask_default_target_minutes`, `subtask_completion_bonus_gold`, `idle_pause_minutes`, `sound_op_chance` (default 0.2), `sound_grid_ease_chance` (default 0.08), window/sound flags. Runtime roll values live in `roll_runtime`; settings roll fields are for **legacy migration** only. Defaults in `AppState.__init__` (`src/models.py`).

### Save behavior

- Auto-save every 15 seconds (`QTimer` in `Application.__init__`); also flushes open runtime intervals.
- Save on quit.
- Atomic write: tempfile → `os.replace()` (avoids corrupting the file on crash mid-write).

## Style notes

- All `src/` files use `from __future__ import annotations` for deferred evaluation.
- Type hints throughout (`from typing import Optional, List, Dict, ...`).
- Qt stylesheets are module-level constants (e.g. `WIDGET_STYLESHEET` in `ui_widget_qss.py`).
- String formatting uses f-strings; `%APPDATA%` resolved via `os.environ` / `src.paths` / `storage.get_data_dir()`.
- Games are in `games/` and import `pygame-ce`; they receive session data via CLI arg, not stdin.
