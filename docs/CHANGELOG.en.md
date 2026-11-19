# Changelog

English version of `docs/CHANGELOG.md`: one `## <version>` section per release, same versions. The app shows these
sections in the "What's new" box when the interface language is English, and the release notes on GitHub include both.

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
