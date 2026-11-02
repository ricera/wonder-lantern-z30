# Desqueeze persistence: static analysis of native settings storage (option B)

Goal: keep the desqueeze factor somewhere the camera itself saves across power-off, so the display-fit hook can read it during the very first live-view layout at boot. This would avoid SD file I/O and a boot hook.

All addresses are Z30 C 1.20 EG. "Runtime" addresses in `0xC18xxxxx` are scatter-loaded from image `0x11F81C94 + (addr - 0xC1800000)`. Static analysis only, with no camera or QEMU confirmation yet.

## Property store

- **Public API.** `0x10465984` (get) → `0x10219F32` → `0x101993DE` → `0x10199AB4` → `0x100F303C`.
  - The read is a lock-free table lookup and a plain load by type: u32, u8, u16, s8 or s16. There are no waits or locks.
- **Keys.** A key is `group << 8 | index`. `0x100F300C` maps a key to its value-table index through the group table.
- **Group table.** At `0x114F774C`: 0x56 groups × 28 bytes. The fields are:
  - `size`
  - `ram_base`
  - `default_blob`
  - `first_index | count << 16`
  - a block-set id
  - a bank/callback pointer
  - `class`, where 0 = volatile, 1 = persistent, 3 = large persistent blob
- **Value table.** At `0x114EB97C`: 16 bytes per entry, `type, size, ram, default_ptr`.
  - For example, `0x501`: type 7, size 4, RAM `0xC2525BD4`.

## How persistence works

- **Block list.** Descriptor `0x100F38D8` → `{count 0x6C, list 0xC18B1950 (image 0x120335E4), 0x14, header+4}`. The list holds 108 `(ram, size, 0)` blocks:
  - the settings header block `0xC2525318` (0x1B8 bytes);
  - every class-1 group, as a whole block;
  - 5 extra bank copies for banked groups. Banked groups have a non-zero pointer in group word 5: groups 2, 5, 7, 8, 0x0C, 0x0E, 0x0F, 0x11, 0x13, 0x2D and 0x55.
- **Boot.**
  1. `0x101E0C38` (called from early init `0x1005362A`) loads block-set 0xF with `0x1021247E`.
  2. It then validates with `0x100F380E`. If the header magic `[0xC2525318] == 0x19368`, it re-applies the header defaults and runs the settings-version migration `0x1007081E` (key `0x4100`). Otherwise it resets every group to its default blob.
- **Save.**
  - `0x101E0CF4(0)` writes all blocks through `0x10212514`.
  - It is triggered by event `0x1000` in the settings task loop `0x101E0E74`. The save runs whenever the flag `[0xC18AB984]` is set. That flag is initialised to 1 in the image and only cleared by `0x101E0CEC`.
  - The flush `0x101E0F8E` posts the event and waits for it.
  - `0x10219D0A` and `0x1032D796` also save.
  - **Saving is block-wise and not driven by a dirty flag.** Any byte inside a listed block is written as it is in RAM.

## Spare storage: padding holes in unbanked persistent groups

`tools`-independent scan (scratch scripts): for each persistent group, mark the bytes covered by value-table entries. Bytes not covered are **struct padding**:
- they are saved and restored with the block;
- no key can address them, because property get/set copies exactly `size` bytes;
- "reset to defaults" restores them from the group's default blob, which is zero.

Candidate groups have no code references to their RAM other than the backup list entry: `0x01, 0x2A, 0x39, 0x3B, 0x3D, 0x43, 0x4B`. Group `0x14`, by contrast, has direct references (`0x10A218B4`, `0x10BD5254`).

**Chosen: `0xC25254EE`, 2 bytes, in group 0x01.**
- **Why it's safe:**
  - It is padding between key `0x0107` (u16 at `0xC25254EC`) and key `0x0108` (u32 at `0xC25254F0`).
  - The default blob is `0000`.
  - Group 0x01 is unbanked (word 5 = 0). Its only pool reference is the backup list entry `0x120335F0`.
- **Proposed encoding:** byte 0 = factor index (0–4), byte 1 = `index ^ 0xFF`. Zero bytes (factory, reset or migration) decode as OFF.
- **Alternatives** in the same group: `0xC2525513` (1 byte, after u8 keys `0x0110..0x0112`) and `0xC253A80D` (3 bytes in group 0x2A).

## Remaining risks (need camera confirmation)

- **Boot order.** That the block load (`0x101E0C38`, early init) runs before the first live-view layout. This is expected, since every shooting setting must be loaded first.
- **Hidden whole-struct users.** That no code reaches group 0x01 RAM through a computed pointer, such as a base held in another object. The scan only covers literal pools.
- **Settings save/load to card.** Nikon's "Save/load menu settings" is expected to be key-based. If so, the factor is not carried in that file and a load leaves it unchanged.
- **Firmware updates.** A firmware update that changes the settings version may reset groups to their defaults, which gives OFF. That is acceptable.

## Flash format of block-set 0xF (settings journal)

Storage area 15 is defined in the partition and area tables at `0xC1880D2C` / `0xC1880888`. It has two banks, A at flash `0x12E00000` and B at `0x131E0000`, each `0x40000` bytes. Settings use `0x19368` of that. The header word `[0xC2525318]` equals the total size, so it doubles as a layout check.

**Save** (`0x10212514` → `0x1008F630` → `0x1008F504`): an append-only journal.
1. `0x1008F296` reads the current bank's generation counter and increments it.
2. Space needed is the sum, over list entries with a non-zero word +8, of `8 + align4(size)`, plus 8. If the current bank has room, the records are appended. Otherwise the other bank is erased (`0x1008EF22`, in 128 KiB units, with 3 retries) and every entry is marked (+8 = 1) for a full rewrite.
3. Each record is `u32 index, u32 size, data[size]`, padded to 4 bytes. Writes go through `0x1008EF46` (half-word program, 3 retries).
4. The journal ends with a 4-byte end marker and the generation counter.

**Load** (`0x1021247E` → `0x1008F8B4` → `0x1008F80C`):
1. Every list entry's word +8 is cleared.
2. The loader walks the records. Classifier `0x1008EF9A` returns 0 = valid, 1 = invalid, 2 = end/erased, 3 = end marker. A record is invalid if it overruns the bank, if `index >= count`, or if `size != list[index].size`.
3. For each valid record, `list[index].+8` is set to the record's data, so a later record replaces an earlier one.
4. **Any invalid record fails the whole load.** So does any list entry left without a record.
5. On success, each block is copied from its newest record using the list's own size.
6. A failed load leaves `0x100F380E` with r0 = 0, and all groups are reset to their defaults.

Word +8 of each list entry is shared: the loader stores the record pointer there, and the writer treats a non-zero value as "write this block". Nothing clears it after a successful load or save. In practice, every save appends all 108 blocks, which is why the padding hole is saved without any dirty marking.

### Consequences for adding a 109th block

- **Patched firmware reading a stock journal:** there is no record for index 108, so the load fails and all settings reset. Patching the loader to allow the new entry to be missing would avoid this.
- **Stock firmware reading a patched journal:** the record with index 108 is out of range (`index >= count` gives 1), so the load fails and all settings reset. There is no memory corruption: the bound check runs before the list is indexed. The reset happens again until the stock firmware rewrites the journal.
- **Changing the size of an existing block** fails the size check in both directions.
- **Free space:** each journal pass costs about 101 KiB, so a bank fits about two saves before it compacts into the other bank. A small extra block costs 8 bytes plus its data per save.
