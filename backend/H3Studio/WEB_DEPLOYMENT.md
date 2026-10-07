# GitHub Pages 與公司內網部署

網頁 Repo: `yichonezhu/h3-studio-web`，Pages 網址發布後為 `https://yichonezhu.github.io/h3-studio-web/`。

主機先維持 H3 Studio、ComfyUI 與共享 Gateway（8190）運作，再執行專案根目錄 `start_h3_web_server.bat`。同事在公司內網瀏覽網頁，填入 `http://<主機內網 IP>:8795` 及自己原有的共享引擎金鑰。這個 8795 入口提供完整 Studio API；不要將網頁直接連到 8190 或 8188。

現有本機介面與作品不會搬動。Web 使用者新增資料在 `H3Studio/data/web_users/<Gateway使用者ID>/data/`，個人連線設定在該使用者自己的 `config.json`。工作程序只監聽 loopback，所有請求另需入口生成的內部隨機 token。

管理員 PowerShell 可執行 `configure_h3_web_firewall.ps1`，只對 Domain/Private 網路同子網路開放 TCP 8795。入口也拒絕公開 IP 來源。公司 Wi-Fi 的不同 VLAN 或端點防護可能需要 IT 調整指定來源規則。

預設網站 Origin 為 `https://yichonezhu.github.io`。本機預覽可額外指定 `--origin http://127.0.0.1:8876`；部署時不需保留測試 Origin。可信 HTTPS 憑證透過 `--cert` 與 `--key` 指定；不會自動停用憑證驗證。

金鑰輪替及停用沿用管理主機的「共享引擎」。入口每次請求讀取最新使用者設定，撤銷 session 和媒體票證立即生效。Web 一般使用者無權變更引擎網址、Gateway、模型或安裝程式。

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
