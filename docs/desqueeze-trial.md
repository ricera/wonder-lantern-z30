# Desqueeze trial v1 (LCD live view)

Experimental anamorphic desqueeze of the rear-LCD live-view picture, in photo and movie mode. It changes only where the live view is drawn on the LCD. Sensor readout, recording, captured files, playback and HDMI are unchanged.

## Basis: camera probe log (2026-11-01)

The probe build is described in `docs/desqueeze-probe.md`. Its decoded log is `build/research/desqueeze-probe-v1.json` (16 records, 0 dropped, phases 4 and 5 skipped). It showed:

- **When the fit runs.** The stock display fit `0x1018ADF0` runs only when live view is set up after a photo↔movie mode switch, once per output.
  - It did not run for menu exit, DISP, half-press or playback.
- **Who calls it.** Only the `0x1018AF6C` cluster calls it. Cluster B (`0x10196Dxx`) and site C never ran.
- **Which call draws live view.** The first LCD call (return `0x1018AFDB`) fits the current mode's image, 5568×3712 in photo and 5568×3128 in movie, into the LCD container. The container is `(0,0,720,480)` in photo and `(0,38,720,442)` in movie.
- **The other calls.** The second LCD call fits the still-image size. The `0x1018B025`/`0x1018B041` pair computes HDMI rectangles.

## Patch

- **Hook site.** The 10 bytes at `0x1018AE6A` (`bl 0x1020C038; ldrh r1,[r0]; strh.w r1,[sp,#4]`, expected `81f0e5f80188adf80410`) are replaced by an absolute jump to `dsq_scale`. It returns to `0x1018AE74`.
- **What `dsq_scale` does:**
  1. Calls the stock class→display-size lookup.
  2. If the fit's caller (saved LR at fit `sp+0x54`) is `0x1018AFDB` and a factor is selected, it returns a private copy of the pair with width × factor:
     - 1.33× = 4/3
     - 1.5× = 3/2
     - 1.8× = 9/5
     - 2.0× = 2/1
  3. Sets the fit's ratio class (`r8`) to an unmatched value. Without this, the fit's same-class edge snap would restore full height in photo mode.
- **Safety.** The hook only reads extension state through `get`. It does no allocation, file I/O or waiting.
- **Menu.** **Extensions → Desqueeze** offers OFF (stock), 1.33x, 1.5x, 1.8x and 2.0x. The choice is saved to `A:\WL-DSQ.CFG` and restored with the other extension settings: on first menu entry or photo trigger after boot.
- **Exclusions.** The trial can't be combined with `aspect-profiles` or the other experimental pages, because they share the page slot.

## Expected LCD rectangles (offline replay of the camera inputs)

| Factor | Photo (3:2) | Movie (16:9) |
|---|---|---|
| OFF | (0,0,720,480) | (0,38,720,442) |
| 1.33x | (0,60,720,420) 2.00:1 | (0,88,720,392) 2.37:1 |
| 1.5x | (0,80,720,400) 2.25:1 | (0,106,720,376) 2.67:1 |
| 1.8x | (0,106,720,374) 2.69:1 | (0,126,720,352) 3.19:1 |
| 2.0x | (0,120,720,360) 3.00:1 | (0,138,720,342) 3.53:1 |

## Build and checks

```sh
python tools/build_desqueeze_trial.py --source Z_30_0120.bin --output output/desqueeze-trial-v1/EG209020.BIN
python tools/verify_desqueeze_trial.py --source Z_30_0120.bin --output output/desqueeze-trial-v1/validation.json
python tools/verify_desqueeze_trial_qemu.py --qemu-root ../z30-qemu --image output/desqueeze-trial-v1/EG209020.BIN \
  --report output/desqueeze-trial-v1/EG209020.json --fixture <sd-fixture.json> --output-dir desqueeze-trial
python -m unittest tests.test_desqueeze_ui
```

**Candidate:** `output/desqueeze-trial-v1/EG209020.BIN`
- SHA-256 `e43338d382ceb633e5ec809ff12bab8ffd1c00c46d4fdd866de9904f22fc2701`
- 33,963,602 bytes, CRC residue 0, reproducible build.

**Evidence (2026-11-01):**
- **Unicorn, 73 checks.**
  - The stock fixture reproduces all 5 camera-logged rectangles: photo/movie LCD, the movie first pass, and photo/movie HDMI.
  - Trial OFF is identical to stock.
  - Every factor letterboxes within 2 px of ideal, with even coordinates, inside the container.
  - The still-image LCD caller, both HDMI callers and unrelated callers stay identical to stock for every factor.
  - An out-of-range selection gives stock geometry.
  - Settings save and restore work, a corrupt record is ignored, and the file is closed afterwards.
