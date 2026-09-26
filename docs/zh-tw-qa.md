# COD4 繁體中文（台灣）單機 QA

這是自動轉換候選版，供 Windows Steam COD4 單機驗收。尚未完成逐關人工翻譯 QA，
也未宣稱已通過遊戲內顯示或 GPU 相容性驗收。請只啟動 `iw3sp.exe`。

本版支援已核對 SHA-256 的乾淨 Steam Windows 英文安裝（App 7940、build 2737681）。
安裝器會核對原始檔案、解壓後資料與每一個 patch 範圍；版本不同會停止。
若有既有漢化或備份，請先使用原安裝器還原，不要刪除備份來繞過檢查。

## Windows 安裝與還原

1. 關閉 COD4。將 QA ZIP 完整解壓至獨立資料夾，例如 `C:\COD4-zhTW-QA`。
2. 以 PowerShell 進入該資料夾。先執行只讀檢查（可能需一至數分鐘）：

   ```powershell
   py -3 cod4_cn_patch.py install --locale zh-TW --qa-assets . --game-dir "C:\Program Files (x86)\Steam\steamapps\common\Call of Duty 4" --dry-run
   ```

3. 檢查通過後，在有遊戲目錄寫入權限的 PowerShell 中安裝：

   ```powershell
   py -3 cod4_cn_patch.py install --locale zh-TW --qa-assets . --game-dir "C:\Program Files (x86)\Steam\steamapps\common\Call of Duty 4"
   ```

4. 備份與交易記錄保存在遊戲的 `.cod4cn_bak`。安裝失敗會回滾；若回滾未完成，
   安裝器保留備份並報錯。請勿手動刪除此資料夾。
5. 完成測試後還原：

   ```powershell
   py -3 cod4_cn_patch.py uninstall --locale zh-TW --game-dir "C:\Program Files (x86)\Steam\steamapps\common\Call of Duty 4"
   ```

還原前會檢查備份及安裝後檔案。若檔案被其他工具修改，會停止並保留備份。
一般 `uninstall` 也能辨識 QA 交易記錄。原簡中預設安裝流程不變。
安裝會使用原專案的中文資源載入配置，並將需要的單機 fastfile 同步到 Steam 英文路徑；
不修改 MP fastfile、PunkBuster、存檔或個人設定。

## 驗收清單

| 畫面／情境 | 檢查內容 |
| --- | --- |
| 主選單 | 繁中、字型、選項位置 |
| 設定頁 | 長字串是否溢出或裁切 |
| 任務選擇 | 任務名、說明、混合簡繁 |
| 載入畫面 | 中文完整、換行、段落 |
| 遊戲字幕 | 缺字、斷行、標點、可讀性 |
| 任務目標／HUD | 小字字型、行距、位置 |
| 暫停選單 | 字型尺寸與各選項 |
| 確認／錯誤對話框 | 引號、書名號、文字完整 |
| 戰役抽查 | 前段、中段、後段各至少一關 |

特別檢查：`遊戲戰國開關槍體讀載選「」《》`。
記錄 `□`、裁字、換行錯誤、atlas 串色、行距異常、標點錯誤、簡繁混用，
以及 Noto 新增字與舊字的視覺落差。確認 1024×2048 atlas 在 IW3 runtime 中正常顯示。
CI 的字形像素與 round-trip 檢查不能代替這些實機驗收。

可在 `manifests/ui-values.json` 查閱介面轉換，在 `manifests/text-build.json`
查閱自動翻譯候選。這些候選的 `approved` 仍為 false，不表示人工翻譯已批准。
來源 2,487 個候選均通過等長 gate；單機安裝其中 2,486 個，另 1 個屬 MP 而排除。
另有一個原始 `aftermath.ff.dump.bin` 僅含英文且檔名沒有 offset，已明確列為不安裝。

## 重建與公開發布界線

開發環境使用 Python 3.12、Pillow 12.3.0、OpenCC reimplemented 0.1.7，以及
固定 SHA-256 的 Noto Sans CJK TC 2.004 與 OpenAssetTools v0.33.0。
在 repo 根目錄執行（`--work` 與 `--output` 必須為不存在或空的資料夾）：

```text
python tools/zh_tw_qa_build.py --oat-bin path/to/oat --font path/to/NotoSansCJKtc-Regular.otf --work work/qa --output qa-build
```

整合後的需求字庫為 1,518 字，比 Phase 3 增加 230 個介面／選單用字。
七套 Font_s 各保留原有 1,742 筆並加 596 字，共 2,338 筆；三張 atlas 的尺寸不變。
兩次建置必須產生相同文字、font/atlas、套件 manifest 與 QA ZIP。
此保證限定同一固定工具環境；Windows 與 Linux 的 OAT 壓縮 fastfile 位元組可能不同，
但兩者都必須通過完整文字、字型及影像回讀。

CI 僅公開雜湊、文字及檢查報告，不上傳完整 QA FF/IWD 或 ZIP，不建立 GitHub Release。
公開 release 仍待 Windows 實機驗收、逐關語義與台灣用語審查。

## 版權

原 `patches/` 翻譯資料仍屬 2009 游俠漢化組，`NOTICE` 原文保留。
QA 中的轉換衍生資料不宣稱 MIT。Noto 字形來源附 SIL OFL 1.1 與署名。
安裝器程式的 MIT 授權不涵蓋原翻譯資料。
