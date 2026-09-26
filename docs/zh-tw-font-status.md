# COD4 繁體中文（台灣）字型覆蓋狀態

資料基準：`zh-tw` commit `919c7260c7a62fdf75700010f2b9a9955bfda2c4` 的 [字型 audit run](https://github.com/dick-man-os/cod4-cn-patch/actions/runs/36189663725)；IWD SHA-256 `c59fba82283ca16cdcb478328675fce850a71112bcb05eef7a270d533878708e`。字形清單由候選轉換文字及 `locales/zh-TW/localization.tw` 重建，尚非逐句人工核准的最終翻譯。

## 實測結果

- 需要的雙位元組 GBK 字形：**1,288**。先前的 1,275 是較早的統計；目前清單也納入全形標點與符號。
- 共匯出 9 個 IW3 `Font_s` 字型，其中 6 個主要遊戲／介面字型（big、bold、extrabig、normal、objective、small）各有 1,647 個數值字形代碼（包含單位元組 `0x7F`），其中 1,646 個是雙位元組代碼；每個字型都覆蓋 **790 / 1,288**，缺 **498**。六者缺字集合相同。
- 已覆蓋的 790 個字形都有非零像素寬高及非零 UV 範圍；目前沒有「有代碼但空白」的需求字形。
- `localized_chinese_iw15.iwd` 有 35 個成員、34 個 IWI 影像。三張字型圖集是 IWI v6、格式 `0x0D`（DXT5）：normal 為 1024×512，small 與 extrabig 各為 1024×1024。
- `images/gamefonts_pc_normal.iwi` 只儲存圖像；字元代碼、繪圖尺寸及 UV 對應在 `patches/zone/code_post_gfx.ff` 的 `Font_s` 資產中。COD4 中文路徑把 GBK 兩個位元組合成 `(first << 8) | second`，再查字形表。

## 缺字清單

以下是 `normalfont` 缺少的全部 498 字；六個主要字型相同。其他字型及 GBK 代碼可在上述 CI artifact 的 `font-audit.json` 查看。

```text
丟並亂佈佔併來係個們倫偉側偵偽傑備傢傳傷價儀億儘償優儲兒內兩冊別刪剛創劃劍動務勝勢勵匯區協厭厲參叢員問啟喚單嗎嘗嘰噓嚇嚨嚴國圍圖團執堅報場塊塗墜壓壞夠夢夾奧媽學
實寫將尋對導屍層屬峽崥師帶幫幹幾庫廢廣張強彈彎徑從復惡態慮憶應戰戲戶掃掛換揮揹損撐撿擁擇擊擋擔據擬擲擺擾攤敗敵數斃斷時暫曠會東柵條棄極榮構槍槳樓標樣樹橋機檔檢檯
櫋欄權歐歡歷歸殘殺殼毀氣決沒況洓淨測湧準溫溼滅滾滿漬潛潰濾瀍瀔灘為無煙煢煩熱燈燒營爍爛爭爾牆狀《》「」猶獅獎獲獵獻現環畢當瘋發盡監盤盧確碼礙碟禦禿種稱穩窩筆節範
築篩簡紀紅紋紐純級紛終組結絕絡給統綁經綠網緊緒線緩練總績織繩繫繳繼續羅義習聞聯聲聽肅脅脫腦臥臺與興舊艙艦茰茲莊乾華萊萬蓋甚薦薩藍藥藼蘭處虛號套螞蟻衛衝補裝裡複襲
見規視覺觀計討訓記設許訴註評試話該誌認語誤說誰調請諾謀謝著證譯議譴護讀變讓豬貝貢貨責貴費資賓賦質賭賽趕跡蹤躍車軌軍較載輕輛輪輸輻轉轟辦農捱這連進遊運過達違遜遠適
遲選遺還邊邏釋針後徵鈕夥鉤鋒錄錘錦錯鍋鍵鎖鎮鏈鏡鐘鐵鑽長門閃閉開間闊關陣陰陸陽隊階隑隨險隱雖雙離難電霧靜於韋響頂項順須預頓領頭頻題額類顥顯風颶飛餘馬駕駛駸穸騎騰
驅驗驚體髮鬆鬥鬧魯鮑鳥鷂鷲鷹麥麼黃點
```

## 目前的製作阻礙

[OpenAssetTools v0.33.0](https://github.com/Laupetin/OpenAssetTools/blob/v0.33.0/docs/SupportedAssetTypes.md) 可以匯出及載入 IW3 `Font_s`。但其 [TTF 自動編譯器](https://github.com/Laupetin/OpenAssetTools/blob/v0.33.0/src/ObjCompiling/Font/FontCompiler.cpp.template) 只產生 `0x20`–`0xFF` 字形，無法直接為上述 GBK 雙位元組代碼產生對應的字表與圖集。單改 IWI 圖像也無法建立字元映射。現階段不能把這個 audit 當成可安裝繁中 build。

下一個可執行方案：先選定授權清楚、確實含全部核准字形的字型來源；再做固定輸入與版本的圖集排版、字形尺寸／UV 及 GBK 代碼輸出，交由 Linker 載入編好的 `Font_s`，並驗證新圖集與 fastfile 的一致性。最後需在 COD4 單機介面和戰役中實際檢查缺字與排版。對罕見字先人工核對文字來源，避免為掃描雜訊製作字形。

原始 `patches/` 翻譯資料的權利歸屬仍依 [NOTICE](https://github.com/dick-man-os/cod4-cn-patch/blob/zh-tw/NOTICE)，本報告不變更其授權。