- **QEMU** (assisted display):
  - The page renders, and the selection mark and the value on the root row are shown.
  - The native 1.33x and OFF rows save and return to Extensions.
  - The jump is installed.
- **Full unit suite:** 116 tests pass.

## Camera procedure

1. Load the EG the usual way. It replaces your current extension build.
2. **Setup → Extensions → Desqueeze → 1.33x.** The root row shows `1.33x`.
3. Leave the menu. **Switch photo→movie and back** (the trial applies at the next live-view setup).
4. Check photo live view: the picture should be letterboxed, about 720×360 on the LCD. Check that the image content is not cropped: the full sensor width should be visible, only squeezed vertically.
5. Switch to movie: about 720×304, centred in the stock 16:9 area. Record a short clip, then check that the file is unchanged (normal 16:9).
6. Check what persists, and note anything that reverts to full screen:
   - DISP cycle, half-press AF, focus magnify, playback and back, standby → wake, power cycle (and whether it comes back before or after the first menu entry).
   - Touch AF: whether the focus point lands where you tap.
7. Try 2.0x and OFF. OFF must return to stock after a mode switch.

## Known limits / open questions

- The setting takes effect only at the next live-view setup, not immediately.
- After power-on it is stock until settings are restored (first Extensions menu entry or photo trigger), and then until the next mode switch.
- Overlays (AF frames, grid, histogram) are drawn in their stock positions. Whether they follow the letterboxed picture is unknown.
- HDMI is deliberately untouched. The HDMI live-view caller `0x1018B025` is the candidate for a later option.

---

# Desqueeze trial v2: factor kept in native settings storage

v1 worked on the camera. Its limitation was that after power-on you had to visit the menu and then switch modes. v2 removes that by storing the factor in two padding bytes of the camera's own persistent settings (`docs/desqueeze-persistence.md`).

## What changed from v1

- **Storage.** The factor lives at `0xC25254EE`, padding inside persistent settings group 0x01. Byte 0 is the index and byte 1 is `index ^ 0xFF`. Any other value is OFF, including the factory default `00 00`.
  - The menu writes these bytes directly with one halfword store. Choosing OFF restores `00 00`.
  - `WL-DSQ.CFG` and all SD access are removed.
- **Hook.** `dsq_scale` reads the NV bytes itself. It no longer touches extension state, so it works from the first live-view setup after boot, before the Extensions menu has ever been opened.
  - It no longer needs a private copy of the size pair. It scales the width straight into the fit's local copy at `[sp+4]` (the store it displaced), and the native class table is never written.
- **Build-time guard.** `check_storage()` recomputes from the image that:
  - the two bytes overlap none of group 0x01's 34 keys;
  - their factory default is zero;
  - they lie inside one of the 108 persisted blocks;
  - group 0x01 is unbanked and class 1.

  The build fails if any of these is false.

## Checks

**Candidate:** `output/desqueeze-trial-v2/EG209020.BIN`
- SHA-256 `1a9fd484744de7b5f7ed0bbad9663c9aee9a66ff3b3e50454edae1c31bc40c12`
- 33,962,226 bytes, CRC residue 0, reproducible build.

**Unicorn, 78 checks:**
- Everything v1 checked: camera-fixture fidelity, every factor's rectangle (identical to v1), and the other callers left untouched.
- NV encoding round-trips for every index without touching the neighbouring bytes of group 0x01. Out-of-range writes store OFF, and 5 kinds of garbage read as OFF.
- Corrupt NV gives stock geometry.
- The hook applies with the extension state cleared.
- The native class-size table is unchanged.

**QEMU:**
- The live settings image has `00 00` at the NV bytes. The neighbouring group data is real loaded settings.
- The native 1.33x row stores `01 FE` and changes nothing else in group 0x01.
- The OFF row restores `00 00`.

**Unit suite:** 117 tests pass.

QEMU loads the patch after boot, so it can't test boot order. That needs the camera.

## Camera procedure

1. Load v2. The factor starts as OFF because the bytes are zero.
2. Choose **Extensions → Desqueeze → 1.33x**, then switch photo → movie → photo. You should get the same letterbox as v1.
3. **Power off and on. Don't open the menu.** Photo live view should come up already letterboxed. This is the main test, covering both persistence and boot order.
4. Standby → wake, and a mode switch: it should stay applied.
5. Optional: run Setup → **Reset all settings**. It is expected to go back to OFF.
6. Optional: load stock firmware and check that your other settings survived. Then reload v2: 1.33x should still be set.

If step 3 comes up stock, open Extensions → Desqueeze:
- If the mark is still on 1.33x, the value was saved but the first layout ran before the settings were loaded (a boot-order problem).
- If the mark is on OFF, the value wasn't saved.

## Camera result (2026-11-01)

