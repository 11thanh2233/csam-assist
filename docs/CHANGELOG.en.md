# Changelog

English version of `docs/CHANGELOG.md`: one `## <version>` section per release, same versions. The app shows these
sections in the "What's new" box when the interface language is English, and the release notes on GitHub include both.

## 0.9.1 (2026-11-20)

- **Renamed to SAM3 Label Assist** (formerly SAM3 CVAT Assist), because the tool will support other labeling platforms.
  - Updating from an older version keeps your data, saved Hugging Face token, CVAT login and start-with-Windows setting.
  - Desktop and Start Menu shortcuts use the new name; old shortcuts are removed.
  - The installer is now `SAM3-Label-Assist-Setup-<version>.exe`. Releases also include a copy with the old name so
    apps up to 0.9.0 can still update themselves.
  - Tampermonkey users: the script keeps its old name in Tampermonkey so two panels never run at once.
- Fixed: "just now" in recent jobs was not translated in English.

## 0.9.0 (2026-11-20)

Details and test results are in `docs/CHECKLIST_0.9.0.md` (Vietnamese).

- **Fixed: Start backend / Open CVAT did nothing** (since 0.8.0). The bug was in the app, not a missing part of your
  computer. The installed app does not need `Set-ExecutionPolicy` or `run.ps1`.
- **Redesigned control panel** following the approved mockup: six pages in a sidebar (Overview, Labeling sites,
  Components, Team sharing, Support, Settings) and its own icon set.
  - Overview: backend status, graphics card, VRAM, GPU temperature; quick open for labeling sites and recent jobs.
  - When the backend fails, it says why (for example, missing Microsoft Visual C++) and lists the fix steps, each with
    a button.
  - Support: create an error report with a description, view logs inside the app (filter, errors only, copy).
  - Settings: switches and segmented choices; new light/dark/follow-Windows theme.
- **The backend restarts by itself if it stops unexpectedly** (up to 3 times in 10 minutes) and tells you through the
  system tray. You can turn this off in Settings.
- **Shortcuts:** Ctrl+K finds and runs any action, Alt+1…6 switches pages. The app remembers the open page.
- **Dialogs are easier to close everywhere:**
  - control panel: Esc, click outside or ✕;
  - panel in CVAT: its own dialogs instead of the browser's (Esc, click outside, Enter to confirm).
- **Every button gives feedback:** a spinner while working, a notice when done. Deleting a labeling site can be undone.
- Labeling sites show their platform (CVAT for now), ready for other labeling platforms.

## 0.8.3 (2026-11-20)

Details and test results are in `docs/CHECKLIST_0.8.3.md` (Vietnamese).

- **New logo:** an S drawn as a polyline with vertices.
  - Used for the exe, installer, shortcuts, tray, app window, the panel in CVAT and the backend's browser tab.
  - Small sizes (16–24 px) use a thicker variant so they stay legible.
- **The taskbar shows the SAM3 CVAT Assist icon and name**, no longer Python's.
- **Splash screen with the logo** appears as soon as the app opens and closes when the app window shows.
- **Setup window redesigned from the mockup:**
  - dark blue left column with the logo and 6 steps; finished steps get a ✓, the current step is highlighted;
  - Cancel / Back / Next bar fixed at the bottom;
  - the download page shows overall progress, speed, time left, each component's state and a collapsed log;
  - a "Ready" page at the end.
- **Control panel** in the same style: a left column with links to each section; light/dark follows Windows.
- **Installer** carries the brand strip and logo; shortcuts group under one taskbar icon.

## 0.8.2 (2026-11-20)

- **Installer language picker fixed:** version 0.8.1 showed "English" twice; it now shows "Tiếng Việt" and
  "English". Vietnamese Windows preselects Tiếng Việt.

## 0.8.1 (2026-11-20)

Details of every item, how it was tested and the bugs fixed are in `docs/CHECKLIST_0.8.1.md` (Vietnamese).

- **Full English interface.** Choose English in the panel's Settings tab, at the top of the app's control panel, or
  when the installer starts. One choice covers the panel, the control panel, the tray, notifications and "What's new".
  - Everything is translated: panel, control panel, installer, backend messages, profile names, guideline review page.
  - Draft guidelines and prompts sent to the LLM follow the chosen language.
- **Stable error codes.** Every error carries a technical code (e.g. `E_BACKEND_UNREACHABLE`, `E_HF_NOT_APPROVED`,
  `E_GPU_OOM`), the same in every language, for easier lookup and bug reports.
- **Logs are always technical English**, even when the interface is Vietnamese; so is the error report bundle.
- **Click points uses SAM 3 Tracker.** SAM 3's own point model, already included in the downloaded weights; nothing
  extra to download.
  - Loaded only when you click points, released after 5 minutes of inactivity.
  - Falls back to the previous method if VRAM is short or it fails.
- **Installer signing prepared:** the build supports SignPath and PFX certificates; see `docs/SIGNING.md`.
- **For developers:** new Vietnamese strings in the code without a translation make the tests fail
  (`scripts/i18n_extract.py`, `scripts/i18n_build.py`).

