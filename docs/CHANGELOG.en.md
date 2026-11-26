# Changelog

English version of `docs/CHANGELOG.md`: one `## <version>` section per release, same versions. The app shows these
sections in the "What's new" box when the interface language is English, and the release notes on GitHub include both.

## 0.9.12 (2026-11-26)

Patch: Claude in 3D jobs no longer reports "Claude's reply is not valid JSON". Details in `docs/CHECKLIST_0.9.12.md`
(Vietnamese).

- **Cause:** the tool called Claude with a 4,000-token limit for the reply. Current models always think before they
  answer, and thinking counts toward that limit: with Claude Fable 5.2 (high thinking by default) Claude used up the
  room before finishing the JSON, the reply was cut off, every batch failed and only the geometry result was left.
- **No token limit in the tool any more:** every Claude call uses the model's maximum; you only pay for what Claude
  actually writes. Calls are streamed so long replies don't time out.
- **Default is Claude Opus 5.5, effort medium.** **Settings → AI guideline drafting** has a **Model** box (suggests
  Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.2) and an **Effort** box (Low / Medium / High / Max / Model default).
  Changing model or effort **applies immediately**, no backend restart. If you had typed a model yourself (for example
  `claude-fable-5-2`) it is kept; pick another one if you want to change it.
- **The panel shows which Claude is running:** the chip "Claude AI: on · claude-opus-5-5 · effort medium" in 3D jobs,
  and the same text on the "Claude views images" box in the Suggest tab.
- **Log when Claude's reply is broken:** the backend logs model, effort, stop reason (`stop_reason`), token counts
  (including thinking) and the first 300 characters of the reply; never the API key. An unreadable reply is retried
  once; if it is still broken, the panel reports it with the stop reason.

## 0.9.11 (2026-11-26)

Details and test results are in `docs/CHECKLIST_0.9.11.md` (Vietnamese).

- **Write "Shape per frame" in 3D jobs:** step 3 of the Whole job tab has **Write as: Track / Shape per frame**. Shapes
  use the same session (cameras, Claude, 3 buckets): each ticked track becomes one cuboid on every frame where the tool
  saw the object, size locked from the clearest frame. Occluded (interpolated) frames are only written when "Also write
  interpolated frames" is ticked. Cuboids already in the job (same label, under 1 m apart) are not duplicated. Objects
  seen in only one frame appear in Suspected noise, unticked. Default: image jobs write shapes, video jobs write tracks;
  the whole write can be undone.
- **Claude reads the project's guideline:** the guideline you imported in the Guideline tab (project profile, or
  "Import guideline") is sent to Claude in 3D jobs: each label's definition and the relevant excerpts (about 6,000
  characters). The "Claude reads guideline" line under the Claude box lets you choose **Relevant excerpts / Whole
  guideline / Don't send**. Jobs without a guideline work as before and the panel suggests importing one. With
  Anthropic, the guideline part is cached between batches (cheaper after the first).
- **CenterPoint models exported on Colab now load:** files exported from mmdet3d failed with `MatMulBnFusion_Gemm`;
  the backend retries with graph optimization off. If the 3D model still fails, the panel says "3D model failed —
  using geometry" with the reason.
- **New CenterPoint export instructions:** Colab now runs Python 3.13, so the old `mim install` steps failed. The
  script and the new notebook `scripts/export_centerpoint_onnx.ipynb` create a separate Python 3.10 with uv, on CPU.

## 0.9.10 (2026-11-26)

Patch: a 3D cuboid session can now continue from where it stopped. Details in `docs/CHECKLIST_0.9.10.md` (Vietnamese).

- **Claude no longer times out:** the "Phase 3/3: Claude recognition & audit" step used to be ONE request for every
  track, so jobs with many tracks easily went past 5 minutes and timed out. The panel now sends batches of 8 tracks,
  one short request each, and shows progress "Phase 3/3: Claude … (16/40 tracks)".
- **"Pause / Cancel" really stops:** it stops after the running batch (also between Claude batches); finished work is
  kept on the backend.
