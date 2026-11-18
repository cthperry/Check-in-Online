# 手機打卡系統｜建立帳號與修改密碼 SOP

適用於管理員在 Windows PowerShell 維護正式站 https://checkinonline.vercel.app 的帳號。手機登入頁目前沒有帳號管理功能。

## 1. 開啟專案並準備 Python

```powershell
cd 'C:\Users\Perry\Documents\Codex\2026-11-18\new-chat-2\work\Check-in-Online'
```

首次使用請先安裝 Python 3.12，再執行下列兩行。若已有安裝好相依套件的專案虛擬環境，可直接使用。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. 在本次 PowerShell 連接正式資料庫

在 Vercel 專案 [checkinonline 的 Storage](https://vercel.com/perrys-projects-e5c94030/checkinonline/stores) 開啟專用資料庫 **checkinonline-attendance**，從 Neon 的 Connect 畫面選擇正式分支與 neondb，複製 PostgreSQL 連線字串。只複製從 `postgresql://` 開始的網址。

將下方區塊貼入 PowerShell，再依提示貼入連線字串。輸入內容會隱藏。區塊會核對資料庫名稱，並以唯讀查詢確認已知的正式管理員帳號。

```powershell
$secret = Read-Host '貼上 checkinonline-attendance 正式資料庫連線字串' -AsSecureString
$env:DATABASE_URL = [System.Net.NetworkCredential]::new('', $secret).Password
$secret.Dispose()
.\.venv\Scripts\python.exe -c "import os, psycopg; from urllib.parse import urlsplit; u=os.environ['DATABASE_URL']; assert urlsplit(u).scheme in ('postgresql','postgres'), 'Invalid PostgreSQL URL'; c=psycopg.connect(u, connect_timeout=8); print(c.execute('SELECT current_database(), account, role FROM staff WHERE account=%s', ('perry_chuang',)).fetchone()); c.close()"
```

看到 `('neondb', 'perry_chuang', 'admin')` 後再往下操作。若得到錯誤或 `None`，先核對所選資料庫。每次新開 PowerShell 都要重做這一步，避免指令套用到本機 SQLite。

## 3. 建立帳號

以下為範例。將帳號與姓名換成實際人員，按需求擇一執行。

```powershell
# 一般員工：可以打卡、匯出個人紀錄
.\.venv\Scripts\python.exe app.py add-user frank "Frank" --role staff

# 管理員：也可以檢視與匯出全體紀錄
.\.venv\Scripts\python.exe app.py add-user team_admin "管理員姓名" --role admin
```

依提示輸入新密碼兩次，長度 12 至 256 個字元。帳號長度 1 至 48 個字元，以英文字母或數字開頭，也可包含底線、句點及連字號。登入時不分英文字母大小寫。若帳號已存在且只需改密碼，使用下一節指令。

## 4. 修改既有帳號密碼

修改 Perry 自己的密碼：

```powershell
.\.venv\Scripts\python.exe app.py reset-password Perry_Chuang
```

依提示輸入新密碼兩次。此管理指令使用資料庫管理權限，無須舊密碼。成功後會撤銷該帳號在所有裝置上的登入狀態。重新開啟正式站，用原帳號與新密碼登入即可。

## 5. 驗證與結束

開啟正式站，確認登入後顯示正確姓名。驗證帳號無須按上下班打卡。操作完成後清除本次 PowerShell 的連線設定，並清除剪貼簿中的連線字串。

```powershell
Remove-Item Env:DATABASE_URL
Set-Clipboard -Value ''
```
