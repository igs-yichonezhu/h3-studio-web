# H3 Studio Web

H3 Studio 的 GitHub Pages 網頁介面。公司主機執行完整 Studio API、GPU 生成和剪輯匯出；瀏覽器只載入操作介面。

網站發布後網址：`https://igs-yichonezhu.github.io/h3-studio-web/`

## 同事使用方式

1. 連上公司內網，開啟網站。
2. 填入管理者提供的公司 Studio Web 網址（連接埠預設 **8795**）及自己的 `h3g_...` 個人金鑰。
3. 進入影片生成、圖片工作室或剪輯室。檔案與專案保存在公司主機的個人資料空間。
4. 共用電腦使用完畢請登出。切換帳號前請先儲存專案並登出。

此網址必須是 Studio Web API，不能填原始 ComfyUI 8188 或共享 Gateway 8190。

## 公司主機準備

主機需有更新後的 `movieeasymake/H3Studio`、已安裝的 Python 依賴與可用模型。

- 管理主機的 H3 Studio 與 ComfyUI 保持運行，並開啟共享 Gateway（預設 `127.0.0.1:8190`）。
- 在管理主機「共享引擎」為每位同事建立個人金鑰。
- 在本 Repo 目錄執行下列安裝命令，套用公司入口及相容性更新。腳本會先檢查版本，保留現有設定與作品；重複執行會識別已套用的更新。

```powershell
./backend/install_company_backend.ps1 -StudioRoot 'D:/影片生成工作流_FISH/movieeasymake'
```

- backend/ 亦附啟動與防火牆腳本。
- 雙擊主機專案根目錄的 `start_h3_web_server.bat`。公司網頁 API 預設為 `http://<公司主機內網IP>:8795`。
- 使用 DHCP 保留或固定 IP；以管理員 PowerShell 執行 `configure_h3_web_firewall.ps1`。只允許同一子網路的 Domain/Private 網路連入 8795。

若 API 使用可信 HTTPS 憑證，可啟動：

```powershell
python H3Studio/web_server.py --cert C:/certs/studio.crt --key C:/certs/studio.key
```

憑證與私人設定不要放入 Repo。不同的網站來源需由管理者以 `--origin https://example.company` 指定；預設僅允許 `https://igs-yichonezhu.github.io`。

## 瀏覽器連線

優先使用公司核准的 Chrome／Edge。從 HTTPS 網頁連內網 HTTP 私有 IP 時，Chrome 可能要求「區域網路存取」權限。其他瀏覽器與公司政策可能不支援此方式，應使用公司核發或公認機構核發、瀏覽器信任的 HTTPS 憑證。不要略過憑證警告。

參考：[Chrome 區域網路存取](https://developer.chrome.com/blog/local-network-access)、[MDN CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS)。

## 資料與登入

- 個人長期金鑰只送往填入的公司主機，不寫入瀏覽器儲存，也不送 GitHub。
- 瀏覽器分頁保存最長 8 小時的登入 session；登出、金鑰換發或停用即失效。
- 每位同事有獨立的 Studio 工作程序、素材、生成歷史、圖片、語音與剪輯專案。
- 圖片、音訊、影片與下載採 30 分鐘的讀取票證，綁定 session、使用者與指定路徑；登入期間自動更新。票證不可執行生成或修改資料。
- 管理入口、引擎啟動與模型安裝仍在公司主機處理。
- 瀏覽器草稿依 Repo、公司主機和使用者分開。GitHub Pages 同一帳號的不同 Repo 共用網站 origin，因此該帳號下發布的所有網站都應視為可信。
- 素材經串流上傳／下載，大檔案不整份載入公司入口 RAM；影片保留 Range seek。

生成工作由共用 GPU 排隊。關閉分頁或登出不會刪除專案；重新登入可繼續。公司主機與服務需要持續開啟。停止公司 API 時會停止它啟動的使用者工作程序；請等進行中的工作完成後再維護。

## Repo 與發布

`site/` 是唯一發布目錄，只包含介面 HTML/CSS/JS 等靜態檔案。`backend/` 是公司部署檔，不會由 Pages 發布。

在 Repo Settings → Pages 將 Source 設為 **GitHub Actions**。推送 main 後，`.github/workflows/pages.yml` 會檢查靜態檔案並發布網站。

首次發布也可在本 Repo 執行 `./publish_github.ps1`：需要先以 `gh auth login` 登入 igs-yichonezhu，腳本會建立公開 Repo、設定 Pages，再推送已提交的 main。此 Repo 的程式碼與介面為公開，個人金鑰、設定與作品僅保存在公司主機。

本版驗證包含 64 項 Python 測試與 7 項前端測試，以及瀏覽器 MP4 上傳、跨來源影片載入及專案儲存。使用真實 CPU 剪輯匯出，未執行公司 GPU 生成或跨電腦網路驗收。

前端由既有 H3 Studio 介面產生。更新主機專案後執行其 `web_deploy/build.mjs` 重新產生 site/；建置會保留上一份輸出於 Git 忽略的 `.site-previous-*`，避免舊檔混入發布。