- **"Continue from where it stopped" button:** shown after you pause, or after a timeout or network error midway. It
  only runs what is missing: frames not yet sent / built, tracks not yet cross-checked with cameras, tracks not yet
  reviewed by Claude (and re-reviews tracks where Claude failed last time). Plus "Show results so far" to review the
  finished part right away, and "Drop this session".
- **Reloading the page (F5) can still continue:** the session id is remembered per job for 2 days. If the backend has
  restarted (session lost), the panel says so and starts over.

## 0.9.9 (2026-11-26)

Small patch: SAM3 in 3D jobs no longer hard-requires 6 GB of free VRAM. Only matters when RT-DETR is not installed
(with RT-DETR the tool always filters noise with RT-DETR, as in 0.9.8).

- **VRAM threshold follows the card:** the SAM3 camera cross-check ("Suggest cuboids for this frame") loads SAM3 when
  about 3.5 GB of VRAM is free (small cards: 55% of total VRAM), instead of the fixed 6 GB before (6–8 GB cards almost
  never had that much free).
- **New choice in the app's Settings → Backend and GPU → "SAM3 in 3D jobs":** Automatic (default) / Always load when I
  tick / Don't use SAM3 in 3D jobs. Applies when the backend restarts.
- **Running out of VRAM no longer breaks suggestions:** if loading or running SAM3 runs out of GPU memory, the tool
  releases SAM3, frees VRAM, shows a one-line reason and still returns the cuboids (just not cross-checked).
- **The panel says what filters noise:** the "Noise filter with images" line in the Suggest tab shows RT-DETR / SAM3 /
  SAM3 if enough VRAM / skipped, with the reason.

## 0.9.8 (2026-11-25)

Details and test results are in `docs/CHECKLIST_0.9.8.md` (Vietnamese). This release fixes what made 3D jobs label
"all over the place" in 0.9.7 and makes the checks (camera images, Claude) actually run.

- **Camera images are no longer lost:** 0.9.7 reported "frame … camera image error: name 'lidar_assist' is not
  defined" for every frame (missing import in the 3D session), so the panel said "no camera images" and the cross-check
  stayed at 0/40. Fixed.
- **Turning on Claude no longer fails with a 500 `KeyError: 'center'`:** tracks sent to Claude and to the camera
  cross-check now carry the centre, heading, point count and images of their clearest keyframe. When Claude fails (bad
  key, out of quota, 429, overloaded) the panel shows a one-line reason, those tracks go to "To review", and the session
  keeps going.
- **Honest buckets:** a track nobody checked (no camera, no Claude) goes to "To review" instead of "High confidence"
  (0.9.7: 109/109 tracks "High confidence" with no check at all). Small trailers and construction vehicles are only
  "High confidence" when the camera confirms them (trailers: or a confirmed towing vehicle right in front). Tracks seen
  in only 1–2 frames of a long job, or always with very few points, go to "Suspected noise".
- **Noise filtering with camera images (RT-DETR):** the RT-DETR r18vd 2D detector (COCO, Apache-2.0, ~83 MB) is
  downloaded from the Components page. A 3D box whose projection shows no vehicle / person goes to "Suspected noise"
  (dumpsters, portable toilets, poles, walls). Used both by "Suggest cuboids for this frame" and the whole job. The
  output reader is fixed too: sigmoid scores (softmax kept almost every box under the threshold) and normalised
  coordinates (boxes used to collapse into the top-left corner).
- **SAM3 cross-check in 3D jobs:** when SAM3 is not loaded it is loaded if enough VRAM is free (6 GB by default,
  `SAM3_3D_SAM3_MIN_FREE_MB`); otherwise the panel says why instead of silently skipping.
- **A real 3D model (CenterPoint, nuScenes):** choose an `.onnx` file on the Components page and cuboids are built by a
  model that recognises vehicles / people in the point cloud instead of clustering + guessing by size. The backend
  does the pillar split, decoding, duplicate removal (circle NMS) and moves the ground to nuScenes height; a frame that
  fails falls back to geometry. Export the file with `scripts/export_centerpoint_onnx.py` (runs on Colab). Weights
  trained on nuScenes: research use only.
