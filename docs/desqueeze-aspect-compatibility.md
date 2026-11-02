# anamorphic-desqueeze × aspect-profiles: compatibility analysis

Status: **not combinable yet.** `build()` and `shutter_sound.add()` refuse the combination, and `full` (which includes aspect-profiles) is therefore also refused. This note records why, and what a composed implementation would need.

## Why the combination is wanted

The two features together describe a real workflow: crop to a sensor aspect and then desqueeze. For example, 4:3 with a 2x anamorphic gives 2.67:1, and 5:4 with 1.33x gives about 1.66:1. The expected result is the aspect-profiles crop first, then the desqueeze applied to that cropped frame.

## Hook overlap (static, from the two preset builds)

Excluding the shared menu framework, aspect-profiles adds 21 edits and desqueeze adds 13. **11 of the 13 desqueeze edits are at exactly the same addresses as aspect-profiles edits.** The patcher rejects overlapping edits, so the two cannot simply be stacked.

| Site | aspect-profiles | anamorphic-desqueeze | Composition needed |
|---|---|---|---|
| `0x1020BFDA` output-size getter | `aspect_output`: per-profile area-2 geometry for capture and the live-view output callers (`0x1018AB41`, `0x10197D9D`, `0x101985E5`, `0x1018AE57`), and the stock pointer for 25 file/metadata callers | `dsq_output`: height ÷ factor for 5 still-capture stage callers when Desqueeze JPEG is on | One selector: aspect's result first, then the desqueeze transform for capture-stage callers |
| `0x1020C02E` / `0x1020C038` preview getters | per-profile preview pairs (index 1) for `PREVIEW_CALLERS` | height ÷ factor for `PREVIEW_CALLERS`, all indices | Combined pair = desqueeze(aspect pair). Note that live view's `0x1018AE6A` reaches `0x1020C038` through `dsq_scale` and must keep the stock pointer |
| 5 direct preview producers (`0x10142DE4` …) | per-profile shadow of `0x1157223C` | per-factor shadow | One shadow per (profile, factor) pair: 6 × 4 + singles |
| `0x10340B02`, `0x10340B82`, `0x103488B2` per-file preview playback | file types (1, 2); custom crop shapes | file types (0, 1, 2); stock and desqueezed shapes | Install once with the union of shapes and types (0, 1, 2). The query returns each file's own sizes, so this is semantically compatible |
| `0x1018AE6A` live-view display width | none | desqueeze only | Works on top of aspect's image size (see below) |
| `0x10197F42` live-view relayout | none | desqueeze only | Independent; check aspect's mask lifecycle on a forced relayout |

The remaining aspect-profiles edits are internal/reduced/offset/crop/edge selectors, trim tables, the grid frame and the soft mask. They don't overlap desqueeze.

## Interactions beyond overlapping bytes

1. **Live-view image size.** In area 2, aspect's `aspect_output` answers the fit's image-size query (`0x1018AE57`) with the profile geometry. That changes the ratio class the fit chooses before `dsq_scale` multiplies the display width.
   - The composed rectangle should be profile aspect × factor, letterboxed in the container.
   - The class-based snap that desqueeze disables (`r8 := 0xFF`) also affects aspect's profile classes. That needs replay with aspect's tables.
2. **Soft mask and grid frame.** These are positioned for the stock live-view rectangle and the profile crop. With desqueeze the picture rectangle is shorter, so the mask and frame would be misplaced vertically unless they are computed from the desqueezed rectangle.
   - AF-frame and touch mapping already assume the full 720×480 screen (playback probe). That limitation is shared with desqueeze alone.
3. **JPEG sizes.**
   - aspect-profiles crops (for example XPAN 5568×2056); desqueeze then divides the height (5568×1544 at 1.33x).
   - Both shrink buffers, which is safe in the direction checked for desqueeze alone. The encoder's compressed-buffer size is per size index and never larger than stock.
   - aspect's crop variants and trim tables must be checked against a shorter output height.
4. **Previews.** aspect fits the crop into the stock 16:9 allocation (index 1, for example 1920×708); desqueeze divides that height (1920×532 at 1.33x). The per-file playback fix handles any size within the stock limits.
5. **Settings and menu.**
   - Two independent categories: aspect stores `WL-ASPECT.CFG` on SD, and desqueeze uses the NV padding bytes.
   - aspect's setting only takes effect after the Extensions menu restores it.
   - desqueeze applies from boot. So right after power-on, desqueeze is applied before aspect is restored, until the menu is opened or a photo is triggered. That ordering should be made explicit.

## Proposed path

1. **Composed selectors in one place.**
   - A shared module owns `0x1020BFDA`, `0x1020C02E`, `0x1020C038` and the producer span. It asks aspect for the profile geometry, then applies the desqueeze factor for the caller classes desqueeze targets.
   - Each feature's existing selectors become functions behind it, so standalone builds stay byte-identical.
2. **A single per-file playback install** with the union of shapes and file types (0, 1, 2).
3. **Offline matrix.** Every profile (OFF + 6) × factor (OFF + 4) × JPEG flag, run through:
   - the live-view fit replay;
   - the getters and the complete preview producers;
   - the resize and encoder stages;
   - per-file playback.

   Then compare against the standalone features at the OFF corners.
4. **Mask and frame.** Recompute the soft mask and grid frame from the desqueezed rectangle (aspect × factor). This is the riskiest part, because aspect-profiles needed many camera iterations for the mask.
5. **Camera tests**, in order:
   - live view with 4:3 + 2.0x;
   - JPEG/NEF capture;
   - playback;
   - mask position;
   - power cycle.

Until then the preset composer refuses the combination and points here.
