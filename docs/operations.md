# 安裝與營運

## 部署條件

使用 Python 3.12 以上、單一應用主機、可持久保存的本機磁碟與 HTTPS 反向代理。SQLite 資料庫預設為 `data/attendance.sqlite3`，可用 `ATTENDANCE_DB` 指定絕對路徑。請將資料目錄權限限於服務帳號及授權維運人員。GitHub Pages 僅提供靜態網頁，本服務需要持續執行的 Python 後端。

## 正式服務

1. 從 GitHub 取得程式，於虛擬環境執行 `python -m pip install -r requirements.txt`。
2. 建立員工與至少一個可匯出全體紀錄的管理員。帳號允許小寫英文字母、數字、點、連字號及底線，以字母或數字開頭。密碼以隱藏輸入提示設定，長度 12 至 256 字元。
3. 將服務主機的時鐘設為自動校時。設定使用者實際開啟的 HTTPS 原始網址及持久資料庫路徑。以下 PowerShell 範例的網域與磁碟路徑需換成實際值。
4. 使用系統服務管理器常駐執行 Waitress。反向代理負責 TLS，將同網域的全部路徑轉送到本機的 8000 埠。從手機開啟 HTTPS 網址；網址的通訊協定、主機與連接埠必須與 `ATTENDANCE_ORIGIN` 一致。

```powershell
$env:ATTENDANCE_ORIGIN = 'https://attendance.example.com'
$env:ATTENDANCE_DB = 'D:\attendance-data\attendance.sqlite3'
python app.py add-user employee01 測試員工
python app.py add-user manager01 測試主管 --role admin
python app.py serve
```

管理命令和服務必須指向同一份資料庫。跨容器反向代理可設定 `BIND_HOST` 與 `PORT`。限制後端網路只允許反向代理存取。預設登入限速按帳號及直接來源位址計數；使用反向代理時，共用代理位址的員工會共用來源限額。

## 帳號、漏卡與匯出

- 員工以帳號登入，首頁姓名來自名冊。管理員於「出勤紀錄」選擇「全體員工」，設定日期再匯出。Excel 會包含出勤明細及可見班次的補登歷程。
- 遺失手機或忘記密碼時執行 `python app.py reset-password employee01`。離職時執行 `python app.py disable employee01`。後續操作會拒絕舊登入階段，歷史紀錄保留。
- 補登前使用 `python app.py open-shifts` 確認班次編號及員工。取得核准後執行下方範例。指令由受授權的主機維運者操作；經手人欄依其輸入留存。
- 使用者操作出現連線錯誤時，先重整出勤紀錄。依確認結果處理，避免把實際已成功的打卡誤當成漏卡。

```sh
python app.py close-shift 17 2026-11-18T18:00 --reason "員工申報漏打，主管核對" --operator "值班管理員"
```

## 備份與還原驗證

使用 SQLite 線上備份 API 將一致快照寫入新的受保護檔案。範例指向目前設定的資料庫。備份檔含登入相關資料，請依正式資料相同標準限制存取，並另存至獨立儲存空間。

```sh
python -c "import os,sqlite3,uuid; source=sqlite3.connect('file:'+os.environ.get('ATTENDANCE_DB','data/attendance.sqlite3')+'?mode=ro',uri=True); target=sqlite3.connect('backup-'+uuid.uuid4().hex+'.sqlite3'); source.backup(target); print(target.execute('PRAGMA integrity_check').fetchone()[0]); target.close(); source.close()"
```

還原演練應在隔離環境，用新路徑載入備份，核對員工數、班次數、最新時間與 Excel。正式還原前停止服務並另存現況；使用新的資料庫路徑以免混用原處的 WAL 檔。還原後以受授權維運者身分清除還原資料庫的 sessions，再開放員工重新登入。

## Vercel 正式部署
在 checkinonline 專案連接專用 Neon PostgreSQL，套用 Production 環境並重新部署。首次啟動會建立資料表。本機管理指令可透過系統環境變數 DATABASE_URL 連到同一資料庫，再依前述 add-user 流程建立帳號。