- **Claude per frame in 3D jobs:** "Use Claude AI for this frame" on the Suggest tab: Claude looks at the camera
  images with the projected boxes, unticks objects that are not vehicles / people and fixes classes. The "Claude AI:
  on/off" chip now follows your checkbox.
- **The panel says what it uses:** one line "Build cuboids: 3D model / geometry · Noise filter with images: RT-DETR /
  none"; repeated errors (the same error on 40 frames) are grouped into one counted line; the camera cross-check now
  covers every track (it used to stop after the first 50).
- **Desktop app:** the Components page lists 2D / 3D ONNX, setup no longer gets stuck when everything is installed,
  and the false "needs update" banner for ONNX is gone.
- **For developers:** `AGENTS.md` + `docs/agents/` (GitHub issue tracker, domain docs).

## 0.9.7 (2026-11-25)

Details and test results are in `docs/CHECKLIST_0.9.7.md` (Vietnamese). This release removes the source of
`cudaErrorIllegalAddress`, brings cuboid 3D up to the 3D v2 spec, and rebuilds the 3D job panel as a three-step flow.

- **3D jobs no longer crash CUDA:** when a 3D session starts the backend releases the 2D models holding VRAM (SAM3,
  pose, face, SAM3 Video, re-ID, depth) — no more competing CUDA contexts, the main source of
  `cudaErrorIllegalAddress`. The camera cross-check only runs when SAM3 is already loaded, never loading it mid-request.
- **ONNX GPU groundwork:** execution providers picked per machine (optional TensorRT → CUDA with a VRAM limit from free
  memory → CPU fallback), CUDA/cuDNN DLLs preloaded from torch/lib on Windows, and a warning when both the CPU and GPU
  onnxruntime builds are installed. 2D/3D ONNX models (RT-DETR / D-FINE, PointPillars / CenterPoint) are declared with
  license metadata in a separate config table so the choice can be settled with a mentor; without them the pure-LiDAR
  path runs exactly as before.
- **Stronger 3D geometry:** L-shape fitting that hugs the object edges (no inflating/shrinking like minAreaRect),
  cKDTree cluster merging, an optional numba local-ground builder (with a numpy fallback), and track rules read from the
  guideline (max hidden keyframes, ±% size tolerance, classes whose yaw is locked to the width axis).
- **QC-grade 3D tracking:** constant-velocity Kalman plus Hungarian matching instead of greedy pairing; size locked to
  the densest frame (±5%); occluded ≤ N keyframes and outside handled independently, with a hard rule that both are
  never set on the same keyframe; the track end is outside, never occluded.
- **Three-tier camera calibration:** CVAT data → standard sensor preset (nuScenes / KITTI / Waymo) → fitted from 2-3
  hand-drawn sample boxes; anything missing is filled from the tier below.
- **Camera cross-check with 2D ONNX:** frustum splitting separates merged vehicles using the 2D box; an empty projected
  area is unticked in advance (suspected noise) and a different class raises a flag — SAM3 is not touched.
- **Full-track Claude Vision 3D:** Phase 1 recognises, removes noise and assigns labels per track (6-10 tracks per
  batch, BEV plus numbered camera images); Phase 2 audits the whole trackset and returns three buckets: high confidence,
  needs review, suspected noise. Network errors are retried.
- **Batched 3D sessions:** `/lidar/v2/session/*` endpoints (init → frames → geometry → vision → llm_batch → result) with
  per-batch checkpoints, safe cancel that KEEPS finished work, and retry of only the failed batch.
- **Rebuilt 3D panel in three steps:** Step 1 quick setup (one status line, Claude toggle, collapsed Advanced section),
  Step 2 a single primary button with per-phase progress (no scary countdown), Step 3 filter pills, plain-language notes
  instead of technical flag names, a "go to clearest frame" button and a pinned write bar. The 2D job block is hidden
  entirely in 3D jobs. The collapsed panel still shows progress.
- **i18n:** 99 new strings have English translations (2450/2450).

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
