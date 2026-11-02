# Desqueezed main JPEG: research

Goal: an opt-in "Desqueeze JPEG" that saves the main JPEG at the desqueezed aspect by keeping the width and shortening the height, for example L 5568×2784 at 1.33x. The NEF stays untouched, so RAW+JPEG gives an original NEF plus a ready-to-view JPEG.

## What is known (static)

**Getters** (index = area `+0x208`, then size `+0x229` L/M/S):

| Getter | DX (area 0) values | Notes |
|---|---|---|
| internal `0x1020BFC8` | 5600×3728 (full) / 4200×2796 (index 2, ISP-reduced) | |
| reduced `0x1020C05C` | same shape | |
| output `0x1020BFDA` | L 5568×3712, M 4176×2784, S 2784×1856 | 16:9 is area 2; 1:1 is area 4 |

`0x10141B48` centres the internal crop in the sensor source: offset = (source − internal) / 2.

Six capture-stage builders, all of the form (descriptor r0, capture info r1), consume these sizes:
- `0x10141B48`
- `0x10141DC0`
- `0x1014237A`, which fills the main output descriptor (+0x48..+0x5C: buffer aligned to 128, output W/H, stride)
- `0x10142BEA`
- `0x1014319E`, which reads output, reduced and internal sizes and builds the encoder/stage descriptors
- `0x10146F6A`

The preview producers `0x10142D86`, `0x1014376E` and `0x101464C4` are separate; v4/v5 already handle them.

**Emulation with synthetic info (stock):**
- `0x1014319E` holds the 5600×3728 internal source at +0x56 and the L/M/S output size at +0x42. It is the likely full-frame → M/S resize stage.
- `0x10142BEA` holds the output buffer (M: 4224×2816 aligned, output 4176×2784).

These are hints only, since the capture info was synthetic.

**Open question:** for each size, which stage scales and which crops, and whether the scaling stage takes independent X/Y ratios. An experiment that shortens the output height while some stage still writes the full height into a now-shorter buffer could overrun memory. So the geometry is observed first.

## Capture probe v1 (observation only)

- **Build:** `tools/build_capture_probe.py` → `output/capture-probe-v1/EG209020.BIN`
  - SHA-256 `6ac4625b950912e6babda229c8e3b15499ba839a0379b6b056e4c9c91137dd33`
  - CRC residue 0, reproducible build.
- **Hooks:** wrappers on the six builders. Each wrapper runs the complete original and then records:
  - the builder, the caller and the phase;
  - 12 capture-info bytes: `+0`, `+0x98`, `+0x208`, `+0x226..+0x22D`, `+0x239`;
  - a 208-byte dump of the finished descriptor.
- **Log:** records are deduplicated by builder, caller, phase and info, up to 40 records. The log is saved to `A:\WL-CAP.DBG` from **Extensions → Capture probe**.

**Checks:**
- **Unicorn, 43 checks:** all six complete builders run for L/M/S, the descriptors are byte-identical to stock, and each call is recorded with the right builder, info and dump. Dedup, phase, save, clear and the capacity bound all work.
- **QEMU:** the page renders and its rows work. The menu-only session logged no builder calls.
- **Unit suite:** 120 tests pass.

## Camera procedure

Use JPEG Fine, DX image area, in photo mode.

1. **Capture probe → Clear log.**
2. **Next phase** (1): image size **L**, take one JPEG.
3. **Next phase** (2): image size **M**, take one JPEG.
4. **Next phase** (3): image size **S**, take one JPEG.
5. **Next phase** (4): **RAW+JPEG (L)**, take one.
6. **Next phase** (5), optional: image area **16:9**, size L, take one JPEG.
7. **Save log**, then decode it:

```sh
python tools/read_capture_probe.py WL-CAP.DBG --output build/research/capture-probe-v1.json
```

## Capture probe result (camera, 2026-11-01)

The decoded log is `build/research/capture-probe-v1.json`: 21 records, 5 phases (L, M, S, RAW+JPEG, 16:9), 0 dropped.

For each JPEG shot, these builders ran:
- **`0x10141B48`:** the sensor crop, 5600×3728 (5600×3144 for 16:9). It is identical for L, M and S.
- **`0x1014237A`, twice** (returns `0x10142A9D` and `0x10142AEF`): the **resize stage**.
  - Its input block (+0x1A..+0x24: 5568×3712, then 16/8) is identical for L, M and S.
  - Only the output block differs: aligned buffer +0x4E..+0x54 (5568×3712 / 4224×2816 / 2816×1856) and output +0x56/+0x58 (5568×3712 / 4176×2784 / 2784×1856), plus the buffer addresses.
  - There is no mode flag between 1:1 (L) and scaled (M/S).
- **`0x1014319E`:** the JPEG encoder setup. Output +0x42/+0x44, a compressed-buffer size per size index (+0x3C), and the previews (640×424, 1620×1080, 160×120).
- **Not seen:** `0x10142BEA`, `0x10141DC0` and `0x10146F6A` did not run.
- **RAW+JPEG:** adds a second `0x10141B48` call (return `0x10141FC5`) and extra M/S-sized blocks.

Conclusion: one generic resize stage maps the fixed 5568×3712 source to whatever output size the output getter returns, and every buffer is derived from that size. The v4/v5 previews showed that the camera's resizer accepts independent X/Y ratios.