## 0.8.0 (2026-11-20)

37 improvements; details, tests and fixed bugs for each are in `docs/CHECKLIST_0.8.0.md`.

- **Faster frame-by-frame labelling.**
  - The tool pre-computes suggestions for the next frame while you review, so they are ready when you move on.
  - **Alt+Enter**: save, then go to the next frame.
  - Review with the keyboard (Alt+J/K select, Alt+X tick, Alt+Delete drop), or hold Alt and click the image.
  - Threshold slider and class hide buttons filter instantly.
  - Undo up to the last 20 saves (Alt+Z).
- **Labels right the first time.**
  - The tool runs QA rules before saving.
  - Auto-fills truncated/occluded when the label has those attributes.
  - **Click points** to segment an object.
  - Polygon vertex limits and smoothing per profile.
  - Warns when this frame has fewer objects than its neighbours.
- **Long jobs.**
  - Whole-job suggestions can be paused / resumed, even after closing the browser, with time remaining.
  - Review whole-job results as a thumbnail grid.
  - Notification when a long job finishes.
- **Desktop app.**
  - Model preloading and warm-up; releases VRAM when idle (15 minutes by default).
  - Opens CVAT directly; recent jobs list.
  - Windows notifications.
  - GPU meter (VRAM, temperature) on the panel.
- **Support and teams.**
  - One-click error report (no tokens, keys, cookies or images).
  - Backup / restore; sync profiles, guidelines and lessons through a shared folder.
  - One GPU machine serves the whole team on the LAN (with a key); machines without a GPU use the other machine's backend.
  - Reviewer mode highlights tool labels nobody has edited.
  - Learning separated per person.
  - CSV productivity report.
- **Smart data.**
  - Suggests frames to do first (unusual, hard, unlabelled).
  - Motion keyframes, and whole-job suggestions on keyframes only.
  - Find frames by text (SAM3).
  - Agreement between two jobs.
- **Project custom model.** A small detector learning from the box labels you reviewed. Only classes that reach the bar are used, many times faster than SAM3.
- **Platform.**
  - Label Studio connector (ML backend).
  - Installer signing step turns on when the repo has a certificate.
  - English interface (partial).
  - Runs from source on macOS/Linux.
  - Custom shortcuts, font size, high contrast.
  - First-run tour and session statistics.

## 0.7.0 (2026-11-19)

- **Nothing already present is downloaded again.**
  - Before downloading, the app scans the machine, including the Hugging Face cache of old manual installs
    (0.3.x – 0.5.x), old tool folders in Downloads, Desktop, Documents, and folders you add.
  - SAM 3, ViTPose and face model weights already present are reused after their hashes are verified.
  - Reuse is done with hard links, so no extra disk space is used.
- **Only what is needed gets updated.**
  - The first installer step lists every component: present and up to date, needs updating, or reusable, with the download size.
  - PyTorch is pinned: a new app only asks to download again when it really needs a different PyTorch.
  - When new weights appear on Hugging Face, only changed files are downloaded.
  - The Hugging Face token is only needed when the SAM 3 weights really must be downloaded.
- **The app checks for and installs its own updates.**
  - Once a day, the app asks GitHub Releases whether there is a new version.
  - If so it shows the notes and an Update button: the app downloads the installer, verifies its SHA-256, installs in update mode and reopens.
  - PyTorch, weights, profiles and learning data stay as they are.
  - Can be turned off in the control panel.
- **"What's new" box** shown once after each update, combining the versions you skipped.

## 0.6.0 (2026-11-19)

- **Single-file Windows installer** (about 136 MB). No Python, PowerShell or Tampermonkey needed.
- **Component downloader at the last installer step:**
  - machine check;
  - SAM licence;
  - Hugging Face token check (approved by Meta or not);
  - downloads only the heavy parts (PyTorch, SAM 3 weights, skeleton models if chosen), with a progress bar and resume after network drops.
- **Dedicated CVAT window** with the tool's panel built in. A list of CVAT addresses to add or remove.
- **Control panel:**
  - start/stop the backend, view logs;
  - manage components;
  - run in the system tray, start with Windows.
- **Safety:**
  - every downloaded file is hash-checked;
  - the token is only sent to huggingface.co;
  - the backend listens only on this machine, has a random API key and runs offline.

## 0.5.0

- **Learns from the user's actions:** keep, edit, relabel, delete, untick, exclude, objects drawn by hand. The tool
  adjusts thresholds and box drawing per label.
- **Whole-job check** in the Check tab.
- **Video tracking with SAM 3 Video** (experimental).
- **Exclusion in Find similar.**

## 0.4.4

- **Backend security review:** DNS rebinding blocked, open CORS removed.
- **Reads the job's real frames.**
- **Duplicate suggestions prevented.**

## 0.4.0

- **Works on local CVAT** and other CVAT sites.
- **Imports guidelines and `taxonomy.json`** for new tasks.
- **Whole-job suggestions** for chosen labels.
- **Profile management.**
