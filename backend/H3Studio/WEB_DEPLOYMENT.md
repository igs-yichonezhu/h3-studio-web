# GitHub Pages 與公司內網部署

網頁 Repo: `igs-yichonezhu/h3-studio-web`，Pages 網址發布後為 `https://igs-yichonezhu.github.io/h3-studio-web/`。

Web 主機執行專案根目錄 `start_h3_web_server.bat`，提供完整 Studio API（8795）。GPU 電腦需維持 ComfyUI 與有金鑰保護的共享 Gateway（8190）運作。新版網頁主要欄位填入自己的 GPU Gateway 網址與那台電腦核發的金鑰；進階「網頁工作區服務設定」填 Web 主機的 8795。不同 GPU 電腦不需各自安裝 Web 服務，但中央 Web 主機必須能連到它們，且同事瀏覽器必須能連到 Web 主機。

現有本機介面與作品不會搬動。Web 使用者新增資料在 `H3Studio/data/web_users/<Gateway使用者ID>/data/`，個人連線設定在該使用者自己的 `config.json`。工作程序只監聽 loopback，所有請求另需入口生成的內部隨機 token。

首次登入的 GPU 網址欄位保持空白，由使用者自行填入，不會自動改用管理者預設 GPU。同一瀏覽器的影片、圖片與剪輯共用最長 8 小時的 Web 登入憑證，每頁先向 `/web/session` 驗證才開啟；GPU 原始金鑰不保存在瀏覽器。任一分頁登出或換使用者會鎖住其他工作室；原本剪輯草稿保留在原使用者空間，不能直接切換身份。

共享登入只適用同一瀏覽器設定檔。GitHub Pages 同帳號的其他 Repo 與本站共用 origin，可讀到 localStorage 登入憑證；Repo 路徑前綴只是命名，需將同一帳號發布的所有網站視為可信。Chrome／Edge 使用 Web Locks 序列化共享登入更新，持久的協定標記阻止登出後還原舊分頁登入。儲存被禁止時不會留下半完成登入，而會撤銷新 Web 憑證並提示瀏覽器儲存限制。

若 GPU Gateway 位於另一台電腦，可使用 `--gateway-url http://<GPU內網IP>:8190 --remote-auth`。也可在主機私人檔案 `H3Studio/data/web_server.settings.json` 設定 `gateway_url`、`remote_auth: true` 及 `origins`，之後雙擊原啟動腳本會沿用設定；不要將此檔案或金鑰提交至 Repo。Web 主機網址仍填 Web 電腦的 8795，個人金鑰則使用該遠端 GPU Gateway 核發的金鑰。

若同事使用不同 GPU 電腦，另加 `--allow-custom-gateways`，或在私人設定加入 `allow_custom_gateways: true`（必須配合 `remote_auth: true`）。每次登入可傳 `gateway_url`，登入、session、媒體票證、生成與 worker 設定皆固定使用該網址，不會在連線失敗時改用預設 GPU。金鑰必須由指定 Gateway 核發；用 A 主機的金鑰登入 B 主機會被 B 拒絕。固定模式拒絕與管理者設定不同的網址。

使用者自訂 GPU 網址只接受 IPv4 的 10/8、172.16/12、192.168/16 或 IPv6 fc00::/7 的 IP 原文；不接受 DNS、loopback、link-local、公開 IP、轉址、帳密或子路徑。Gateway HTTP 與 WebSocket 轉址皆在 Web 工作程序內拒絕。預設最多 16 個工作程序、同時最多啟動 2 個；大量同事部署需依 Web 主機 RAM/CPU 調整 WorkerPool 容量。登入頁的 Web 服務 health 只檢查相容性，不取代管理者確認公司服務網址。

遠端模式在登入及後續 API／媒體請求重新向 GPU Gateway 驗證，停用後的新請求立即拒絕；已開始的下載不會中途終止。GPU 暫時離線會回 503，保留 session 供重試。遠端 Gateway 未提供穩定使用者 ID，因此以 Gateway 與金鑰建立獨立資料空間；同一金鑰重登會保留資料，換發金鑰會建立新空間。請在換發前下載完整剪輯專案備份，或由管理者核對身份後移轉舊工作程序的私人 data 目錄。本機驗證模式仍沿用 Gateway 使用者 ID，換發金鑰不會建立新空間。

管理員 PowerShell 可執行 `configure_h3_web_firewall.ps1`，只對 Domain/Private 網路同子網路開放 TCP 8795。入口也拒絕公開 IP 來源。公司 Wi-Fi 的不同 VLAN 或端點防護可能需要 IT 調整指定來源規則。

預設網站 Origin 為 `https://igs-yichonezhu.github.io`。本機預覽可額外指定 `--origin http://127.0.0.1:8876`；部署時不需保留測試 Origin。可信 HTTPS 憑證透過 `--cert` 與 `--key` 指定；不會自動停用憑證驗證。

金鑰輪替及停用沿用核發金鑰那台 GPU 主機的「共享引擎」。入口每次請求重新驗證，撤銷 session 和媒體票證立即生效。一般使用者可在登入時選擇已開放的 Gateway，但登入後無權變更該工作程序的引擎網址、模型或安裝程式；切換 GPU 或金鑰前先儲存專案並登出。

每位 Web 使用者的工作程序會額外使用 CPU/RAM；GPU 任務仍使用原有共享 Gateway 排隊。停止 Web 入口前先確認沒有進行中的生成或匯出工作。Web 使用者的新空間不會自動匯入管理主機或其他同事的私人歷史；可用原有完整剪輯專案備份移轉。

## 建置

```powershell
cd web_deploy
npm ci --ignore-scripts
node build.mjs "D:/影片生成工作流_FISH/h3-studio-web"
npm test
```

每次只發布新產生的 `site/`，不發布設定、data、模型、Python 後端與測試素材。GitHub Actions 僅上傳此目錄。

## 驗證

```powershell
cd H3Studio
python -m unittest discover -s tests -p test_web_server.py -v
python -m unittest discover -s tests -p test_web_workers.py -v
```

Worker 整合測試包含兩位使用者、真實 Studio 程序、素材上傳、剪輯備份、真實 CPU MP4 匯出和 Range 下載，不執行 GPU 生成。

Chrome 的 HTTPS→HTTP 私有 IP 存取可能需要區域網路權限；公司政策或其他瀏覽器不支援時請提供可信 HTTPS API。參考 https://developer.chrome.com/blog/local-network-access。