## Trial v6: opt-in desqueezed JPEG

- **Menu.** **Extensions → Desqueeze** gets a sixth row, **Desqueeze JPEG** (marked `*` when on).
  - The flag is bit 4 of NV byte 0; bits 0–2 remain the factor index and byte 1 is still the check byte.
  - The stored v5 value `01 FE` reads as 1.33x with JPEG off.
- **Getter.** The output getter `0x1020BFDA` is replaced by a selector. Only the capture-stage callers get desqueezed sizes:
  - `0x10142967`, the resize stage `0x1014237A`;
  - `0x10142C23`, the output buffer `0x10142BEA`;
  - `0x101432DF`, `0x1014339D` and `0x101435FB`, the encoder setup `0x1014319E`.

  The condition is that JPEG is on, a factor is stored, the area is 0/2/4 and the size is 0–2. Width stays stock; height is divided by the factor, to the nearest multiple of 16. Live view, playback and file metadata callers keep stock sizes.
- **Playback.** The desqueezed main sizes are added to the per-file preview playback shapes (46 shapes in total).

| 1.33x | L | M | S |
|---|---|---|---|
| DX | 5568×2784 | 4176×2080 | 2784×1392 |
| 16:9 | 5568×2352 | 4176×1760 | 2784×1168 |
| 1:1 | 3712×2784 | 2784×2080 | 1856×1392 |

**Candidate:** `output/desqueeze-trial-v6/EG209020.BIN`
- SHA-256 `e867279019821f989a63f935c1b962054442120a8642d24b74795985a8859b98`
- CRC residue 0, reproducible build.

**Unicorn, 2346 checks.** Everything from v5, plus:
- **NV flag encoding:**
  - a v5 value stays valid;
  - toggling keeps the factor, and writing the factor keeps the flag;
  - invalid bytes read OFF.
- **Output getter, 1440 cases:**
  - every capture caller and 4 non-capture callers;
  - areas 0/1/2/4, sizes 0–3;
  - every factor with the flag on and off.
- **Complete `0x1014237A` and `0x1014319E`, DX and 16:9, L/M/S:**
  - **flag off, or OFF:** byte-identical to stock for the resize stage, and the main size stays stock in the encoder;
  - **flag on:** desqueezed size with stock width. The resize-stage buffer is the stock width and covers the shorter output, and the encoder's compressed buffer is never larger than stock.
- **JPEG playback (type 0)** uses the file's previews for all desqueezed main sizes.
- **Regression:** the aspect-profiles and full builds are still byte-identical.

**QEMU:**
- The sixth row renders, and its mark follows the flag.
- 1.33x stores `01 FE`; the JPEG row stores `11 EE`, then `01 FE` again; OFF stores `00 00`.

**Unit suite:** 122 tests pass.

## Risks and camera procedure

**What is unverified:**
- whether the hardware resizer and encoder accept the new output size for the main image (the previews suggest yes);
- the EXIF and maker-note pixel dimensions, which may still say 5568×3712;
- whether the NEF's embedded full-size JPEG in RAW+JPEG is the same encode, and therefore also desqueezed.

**Worst plausible outcome:** a failed or corrupt JPEG, or a camera error during capture. Remove the battery if the camera hangs.

1. Load v6. Leave **Desqueeze JPEG** off and take a JPEG: it should be stock 5568×3712 with desqueezed previews, as in v5.
2. Turn **Desqueeze JPEG** on (1.33x) and take one **JPEG L**. Check:
   - normal playback;
   - zoomed playback, which should now also look desqueezed;
   - the file on a computer: 5568×2784, with no stretching or cropping. Compare the framing with the v5 preview.
3. Repeat with **M** and **S**.
4. Take **RAW+JPEG**: the JPEG should be desqueezed, and the NEF should open normally in a RAW converter.
5. Copy one desqueezed JPEG and the RAW+JPEG pair to the repo root. I'll check the stream sizes and EXIF dimensions.

## Camera result (2026-11-01)

The user loaded v6 and tested it on the camera. The desqueezed JPEG "looks good".

No sample file has been inspected yet. Still to check:
- the EXIF and maker-note pixel dimensions;
- the NEF's embedded full-size JPEG in RAW+JPEG.

## Sample inspection (DSC_0640, RAW+JPEG L, 1.33x, Desqueeze JPEG on)

Only dimensions and statistics were kept; the files themselves are not stored.

- **JPG:**
  - main 5568×2784, previews 640×316 and 1620×808, thumbnail 160×120;
  - EXIF PixelXDimension/PixelYDimension = **5568×2784**. The camera writes the real output size; the v5 stock reference says 5568×3712.
- **NEF:**
  - raw SubIFD1 **5600×3728** (uncompressed raw), untouched;
  - embedded full-size JPEG 5568×2784 (SubIFD0), plus 1620×808 and 640×316. The embedded JPEGs are described only by offset and length (no size tags), so there is no metadata conflict.
- **Content check:** the main image downscaled to 1620×808 matches the independently resized embedded preview with MAE **1.31/255**. Compared as if it were a vertical centre crop, the MAE is 28.79/255. So the main JPEG is a full-frame desqueeze, not a crop.