The user loaded v2, chose 1.33x and switched photo/video once. After power-off and power-on, live view came up desqueezed without the Extensions menu being opened.

This confirms on the camera that:
- the NV padding bytes survive power-off through the native settings journal;
- settings are loaded before the first live-view layout at boot;
- the hook applies at boot without extension state.

Not yet tested:
- standby → wake;
- Reset all settings → OFF;
- a stock-firmware round trip;
- touch AF, focus magnify and HDMI.

---

# Desqueeze trial v3: apply immediately

**Camera notes on v2 (2026-11-01):** touch AF behaves correctly with the letterboxed picture.

## Mechanism

- **Where layout is triggered.** The live-view display update `0x1018CA30` runs in the display task. It is called from `0x1018FF44`, which in turn is called from the display loop at `0x10225128`. The update lays out live view only when its change mask is non-zero. That mask (`[sp+0x20]`) is built by `0x10197F42`, which has a single caller (return `0x1018CA8F`).
- **Which bit to use.** Bit 3 (8) is what the firmware sets when its event carries a change of the live-view mode property `0x1824`. Bit 3 drives:
  - the `0x1018AF6C` layout;
  - `0x10197E0C`;
  - the source window `0x10197D78`;
  - the output apply `0x1018AB02`, when that function's own conditions hold.
- **The v3 change.**
  - The menu action compares the old and new factor. If they differ, it sets `pending` in extension state.
  - A wrapper on `0x10197F42` runs the original completely. Then, if state is initialized (magic `DSQ3`) and `pending` is set, it clears `pending`, counts the event, and ORs bit 3 into the mask.
  - At boot there is no extension state, so nothing is forced. The first native layout already reads the stored factor.

## Checks

**Candidate:** `output/desqueeze-trial-v3/EG209020.BIN`
- SHA-256 `e0b602605d51a466e916c36028c4ed5ffb028c120eebe3ca8099b261a4eca419`
- CRC residue 0, reproducible build.

**Unicorn, 85 checks:**
- All the v2 checks.
- State is initialized idempotently, and a pending relayout survives a restore.
- With nothing pending, the mask builder is identical to stock, both with and without a native mode change.
- A pending change adds exactly bit 3 once, with the return value and event output unchanged.
- Nothing is forced without initialized state.

**QEMU:** selecting 1.33x stores `01 FE` and sets `pending = 1`. In the menu-only session the display update did not run (pending was still 1 after 3 s), so consuming the flag needs the camera.

**Unit suite:** 117 tests pass.

## Camera procedure

1. Load v3. Your factor from v2 is still stored.
2. **Extensions → Desqueeze → 2.0x**, then leave the menu. Live view should change to 2.0x **without a mode switch**.
3. Repeat with OFF and 1.33x.
4. If it doesn't change on leaving the menu, try DISP or a half-press, then a mode switch. Note which one finally applied it.

## Camera result (2026-11-01)

The user loaded v3. A new factor takes effect as soon as you leave the menu (half-press back to live view), in both photo and movie modes, with no mode switch.

---

# Desqueeze trial v4: desqueezed photo previews

v3 was confirmed on the camera: a new factor applies on returning to live view in photo and movie mode. The playback probe (`docs/playback-probe.md`) showed that playback does not use the generic fit. Normal single-frame playback shows the preview JPEG embedded in the file, while zoom decodes the main image.

## Change

While a factor is stored, the camera writes the two embedded previews at the desqueezed aspect. Each keeps its stock width, and its height is divided by the factor (nearest multiple of 4):

| Preview (3:2 DX) | Stock | 1.33x | 1.5x | 1.8x | 2.0x |
|---|---|---|---|---|---|
| large | 1620×1080 | 1620×808 | 1620×720 | 1620×600 | 1620×540 |
| small | 640×424 | 640×316 | 640×284 | 640×236 | 640×212 |

All five preview indices are scaled the same way (16:9, 5:4, 4:3, 1:1).

The full frame is scaled into the smaller buffer without keeping its proportions, so the preview itself is desqueezed. That's what normal playback shows, and also what computers and apps show when they read the embedded preview.

**Unchanged:**
- the main JPEG/NEF image;
- thumbnails (160×120 class);
- zoomed playback;
- photos taken while the factor is OFF;
- existing files.

