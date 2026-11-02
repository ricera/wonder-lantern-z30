# Playback probe v1 (diagnostic, observation only)

Goal: find where playback places the decoded photo or movie frame on the LCD, so a playback desqueeze can widen it the same way the live-view trial does.

## Why a new probe

The live-view display fit `0x1018ADF0` never ran during playback (desqueeze probe v1, phase 3). Playback still has to fit the picture into the screen. The firmware's generic "fit source WH into target" routine `0x10238C3A` has 50 static callers, including the live-view fit (return `0x1018AF11`), and several of them are in file/playback code ranges (`0x1030xxxx`, `0x1032xxxx`, `0x1039Cxxx`). This probe logs which callers run, and with what geometry, in each phase.

## What the image contains

- **Contents:** the Extensions menu framework and **Extensions → Playback probe** (Next phase / Save log / Clear log). No other presets are included; aspect-profiles, desqueeze and the desqueeze probe are excluded.
- **Hook:** a wrapper on `0x10238C3A` (12 displaced bytes, expected `2de9f04f99b0994640f20014`, continues at `0x10238C46`).
  - It re-pushes the 5th (stack) argument and runs the complete original.
  - It then records the caller, flags, source WH, target WH, rect and the resulting rect.
- **Records:** up to 128 distinct calls of 48 bytes each, deduplicated with first/last sequence and hit counts, and phase-tagged. Further distinct calls are counted as dropped.
- **Log file:** saved to `A:\WL-PBK.DBG` (6216 bytes) from the menu only.
- **When recording starts:** after the Extensions menu is first opened.

## Checks

**Candidate:** `output/playback-probe-v1/EG209020.BIN`
- SHA-256 `2ff2186f68d25674a51284513b0b7ed5afe7f9e35f508c967f1a816d4608b952`
- CRC residue 0, reproducible build.

**Unicorn, 20 checks:**
- The generic fit returns identical results and leaves the stack-argument area untouched for 7 cases (3:2, 16:9, portrait, 4K→1080, inset rect, several flag values).
- It is transparent before init and while the log is locked.
- Dedup and record fields are correct.
- The live-view fit is identical through the wrapper, and its caller is attributed.
- Phase, save through the modelled file service, clear and the capacity bound all work.

**QEMU:** the page renders, and the native Next phase and Clear log rows return to Extensions. The menu-only session logged 0 generic-fit records.

## Camera procedure

1. **Setup → Extensions → Playback probe → Clear log.**
2. **Phase 1:** Next phase. Use photo live view for ~5 s. This is the baseline that separates live-view callers.
3. **Phase 2:** Next phase. Press Playback and view one **photo** (single-frame view). Step to a second photo if it has a different aspect, such as a portrait or another crop.
4. **Phase 3:** Next phase. In playback, **zoom in** once or twice and pan a little, then zoom out.
5. **Phase 4:** Next phase. Play back a **movie**: start it, let it play ~3 s, then pause.
6. **Phase 5:** Next phase. Optionally use thumbnail view (zoom out to the index).
7. **Save log**, then copy `WL-PBK.DBG` and decode it:

```sh
python tools/read_playback_probe.py WL-PBK.DBG --output build/research/playback-probe-v1.json
```

If `dropped` is large, repeat with fewer actions per phase.

## What the answer decides

- **A caller runs only in phases 2–4, with the photo or movie size as source:** that caller is the playback placement. A width factor at that call, like the live-view trial, would letterbox playback.
- **No playback records:** playback places pictures without `0x10238C3A`, and the next probe has to target the playback layout code directly.

## Camera result (2026-11-01)

The log is decoded at `build/research/playback-probe-v1.json`: 583 calls, 11 distinct records, 0 dropped, final phase 5.

- **Playback.** The generic fit `0x10238C3A` does **not** run in photo playback, playback zoom, movie playback or thumbnail view (phases 3–5 have no records). The phase-2 records are live-view calls made before playback started.
- **Live view.** Every record is live-view overlay or coordinate mapping:
  - `0x1039C73F` and `0x100D74DF`/`0x100D7541` map AF/subject boxes from sensor coordinates (5568×3712) to the full 720×480 screen.
  - `0x104F1719` (flags 0x400) maps a touch point on 720×480 back to sensor coordinates.
  - `0x1018A9A7` (flags 0x840) appears once.
  - All of these assume the picture fills the 720×480 screen, not the desqueezed letterbox. So overlays and touch could be off vertically away from the centre.

Conclusion: playback places pictures without `0x10238C3A`. Earlier aspect-profiles work (`docs/aspect-playback-investigation.md`) found that normal single-frame playback shows the **embedded preview JPEG**, while zoom decodes the main image. A preview generated at capture time with the desqueezed aspect would therefore display correctly in normal playback. This works the same way as the aspect-profiles v4 preview producers (`0x10142D86`, `0x1014376E`, `0x101464C4`).
