# Changelog

English version of `docs/CHANGELOG.md`: one `## <version>` section per release, same versions. The app shows these
sections in the "What's new" box when the interface language is English, and the release notes on GitHub include both.

## 0.9.6 (2026-11-24)

Details and test results are in `docs/CHECKLIST_0.9.6.md` (Vietnamese). This release makes cuboid 3D jobs usable,
keeps the backend from slowing down over time, stops tracking from failing on timeouts, and sets occluded / outside
from the share of the body that is hidden.

- **Quality levels** (panel Settings, like game graphics settings): Auto / Light / Balanced / Quality / Max. Auto picks
  from the GPU's VRAM. The level sets prompts per label, tracking density and which extra models run.
- **No slowdown over time:** when VRAM is short the backend releases the least recently used extra model before loading
  another; it returns spare memory after heavy runs and cleans up when idle. Settings show VRAM use and loaded models.
- **Tracking survives timeouts:** per-frame results are saved, so a stopped or failed run resumes where it left off. A
  run over 5 minutes is cancelled on the backend and retried frame by frame.
- **occluded / outside from the % of the full body hidden**, per frame:
  - thresholds come from the guideline (e.g. "over 35% hidden is occluded, over 60% is outside"), else 35% / 60%;
  - edit them under "Occlusion thresholds" in the Whole job tab (remembered per task);
  - hysteresis between states; a single odd frame does not flip the state;
  - a person who reappears after outside keeps the same ID (before, outside lasted to the end of the video);
  - frames where the model missed someone: SAM3 looks again at the predicted spot; found means % hidden is computed,
    not found means outside.
- **New optional models** (install under Components in the app):
  - DINOv2-small re-identification: IDs follow appearance, fewer swaps between similarly dressed people;
  - Depth Anything V2 Small: knows who stands in front, so only the person behind counts as occluded;
  - RTMW whole-body pose (133 points, CPU): skeletons with hand / foot points; fills the left / right attribute of
    hand boxes.
- **Cuboid 3D jobs reworked:**
  - height must match the size table; length / width may be smaller (occlusion) but not larger; clusters matching no
    class are dropped instead of forced into one;
  - only the rear / part of an object seen: heading fixed, size extended to the table, centre moved to the hidden side;
  - ranges per class (vehicles ≤ 50 m, pedestrians / two-wheelers ≤ 40 m, barriers ≤ 30 m, or from the guideline);
  - minimum points by distance; ground per 8 m cell; floating clusters (signs, tree crowns) dropped; barrier rows split;
  - camera images (CAM_FRONT, CAM_BACK…): camera heading detected automatically, SAM3 confirms objects, dumpsters /
    portable toilets get unticked;
  - objects you drop when saving are remembered and unticked next time;
  - top-view minimap, suggestions grouped by class, sorted by confidence, weak ones hidden by default;
  - "Check 3D job data" and "Tune from your cuboids";
  - Label tab for 3D jobs: edit size table, range, minimum points, camera prompt per label.

## 0.9.5 (2026-11-24)

Details and test results are in `docs/CHECKLIST_0.9.5.md` (Vietnamese). The direction of this release: the tool adapts
to each project's guideline and to how YOU settle labels, instead of hand-written rules per label.

