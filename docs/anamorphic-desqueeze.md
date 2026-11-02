# Anamorphic desqueeze

`anamorphic-desqueeze` is an **experimental, opt-in** preset for Nikon Z30 C 1.20. It is not part of `full`, and it cannot yet be combined with `aspect-profiles` (see [compatibility](desqueeze-aspect-compatibility.md)). Selecting it also includes the Extensions menu framework (`shutter-sound`, default OFF).

```sh
python patch.py anamorphic-desqueeze --source path/to/Z_30_0120.bin --output output/EG209020-desqueeze.BIN
# Combines with every other preset except aspect-profiles / full, for example:
python patch.py anamorphic-desqueeze photo-zebra bitrate-slowmo-uncap liveview-dual \
    --source path/to/Z_30_0120.bin --output output/EG209020-custom.BIN
```

## Use

1. Open **Setup → Extensions → Desqueeze** and choose **1.33x**, **1.5x**, **1.8x** or **2.0x**. **OFF (stock)** turns it off.
2. Return to live view, for example with a half-press. The LCD picture is letterboxed to the desqueezed aspect in photo and movie mode.
3. Optional: choose **Desqueeze JPEG** to toggle saving the main JPEG desqueezed (marked `*` when on).

Settings:
- The factor and the JPEG switch are stored in the camera's own persistent settings, in two padding bytes of settings group 0x01, not on the SD card.
- They survive power-off and apply from the first live-view frame after power-on.
- Reset all settings, or a firmware update that resets settings, returns them to OFF.

## What changes

| Area | With a factor set | Unchanged |
|---|---|---|
| **LCD live view**, photo and movie | Letterboxed at width × factor. For example, 1.33x on 3:2 is 720×360 and on 16:9 is 720×304. | HDMI output, recordings, sensor readout |
| **Embedded previews** in new photos (JPEG and NEF) | Stock width, height ÷ factor. For example, 1620×808 at 1.33x. | Thumbnails (160×120), existing files |
| **Playback**, normal single-frame view | Shows desqueezed photos (JPEG, NEF, RAW+JPEG), using each file's own preview size | Photos taken while OFF |
| **Main JPEG**, only with **Desqueeze JPEG** on | Full-frame desqueeze with the stock width and height ÷ factor (multiple of 16): DX L 5568×2784 at 1.33x, and M/S/16:9/1:1 likewise. EXIF pixel dimensions match. | NEF raw data (5600×3728) |

With Desqueeze JPEG on, the full-size JPEG embedded in a NEF is also desqueezed. The raw image itself is not.

## Known limitation: 16:9 previews stay stock

On the composed test build (`f49b2682…`), recording a movie with 1.33x set showed the movie's playback preview (the poster frame) with its bottom quarter corrupted. The video itself played clean and squeezed.

**Cause:** the poster preview uses preview index 1 (16:9), which had been desqueezed to 1920×808. Movie playback (`0x103472DE`, file types 4/5) always decodes posters into fixed 1920×1080 / 640×360 buffers. The 808 decoded rows leave the rest of the buffer uninitialized. Nothing overflows, and the embedded JPEG is valid.

**Fix:** preview index 1 now stays stock. Movie posters are correct again, while DX (index 0) and 1:1 (index 4) photo previews are still desqueezed.

- **Side effect:** photos shot in the **16:9 image area** keep a stock preview and play back squeezed. With Desqueeze JPEG on, their main JPEG is still desqueezed.
- **Existing clips:** movies recorded with the earlier build keep their 1920×808 poster.
- **Possible refinement:** a probe that separates the movie-poster path from 16:9 photo capture could restore 16:9 photo previews.

## Camera verification (Z30 C 1.20, 2026-11-01)

- **Live view:** desqueezed in photo and movie; persists across a power cycle; applies immediately on returning from the menu; touch AF behaves correctly.
- **Playback:** desqueezed previews for JPEG (L/M/S), NEF and RAW+JPEG; OFF photos are unchanged.
- **Desqueeze JPEG:** confirmed. For a RAW+JPEG sample the JPEG is 5568×2784 with EXIF 5568×2784, and the NEF raw is still 5600×3728. The main image downscaled matches the independently generated preview (MAE 1.31/255), so it is a desqueeze, not a crop.
- **Not yet covered:**
  - standby/wake;
  - Reset all settings;
  - HDMI;
  - AF-frame and touch alignment near the edges of the letterbox. These overlays still map onto the full 720×480 screen.

The exact composed image you build has not itself been camera-tested; the evidence above is for the standalone trial build.

## Validation in this repository

```sh
python tools/build_desqueeze_trial.py --source Z_30_0120.bin --output output/desqueeze/EG209020.BIN   # = the preset
python tools/verify_desqueeze_trial.py --source Z_30_0120.bin --output output/desqueeze/validation.json
python tools/verify_desqueeze_trial_qemu.py --qemu-root ../z30-qemu --image output/desqueeze/EG209020.BIN \
  --report output/desqueeze/EG209020.json --fixture <sd-fixture.json> --output-dir desqueeze-preset
python -m unittest tests.test_desqueeze_ui tests.test_presets
```

**Unicorn, 2346 checks.** Everything runs in the patched and stock firmware side by side:
- live-view geometry, replaying camera-logged inputs;
- the NV encoding and relayout;
- the preview getters and the complete native preview producers;
- per-file playback for JPEG and NEF;
- the output-size getter and the complete resize and encoder stages.

**Combinations:**
- Desqueeze with each other preset, and with all of them except aspect-profiles, builds without overlapping edits.
- No other preset edits code within 2 KiB of a desqueeze hook.
- All existing presets, including `full`, remain byte-identical to before.

**QEMU:** the menu renders alone and in a five-category Extensions menu (desqueeze, photo zebra, video bitrate, shutter sound, SD modules), and all six rows work.

Design and research notes:
- [trial history](desqueeze-trial.md)
- [live-view probe](desqueeze-probe.md)
- [settings storage](desqueeze-persistence.md)
- [playback probe](playback-probe.md)
- [desqueezed JPEG](desqueeze-jpeg.md)
