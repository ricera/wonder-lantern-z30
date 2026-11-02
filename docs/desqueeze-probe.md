# Desqueeze probe v1 (diagnostic, observation only)

Goal: find out which callers of the stock live-view display fit `0x1018ADF0` run in photo live view, movie live view, playback and HDMI, and with what geometry, before writing an anamorphic desqueeze patch.

## Background

Offline replay of the stock fit showed two things:
- Feeding it anamorphic image sizes does not work. The ratio classifier `0x101D60CA` (`k = W*36/H`) snaps every ratio wider than about 1.64:1 to the 16:9 rectangle `(0,36,720,442)`.
- Overriding the class-to-display-size lookup `0x1020C038` at the fit's own call (`0x1018AE6A`, returns to `0x1018AE6F`) yields exact letterbox rectangles on 720×480. Examples: 1.33× on 3:2 gives `(0,58,720,420)`, 2× gives `(0,120,720,360)`.

`0x1018ADF0` has 9 static callers:
- 4 in the `0x1018AF6C` cluster, gated by an output mask (`&5` / `&6`).
- 4 in `0x10196Dxx`.
- 1 at `0x10198486`.

A hook inside the fit would affect all of them. This probe records which ones actually run in each camera state.

## What the image contains

- The Extensions menu framework (shutter sound, default OFF) and an **Extensions → Desqueeze probe** page.
- No other presets. `aspect-profiles` is deliberately excluded so the log shows stock geometry.

Three hooks. Each runs the complete original code and returns the original results:

| Site | Records |
|---|---|
| `0x1018ADF0` fit | caller, flags, area, `arg+0x10`, viewport, view rect, ratio, resulting display rect, control rect |
| `0x101D60CA` classifier | image WH and ratio class, only for the call from inside the fit (paired by the fit's argument pointer) |
| `0x1018AF6C` cluster | output mask `r1` (reported only for fit calls made by this cluster) |

- Distinct calls are deduplicated into 64 records of 80 bytes each, with first/last sequence numbers and a hit count. Each record is tagged with the current phase (0–9).
- Display callbacks never call file services.
- **Save log** writes `A:\WL-DSQ.DBG` (5192 bytes) from the menu task while holding the record lock. Display events that arrive during the write are dropped and counted.
- Recording starts once the Extensions menu has been opened after boot. This allocates the state.

## Build and checks

```sh
python tools/build_desqueeze_probe.py --source Z_30_0120.bin --output output/desqueeze-probe-v1/EG209020.BIN
python tools/verify_desqueeze_probe.py --source Z_30_0120.bin --output output/desqueeze-probe-v1/validation.json
python tools/verify_desqueeze_probe_qemu.py --qemu-root ../z30-qemu --image output/desqueeze-probe-v1/EG209020.BIN \
  --report output/desqueeze-probe-v1/EG209020.json --fixture <sd-fixture.json> --output-dir desqueeze-probe
python -m unittest tests.test_desqueeze_probe
```

Candidate: `output/desqueeze-probe-v1/EG209020.BIN`
- SHA-256 `f954d2a180e08278048222f0a8d03fe0b7e1232eb7c74c51b0f41ecc0e2fa315`
- 33,965,710 bytes, CRC residue 0, reproducible build.

Evidence (2026-11-01):
- **Unicorn, 45 checks.** The fit gives a rectangle identical to stock in 8 fixture cases, including flagged, portrait and 1920×1080. The classifier is identical in 9 cases. The cluster leaves the view object identical for masks 0/1/2/4, and mask attribution is correct. Also covered: dedup and hit counts, phase tagging and saturation, save through the modelled file service, busy/lock drop, clear, capacity bound, and no recording before initialization.
- **QEMU** (assisted `nikon-z30-display`):
  - Extensions and the probe page render with native fonts.
  - The native **Next phase** and **Clear log** rows return to Extensions, and the root row shows the status.
  - No fit records were produced in the menu-only session.
- **Full unit suite:** 113 tests pass.

None of this proves behavior on a camera, LCD scaling, HDMI or live view.

## Camera procedure

Load the EG with the same procedure as other Wonder Lantern builds. It replaces your current extension build until you load that build again.

1. Power on and open **Setup → Extensions → Desqueeze → Clear log**. The root row shows `Log cleared` (phase 0).
2. Choose **Next phase** (phase 1). Leave the menu and use **photo live view** for ~10 s, including one DISP cycle and a half-press.
3. Menu → **Next phase** (phase 2). Switch to **movie** mode live view for ~10 s. Optionally start and stop a short recording.
4. Menu → **Next phase** (phase 3). Back in photo mode, press **Playback**, view one photo, zoom in once, and exit.
5. Menu → **Next phase** (phase 4). Optional: connect **HDMI** and repeat photo then movie live view. Write down which you did.
6. Optional phase 5: photo live view **magnify** (focus zoom) and the standby → wake cycle.
7. Menu → **Save log**. The root row must show `Log saved`. `Save failed` or `Camera busy` means retry once the camera is idle.
8. Copy `WL-DSQ.DBG` from the card root and decode it:

```sh
python tools/read_desqueeze_probe.py WL-DSQ.DBG --output build/research/desqueeze-probe-v1.json
```

Each phase includes the menu exit and entry transitions that frame it. Compare records by phase and caller rather than by order. If `dropped` is nonzero, repeat with fewer actions per phase, or save after each phase.

## What the answer decides

- **Video is free:** movie live view uses the same caller(s) as photo live view and is classified as 16:9. A single fit-local override then covers both modes.
- **Filter needed:** HDMI or playback use distinct callers or masks. The desqueeze hook must then filter by caller or mask.
- **Movie path not traced:** no records in phase 2 means movie live view does not use this fit, and its display path must be traced separately.

## Result (camera, 2026-11-01)

The log was decoded to `build/research/desqueeze-probe-v1.json`. Phases 4 and 5 were skipped.
- **Video is free.** Only the `0x1018AF6C` cluster ran, and only on photo↔movie live-view setup.
- **Which call to patch.** The LCD live-view rectangle comes from the call returning to `0x1018AFDB`, in both modes.
- **Playback** does not use this fit.

Next step: `docs/desqueeze-trial.md`.
