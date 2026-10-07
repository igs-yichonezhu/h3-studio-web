# H3 Studio Web

H3 Studio 的 GitHub Pages 網頁介面。公司主機執行完整 Studio API、GPU 生成和剪輯匯出；瀏覽器只載入操作介面。

網站發布後網址：`https://igs-yichonezhu.github.io/h3-studio-web/`

## 同事使用方式

1. 連上公司內網，開啟網站。
2. 在空白的 **GPU Gateway 網址**欄位填入自己的共享引擎網址（預設連接埠 **8190**），及**該 GPU 電腦核發**的 `h3g_...` 個人金鑰。每台 GPU 可以有不同 IP 與金鑰；登入頁不預選 GPU，也不沿用上位使用者的 GPU 網址。
3. 進入影片生成、圖片工作室或剪輯室。檔案與專案保存在公司主機的個人資料空間。
   同一瀏覽器的三個工作室共用一次登入；另開圖片／剪輯分頁會自動驗證既有登入，不必重填金鑰。
4. 共用電腦使用完畢請登出。切換帳號前請先儲存專案並登出。

「網頁工作區服務設定」內的 Web 網址由管理者預填（預設 **8795**）。它負責素材、專案與剪輯；GPU Gateway 8190 負責各自的生成引擎。不要把原始 ComfyUI 8188 填入 GPU Gateway 欄位。

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
- 同事使用不同 GPU 時，在主機私人 `H3Studio/data/web_server.settings.json` 設定 `remote_auth: true`、`allow_custom_gateways: true` 及預設 `gateway_url`。所有金鑰均向登入時指定的 GPU Gateway 驗證；不同網址＋金鑰有獨立工作區。
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

- 登入前先檢查 Web 服務相容性；個人金鑰由選定的公司 Web 服務轉交指定的 GPU Gateway，不寫入瀏覽器儲存，也不送 GitHub。瀏覽器與 Web 主機、Web 主機與 GPU Gateway 都需要網路通行。
- 自訂 GPU Gateway 僅接受 RFC1918 IPv4／fc00::/7 IPv6 內網 IP，拒絕 DNS、loopback、公開 IP、子路徑與轉址。管理者可保留固定 GPU 模式；詳細設定與金鑰輪替資料移轉見 backend/H3Studio/WEB_DEPLOYMENT.md。
- 同一瀏覽器保存最長 8 小時的 Web 登入憑證，影片、圖片與剪輯共用；不保存 GPU 原始金鑰。任一分頁登出，其他分頁也停止使用該登入；金鑰換發、停用或後台重啟後需重新登入。不同瀏覽器或瀏覽器使用者設定檔需各自登入。
- 每位同事有獨立的 Studio 工作程序、素材、生成歷史、圖片、語音與剪輯專案。
- 圖片、音訊、影片與下載採 30 分鐘的讀取票證，綁定 session、使用者與指定路徑；登入期間自動更新。票證不可執行生成或修改資料。
- 管理入口、引擎啟動與模型安裝仍在公司主機處理。
- 瀏覽器登入記錄與草稿以 Repo 路徑命名，草稿另依公司主機和使用者分開；路徑命名不提供瀏覽器安全隔離。GitHub Pages 同一帳號的不同 Repo 共用網站 origin，因此該帳號下發布的所有網站都應視為可信，能讀取同一 origin 的 Web 登入憑證。
- 素材經串流上傳／下載，大檔案不整份載入公司入口 RAM；影片保留 Range seek。

生成工作交給各自選擇的 GPU 排隊。同一組 Gateway 網址＋金鑰重新登入可繼續；換 GPU 或金鑰會開啟另一個資料空間。公司 Web 主機與選定的 GPU 服務需要持續開啟。停止公司 API 時會停止它啟動的使用者工作程序；請等進行中的工作完成後再維護。

影片工作每 3 秒、圖片工作每 4 秒自動同步；提交成功立即顯示工作，完成後自動顯示成果。讀取逾時會恢復輪詢，返回分頁或網路恢復時立即同步，保留表單與專案。正在播放的影片不阻擋其他工作更新。後台每 5 秒核對引擎完成紀錄，避免持續狀態訊息延後完成判定；安裝套件包含 progress-monitor.patch。網站更新後重新整理一次即可載入版本化的介面資源。

生成同步修正通過 116 項既有介面／圖片回歸、30 項網頁建置／登入／更新、62 項引擎監控／佇列／Qwen 及 19 項 Web 後台測試。安裝腳本在 Windows PowerShell 5 與 PowerShell 7 均通過首次、重複及舊公司入口升級的隔離驗證，保留設定與資料。

## Repo 與發布

`site/` 是唯一發布目錄，只包含介面 HTML/CSS/JS 等靜態檔案。`backend/` 是公司部署檔，不會由 Pages 發布。

在 Repo Settings → Pages 將 Source 設為 **GitHub Actions**。推送 main 後，`.github/workflows/pages.yml` 會檢查靜態檔案並發布網站。

首次發布也可在本 Repo 執行 `./publish_github.ps1`：需要先以 `gh auth login` 登入 igs-yichonezhu，腳本會建立公開 Repo、設定 Pages，再推送已提交的 main。此 Repo 的程式碼與介面為公開，個人金鑰、設定與作品僅保存在公司主機。

本版驗證包含既有 64 項 Python 回歸測試、19 項 Web 後台測試與 20 項前端測試。不同 Gateway／金鑰驗證與撤銷、真實工作程序的目標設定與專案隔離均已測試。也已驗證瀏覽器 MP4 上傳、跨來源影片載入、專案儲存與 CPU 剪輯匯出；未執行公司 GPU 生成或同事電腦跨機網路驗收。

前端由既有 H3 Studio 介面產生。更新主機專案後執行其 `web_deploy/build.mjs` 重新產生 site/；建置會保留上一份輸出於 Git 忽略的 `.site-previous-*`，避免舊檔混入發布。

建置時可用 `H3_WEB_DEFAULT_SERVER` 環境變數預填公司 Web 服務。GPU 網址由每位使用者登入時自行填寫，留空時不會自動選用預設 GPU。請勿在部署資料中填入金鑰或含帳密的 URL。