**Hooks:**
- **Capture preview getters.** `0x1020C02E` (small) and `0x1020C038` (large) are replaced by selectors. Only the capture callers `PREVIEW_CALLERS` from `aspect_ui.py` get desqueezed pairs; everything else, including live view, gets the stock pointer.
- **Direct buffer producers.** The five producers that read the `0x1157223C` span directly use the same register-preserving jumps as the camera-verified aspect-profiles v4. Each factor has its own read-only shadow copy of the span in which only the heights change. Strides stay stock (2 × width).
- **NEF playback.** Stock NEF playback configures preview buffers from the area enum. `aspect_raw_playback.install` (v43, camera-verified for XPAN) is installed for the stock shapes 5568×3712 / 5568×3128 and their RAW planes 5600×3728 / 5600×3144. Each NEF then uses its own embedded preview sizes, queried from the file; failed or oversized queries fall back to stock.

## Checks

**Candidate:** `output/desqueeze-trial-v4/EG209020.BIN`
- SHA-256 `7643dfca9a381ba298f2073cdef5ef77b7c33bdc246beb0d66a1b85aaf4c74c3`
- CRC residue 0, reproducible build.

**Unicorn, 636 checks.** Everything from v3, plus:
- **375 getter cases** (2 getters × 5 factors × capture and non-capture callers, including live view and the shared preview/metadata callers × 5 indices). Only capture callers with a factor get desqueezed pairs; all others match stock exactly. The firmware mapping is read-only.
- **60 complete native producer runs** (3 producers × 4 factors × 5 indices).
  - Preview widths and strides are stock, and the heights match the table.
  - The only other changes are the 32-row-aligned buffer heights, which shrink, and the sequential buffer addresses, which only move lower.
  - With the factor OFF, the output is byte-identical to stock.
- **The shared `verify_aspect_raw_playback` harness on the stock shapes:** 126 complete native descriptor cases, the metadata getters for each caller with and without failure, and the rejected-query cases.

**QEMU:** the menu stores the factor, sets the pending flag, and the live-view jump is installed.

**Unit suite:** 118 tests pass.

## Camera procedure

1. Load v4. Your stored factor stays in effect.
2. With **1.33x** set, take a **JPEG**. Normal playback should show it desqueezed (about 2:1, letterboxed). Zoom shows the squeezed full image, which is expected.
3. Take a **RAW (NEF)** and play it back the same way. It should be desqueezed and **not corrupted**. If it is corrupted, report that first.
4. Set **OFF** and take one more photo. It should play back as normal 3:2. Older desqueezed photos should still show desqueezed.
5. Optional: copy the JPEG to a computer. The embedded preview should be 1620×808, and the main image 5568×3712 (still squeezed, as expected).

## Camera result for v4 and the v5 fix (2026-11-01)

**v4 on the camera:** RAW (NEF) playback showed the desqueezed preview correctly. RAW+JPEG still played back squeezed.

**Sample pair** (`DSC_0623`, 1.33x): the dimension inventory alone was kept, not the files. Both files already contained the desqueezed previews:
- JPG: 640×316 and 1620×808, plus the 5568×3712 main image and the 160×120 thumbnail;
- NEF: 640×316, 1620×808 and 5568×3712.

So capture was correct, and the fault was in playback setup.

**Cause:**
- The per-file preview query `0x103A1FA8` serves still file types 0, 1 and 2 (type 3 is unsupported; 4/5 are movies). The aspect-profiles RAW fix only admits types 1/2 (NEF). JPEG is type 0.
- For type 0, `0x103472DE` / `0x10340C84` fill the large-preview descriptor from the area enum (1620×1080) whenever the main image is taller than 1080 (`0x438`), which covers every stock size.
- A 1620×808 preview decoded into a 1620×1080 descriptor is stretched back to 3:2, so it looks squeezed.

**v5 fix:**
- `aspect_raw_playback.install()` takes `file_types` (default `(1, 2)`). The aspect-profiles and full builds are byte-identical to before.
- Desqueeze passes `(0, 1, 2)` together with the stock JPEG L/M/S shapes for DX, 16:9 and 1:1, plus each L RAW plane. Every JPEG or NEF of those shapes then uses its own embedded preview sizes. For files with stock previews, that gives exactly the stock sizes.

**Candidate:** `output/desqueeze-trial-v5/EG209020.BIN`
- SHA-256 `7447381d31ee77d2e7eec187cca04d2dc97f77dead7469ef3bd342d740b7aa8e`
- CRC residue 0, reproducible build.

**Unicorn, 659 checks:**
- the v4 checks;
- **the type-0 (JPEG) native descriptor builder:**
  - for all 9 stock JPEG shapes it uses the file's previews;
  - a non-target shape and movie type 4 match stock with no query;
  - the stock path reproduces the enum mismatch;
- **`0x10340C84` size preparation** for JPEG, for both preview kinds.

**Unit suite:** 118 tests pass.

**Camera test:**
1. Play back the existing RAW+JPEG pair. It should now be desqueezed: the files already hold the right previews.
2. Play back a plain JPEG and a JPEG at M or S size.
3. Play back a stock photo taken while OFF. It should be unchanged.
