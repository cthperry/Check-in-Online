# 出勤簿｜手機上下班打卡

員工以手機登入後，首頁會顯示姓名、台灣時間，以及目前可執行的上班或下班操作。出勤資料集中儲存在伺服器，換裝置登入同一帳號即可繼續打卡。個人裝置可以保持登入 30 天。員工可匯出個人 Excel，管理員可以查詢及匯出全體員工紀錄。

## 專案入口

- [安裝、帳號管理、HTTPS 部署與備份](docs/operations.md)
- [功能規劃、資料規則及驗收情境](docs/plan.md)
- [既有 Sites 測試站](https://check-in-online-pilot.pperry.chatgpt.site)

GitHub 版本使用 Python、SQLite 與 openpyxl，可部署到有持久磁碟的單一主機。Sites 測試站獨立運作，兩邊的登入方式與資料庫各自管理。此儲存庫只收錄程式、文件及使用臨時資料庫的測試。

## 本機啟動

使用 Python 3.12 以上版本，在專案根目錄執行以下指令。首次建立帳號時，終端機會提示輸入密碼。

```sh
python -m pip install -r requirements.txt
python app.py add-user employee01 測試員工
python app.py serve --dev
```

以同一台電腦開啟 <http://127.0.0.1:8000>。手機連線與正式環境設定請見部署文件。

## 驗證

```sh
python -m compileall -q app.py attendance tests
node --check static/app.js
python -m unittest discover -s tests -v
```