- **Claude reviews the image** (needs an Anthropic API key in the app's Settings; turn it off with the "Claude reviews the
  image" box in the Suggest tab):
  - each suggestion sends the frame with numbered proposals, plus zoomed crops of small objects, together with the
    guideline, labels, attributes and your past corrections;
  - Claude fills attributes and tags with the task's own values;
  - it judges `occluded` / `outside` on the object itself, and on each skeleton part (sunglasses, a hat, a moustache
    over the mouth…);
  - it suggests dropping what the guideline excludes (people in photos, too little visible);
  - it points to objects SAM3 missed so SAM3 redraws them;
  - a daily call limit applies (500 by default, change it in the app's Settings).
- **Project memory** (Learning tab):
  - the tool remembers the values you settle on at each Save, compared with what it suggested;
  - it learns file-name conventions (e.g. `_MVL` = left mirror camera) and keeps tags across an image sequence;
  - Claude summarises corrections into project "lessons"; you can also write rules and delete wrong ones.
- **Pick attributes in the panel:** each proposal has a dropdown per attribute (tool / Claude values pre-selected, yellow
  boxes need your choice); click `occluded` / `visible` to switch. No more fixing them in CVAT after saving.
- **occluded judged on the object itself:** two overlapping boxes no longer set `occluded`; they are flagged for review
  (or Claude decides).
- **Face skeletons:** sunglasses over the eyes no longer drop the nose and lips too. The "below threshold, whole skeleton
  Outside" rule only applies when the guideline sets a threshold for that skeleton. Hats and moustaches are now occluders.
- **Body skeletons:** people with too few visible points, or inside a picture frame, poster, screen or mirror, start unticked.
- **Polygons / polylines follow the guideline:** when creating a Project profile, the tool reads definitions to choose
  how shapes are built:
  - merge parts into one area (a crosswalk is one polygon around its stripes);
  - road area connected to the car (drivable comes out on its own, with a current lane / adjacent lane attribute);
  - the road area's edge as road_curb;
  - paint inside a crosswalk is not a lane.
- **Tags:** fisheye images are recognised with only two dark corners; when the direction is unknown, you choose instead
  of getting `front`.
- **Faster tracking, same accuracy:**
  - SAM3 runs in bf16 / fp16 on GPU, about half the VRAM;
  - "Smart" mode runs SAM3 on keyframes, checks inside each gap, and runs every frame where interpolation drifts;
  - pick "Every frame" to run as before.

## 0.9.4 (2026-11-23)

Details and test results are in `docs/CHECKLIST_0.9.4.md` (Vietnamese).

- **Project profile** (Guideline tab, first section): the tool builds project-specific suggestions from the task's own
  labels, with no taxonomy file and no labels needed beforehand:
  - one job can mix boxes, polygons, polylines, masks, **skeletons** (17-point body, 13-point face…) and **image tags**;
  - skeleton points are matched by point name (e.g. `left_eye_outer`, `mouth_left`), following the guideline's
    left/right convention;
  - attributes are auto-filled when measurable from the image (touches border, occluded, fisheye edge, light colour);
    the rest are left for you;
  - minimum size is read from the guideline (e.g. people under 60 px are not suggested);
  - **Try on this frame** before **Save for project**; every job of the project then uses it.
- **Image tags** (camera, timeofday, weather, modality…): the tool guesses measurable values (infrared, brightness,
  fisheye, blocked camera) and marks the values you must choose.
- **3D cuboids on CVAT 3D (LiDAR) jobs:** cuboids built from the point cloud (road removal, clustering, class from the
  guideline's size table), whole-job tracks with locked size, yaw from motion, interpolation through up to 6 hidden
  frames. Nothing extra to install.
- **Better tracking:**
  - **choose the people to follow** (P1, P2…) on the frame where they are clearest: only they are tracked, before and after;
  - **ignore zones**: outline mirrors, glass, billboards, screens;
  - people standing still for the whole job (posters, reflections) are flagged and unticked;
  - bridges frames the model missed and sets `occluded` / `outside` from the gap and the image border;
  - SAM3 Video: no more `E_BACKEND_TIMEOUT` on long jobs, a Stop button, noisy masks cleaned up;
  - "Smart data" on video jobs has **Track the whole job (as tracks)**.
- **Guideline drafting:**
  - works without labels; the number of sample images is chosen automatically;
  - questions show progress, can be skipped and edited, and preview what each answer changes in the draft;
  - objects named in your dataset purpose default to **Add as a new class**; two names for one object
    (`person` / `pedestrian`) are asked once.
- **Guideline import** now reads skeleton sections with point-name tables, an "image tags" row as separate tags, attribute
  columns, "CVAT type" columns and tick-box checklists.
- **Learning:** learns from tracks, from skeleton points you keep dragging, and from auto-filled attributes you keep
  changing. The **custom model** no longer scores 0%: a new **Starting weights for the custom model** component, enough
  training steps even with few images, reasons shown when a class is not usable yet; polygons and masks are used too.
- **Panel:**
  - drag the left / bottom edge to resize, or pick Compact / Medium / Wide;
  - flags are short chips, same-type flags grouped with a count; long explanations fold away;
  - "Do first" groups frames with the same reason; unticked tracks are hidden;
  - a manually chosen profile is remembered per task; the status bar no longer cuts text off.
- **App and installer:**
  - the **Support** page and installer step 1 check the computer: Windows, disk, RAM, Visual C++, WebView2, WSL2, Docker;
  - the installer installs WebView2 when missing and offers to install WSL2 (for local CVAT);
  - CUDA GPU errors (`illegal memory access`): the backend restarts itself when "Restart the backend automatically" is on.
- **Fixed:**
  - the copy log button still said "Could not copy": it now copies directly in the app's browser, falling back to
    `clip.exe`;
  - switching jobs in CVAT could keep the previous job's frame range ("Scan 13 images" although 50 was set);
  - the repeated `kernels library is not installed` notice is removed from the log.

## 0.9.3 (2026-11-22)

Details and test results are in `docs/CHECKLIST_0.9.3.md` (Vietnamese).

- **AI guideline drafting can be turned on in the app** (before, only environment variables enabled it, so the panel
  always said "The backend has no LLM configured"):
  - **Settings → AI guideline drafting**: choose Claude (Anthropic) or an OpenAI-compatible server (Ollama, LM Studio…);
  - the API key is stored in Windows Credential Manager, never written to a file or included in error reports;
  - the **Test** button sends a short message (no images) to confirm the key and address;
  - the panel points to this setting while AI is off. Sample images are still only sent when you tick consent.
- **CVAT on this computer (localhost):** pressing **Open** while CVAT is not running no longer opens a connection error
  page. Instead the app:
  - checks each step: WSL2, Docker Desktop installed, Docker running, CVAT containers, CVAT responding;
  - offers **Start CVAT**: opens Docker Desktop, starts the CVAT containers (or `docker compose up -d` in the CVAT folder
    you choose), waits until CVAT is ready, then opens the site;
  - links to the official install guides when WSL2, Docker or CVAT is missing;
  - also applies when the app opens CVAT at startup, to **Continue** on a recent job and to **Open CVAT** in the tray.
- **Fixed:** copying text (copy log button, copying from the panel) failed with `ctypes.ArgumentError: OverflowError:
  int too long to convert` on 64-bit Windows.

## 0.9.2 (2026-11-20)

Details and test results are in `docs/CHECKLIST_0.9.2.md` (Vietnamese).

- **The panel in CVAT is redesigned to be roomier and easier to read** (following the approved mockup):
  - six tabs with icons; Settings moved to a gear button in the panel corner;
  - the Job / Frame line and the profile box fit on one row;
  - **the save bar stays at the bottom**: a large "Save N, next frame" button, with Save only, Undo and Discard below;
  - long tabs (Whole job, Check, Guideline, Learning) are split into sections you open or fold; the panel remembers them;
  - Settings are grouped (Connection, While reviewing, When saving to CVAT, Display) with switches; rarely used options
    are under Advanced.
- **Copying and selecting text works in the app:**
  - the copy-log button works (it used to say "Could not copy");
  - you can select logs, keys, licenses, and text in the CVAT window.
- **Logs show only the current run by default**, so errors from earlier runs don't mix in. Click "This run" to see all
  runs.
- Releases have a single exe (`SAM3-Label-Assist-Setup-<version>.exe`). Apps up to 0.9.0 download the new version once
  with the "Open Releases page" button.

## 0.9.1 (2026-11-20)

Details and test results are in `docs/CHECKLIST_0.9.1.md` (Vietnamese).

- **The installer installs Microsoft Visual C++ by itself** when it is missing (Windows asks for admin rights once),
  instead of only reporting an error.
- **Quick check after installing:** the "Ready" step runs SAM 3 on a sample image and reports "Everything works" or the
  error with how to fix it. It can be run again from the Support page.
- **Safe updates:**
  - before installing a new version, the app backs up profiles, guidelines, learning data and settings (keeps 3);
  - if the new version does not start, the app goes back to the previous one and tells you;
  - settings from every older version load; a broken settings file is kept instead of overwritten.
- **About page:** version, the SAM 3 license (summary and full text) and every bundled software and model. Open it by
  clicking the version number in the sidebar.
- **Work stats on the Overview:** labels saved today, frames, estimated time saved, a 7-day chart, and CSV export for
  team reports.
- **Clear notice when the panel and backend versions differ:** says which one is older and how to update (Tampermonkey,
  the app, or the shared machine).
- **User guide for annotators** with screenshots: `docs/HUONG_DAN.md` (Vietnamese; from the app: About → User guide).
  New privacy page `docs/PRIVACY.md`.
- Fixed: the word "null" showed under the Job/Frame line in the panel.
- Fixed: panel stats counted days in UTC (in Vietnam, work between midnight and 7 am went to the previous day).
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
