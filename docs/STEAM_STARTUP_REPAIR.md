# Steam 啟動卡在 PunkBuster 的可回復修復

適用情境：Steam 顯示 **Running install script / PunkBuster Anti-Cheat**，
而 COD4 (2007) 長時間無法啟動。

## 先做的事

先在 Windows 工作管理員結束仍卡住的 `PBSetup.exe` / `pbsvc.exe` /
PunkBuster 安裝程序，再執行本工具。

## 為什麼是可選功能

這個修復只針對 **單機戰役或不需要舊 PunkBuster 的使用者**。
如果你仍需要舊式 PunkBuster 多人伺服器，不應停用其 Steam 安裝區塊。

## 使用

把 repo 中的 `tools/steam_startup_repair.py` 放在 COD4 遊戲根目錄執行：

```powershell
py tools\steam_startup_repair.py status
py tools\steam_startup_repair.py disable-punkbuster
```

若腳本本身就在別的資料夾：

```powershell
py tools\steam_startup_repair.py --game-dir "C:\Program Files (x86)\Steam\steamapps\common\Call of Duty 4" status
```

修復會：

1. 只尋找名稱含 `PunkBuster` 的 VDF block。
2. 若不是恰好找到一個，就拒絕修改。
3. 修改前備份原始 `installscript.vdf` 到
   `.cod4tw_startup_bak/installscript.vdf`。
4. 只移除 PunkBuster 安裝區塊，保留 DirectX 等其他安裝項目。

還原：

```powershell
py tools\steam_startup_repair.py restore
```

Steam「驗證遊戲檔案完整性」也可能恢復原始 installscript，因此日後驗證後如果
問題重現，需要再次執行修復。
