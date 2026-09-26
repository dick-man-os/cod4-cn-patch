# zh-TW font build pipeline (Phase 3)

This pipeline makes the IW3 font assets needed by the current zh-TW text
candidate. It keeps the original 1,742 glyph entries in each affected font,
appends the 498 missing two-byte GBK entries, and checks the resulting atlas
pixels. It uses OpenAssetTools (OAT) to link compiled `Font_s` JSON and image
assets; it does **not** use OAT's TTF auto compiler.

## Pinned inputs and ownership

- Required characters come from the current payload scan and
  `locales/zh-TW/localization.tw`. The current checkpoint contains **1,288**
  two-byte GBK glyphs.
- The source face is Noto Sans CJK TC Regular, tag `Sans2.004`, from
  [`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk/tree/Sans2.004/Sans/OTF/TraditionalChinese).
  Its OTF SHA-256 is
  `dce08bd4fd91aa8aa76ed8fea4b694c2dfb8550f67871e326843212ddbeb88b4`.
  The builder checks this hash and the Unicode cmap before it draws a glyph.
- Generated font assets carry the Noto [SIL OFL 1.1](../licenses/Noto-CJK-OFL.txt)
  and [attribution](../licenses/Noto-CJK-NOTICE.txt). The original translation
  resources in `patches/` retain their separate copyright status under the
  repository `NOTICE`; this pipeline does not label them MIT.
- The source atlas IWD SHA-256 is
  `c59fba82283ca16cdcb478328675fce850a71112bcb05eef7a270d533878708e`.
  The original `code_post_gfx.ff` SHA-256 is
  `9671d5d7366acb8260a866a21abf558c07b848ca7933132a4015f247d08f6fef`.
- CI downloads OAT v0.33.0 (Linux archive SHA-256
  `aa0b80f633c50d656b26d28b4899fd76bd994024ff0c669303ed5db52cc9e864`)
  and Pillow 12.3.0. The build manifest records the active FreeType version.

## How the builder works

The rasterizer looks up a **Unicode** character in Noto's cmap, then draws it
in a fixed em cell. The JSON `letter` is the two-byte **GBK packed code** that
COD4 uses. For example, `遊` is U+904A and encodes to GBK `DF 5B`, so its
`letter` is `0xDF5B`. GBK `0xD3CE` represents `游`.

Original glyphs keep their baseline, advance, width, height and horizontal UV.
When the normal or extrabig atlas height doubles, their original vertical UV
values are scaled by one half. The original BC3 alpha is decoded into the top
area of a white-RGB, RGBA IWI v6 atlas. The 498 new glyphs are packed in a
fixed code-sorted grid beyond the occupied original rows. The small atlas has
enough unused rows and remains 1024 by 1024.

| Atlas | Original | Built | Used by |
| --- | --- | --- | --- |
| `gamefonts_pc_normal` | 1024×512 | 1024×1024 | normal, console |
| `gamefonts_pc_extrabig` | 1024×1024 | 1024×2048 | extrabig, big, bold, objective |
| `gamefonts_pc_small` | 1024×1024 | 1024×1024 | small |

The seven JSON assets keep each original `pixelHeight`, material, glow material
and the modal CJK metrics of that font. Console is rebuilt alongside the six
core fonts because it shares the normal atlas. Every output file has a SHA-256
entry in `font-build-manifest.json`.

## Reproduction and gates

Run [the CI workflow](../.github/workflows/zh-tw-font-build.yml) on `zh-tw`,
or reproduce its commands locally with Python 3.12, Pillow 12.3.0 and OAT
v0.33.0. The workflow:

1. Regenerates the required character inventory and verifies all 1,288
   Unicode code points are in the pinned font cmap.
2. Unlinks the original fonts and builds twice from identical inputs; every
   output byte must match.
3. Verifies the 1,742 original glyph entries and occupied atlas alpha, while
   checking that each of the 498 added glyphs is present.
4. Checks all 1,288 required glyphs in every built font against the actual
   RGBA atlas pixels, including UV spans and nonzero alpha.
5. Uses OAT to rebuild `code_post_gfx.ff`, unlinks it again, and repeats the
   font and pixel coverage audit. The workflow uploads an **experimental QA
   artifact**, not a release package.

Passing those gates proves asset generation and OAT round-trip. The Windows
game must still load and display the built fastfile and IWD together. Test the
main menu, settings, mission selection, loading screens, subtitles, objectives,
pause menu and dialogs, including `遊戲戰國開關槍體讀載選「」《》`.
