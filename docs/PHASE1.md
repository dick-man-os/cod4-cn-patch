# COD4 繁體中文（台灣）Phase 1

上游基準：`thedavidweng/cod4-cn-patch`，main commit
`80cba124b26852d6ded65adebbb0d320c6c5d97d`（2026-09-26 檢查）。

## 本階段完成範圍

1. 保留上游安裝器，不修改原始簡中 binary payload。
2. 新增 GBK/CP936 payload 掃描器，只擷取 NUL/0xFF 邊界內、可嚴格 GBK 解碼且含 CJK 的文字 span。
3. 可選使用 OpenCC `s2twp` 產生台灣繁中候選。
4. 每個候選都驗證：
   - 可否嚴格編碼回 GBK
   - byte 長度是否與原 span 完全一致
5. build 只接受 `approved=true` 且 exact-length 的條目；來源 byte 發生 drift 時拒絕寫入。
6. 可輸出繁中 CJK glyph inventory，供 Phase 2 字型資源檢查。

## 安全原則

- 不在來源 `.bin` 上 in-place 修改。
- 不對長度不一致的翻譯做 NUL padding 或截斷。
- 不猜測二進位結構。
- 不把 `patches/` 宣稱為 MIT；上游 NOTICE 明確指出原翻譯資料版權屬 2009 游俠漢化組。
- 發布前需另外決定翻譯資料的合法散布策略。

## 建議命令

```powershell
py -m pip install opencc-python-reimplemented

py tools\\zh_tw_payload_tool.py scan patches\\zone\\chinese `
  --out work\\zh_tw_manifest.json `
  --glossary tools\\zh_tw_glossary.json `
  --auto

# 人工審核 manifest，僅將確認項目設 approved=true

py tools\\zh_tw_payload_tool.py build work\\zh_tw_manifest.json `
  --source patches\\zone\\chinese `
  --output build\\zh-tw\\zone\\chinese `
  --clean

py tools\\zh_tw_payload_tool.py glyphs work\\zh_tw_manifest.json `
  --localization locales\\zh-TW\\localization.tw `
  --out work\\required_zh_tw_glyphs.txt
```

## 下一階段

- 取得完整 fork 後，對全部 payload 跑 manifest。
- 統計 exact-length / mismatch / GBK-unencodable 數量。
- 抽出 mismatch 做人工台灣用語調整。
- 解包 `localized_chinese_iw15.iwd`，核對 `gamefonts_pc_normal.iwi` 對所需繁中字形的覆蓋率。
