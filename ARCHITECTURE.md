# 世界先修課 — 自動化紀錄片生產系統架構

> 先看懂世界，再出發。

## 1. 環境盤點（2026-10-04）

| 項目 | 狀態 | 對策 |
|---|---|---|
| OS | Windows 11 Pro | 全部以 Python 跨平台實作，路徑一律用 `pathlib` |
| Python | 3.11.2 | 主語言 |
| Node | 24.15 | 不使用（見技術選型） |
| FFmpeg | 未安裝 | 使用 `imageio-ffmpeg` 內建的 ffmpeg 執行檔；可用 `FFMPEG_PATH` 指向系統版 |
| Docker / PostgreSQL / Redis | 未安裝 | MVP 用 SQLite + DB 任務表；`DATABASE_URL` 換成 Postgres 即可上線 |
| 字型 | Noto Sans TC、微軟正黑體 已安裝 | 縮圖 / 字幕 / 片頭使用 |
| API 金鑰 | 僅 `ANTHROPIC_API_KEY` | 其他金鑰列於 `.env.example`，缺少時自動退回替代 provider |
| 既有程式碼 | 無（僅 `Automation.txt`） | 從零建立 |

## 2. 技術選型與理由

| 層 | 選擇 | 理由 |
|---|---|---|
| 後端 | Python 3.11+ CLI | FFmpeg / Pillow / matplotlib / Google API 生態完整 |
| LLM | Claude（`claude-opus-5-5`）+ server-side `web_search` | 研究、查核、腳本皆需深度推理；web search 由 Anthropic 端執行，免自建爬蟲 |
| 資料庫 | SQLAlchemy 2.0；SQLite（開發）/ PostgreSQL（正式） | 同一份 ORM，零修改切換 |
| 佇列 / 重試 | DB 內 `production_jobs` 表 + `tenacity` 指數退避 | 每日一集的吞吐量不需要 Redis/Celery；狀態全在 DB，可從上次成功的階段續跑。量變大再接 Celery，介面不變 |
| 排程 / 執行環境 | GitHub Actions（`schedule` + `timezone: Asia/Taipei`）；本機備案為 APScheduler | 不需要常開的主機；明確時區，不依賴伺服器時區 |
| 儲存 | 執行中用本機工作目錄；成品與狀態存 Google Drive | 目錄分 `raw/research/scripts/audio/assets/video/thumbnails/subtitles/final`，以 episode id 分組；GitHub 每次執行前後與 Drive 同步 |
| 渲染 | FFmpeg（Ken Burns zoompan + concat + 混音 + 字幕燒錄） | 穩定、免費、可重現 |
| 語音 | `VoiceProvider`：Azure Speech（正式）/ Edge TTS（開發）/ Mock | Azure 有官方 zh-TW 神經語音與 SSML；Edge TTS 僅供開發測試 |
| 圖像 | `ImageProvider`：Pexels（授權素材）/ OpenAI Images（AI 生成）/ 自製卡片圖表 | 不從 Google 圖片抓圖；所有素材入 `assets` 表記錄授權 |
| Email | `EmailProvider`：SMTP（Gmail App Password） | 每天一封，最少依賴 |
| YouTube | 不串接：由頻道主看過成品後手動上傳 | 通知信提供可直接貼上的標題、說明、標籤與檢查清單 |

## 3. 系統總覽

```
GitHub Actions（Asia/Taipei）
  18:00 daily-produce ─ ci-pull（Drive→本機）→ pipeline → deliver（Drive）→ ci-push（本機→Drive）
  08:00 daily-email   ─ ci-pull → 通知信（標題/說明/標籤/縮圖/Drive 連結/上傳清單）→ ci-push
  手動  regenerate    ─ ci-pull → 重做指定部分 → 重新交付 → ci-push
三個 workflow 共用 concurrency group，確保同一時間只有一個執行者讀寫狀態。

pipeline：Topic → Research → FactCheck → Script → Storyboard → Visual → Voice → Edit/Render
          → Subtitles → Thumbnail → Metadata → QA → Deliver
```

所有 agent 透過 `providers/` 內的介面呼叫外部服務，不直接相依特定廠商。

## 4. 生產流程與狀態機

```
IDEA → RESEARCHING → RESEARCH_COMPLETE → FACT_CHECKING → FACT_CHECK_COMPLETE
→ SCRIPTING → SCRIPT_COMPLETE → STORYBOARDING → ASSET_GENERATION → VOICE_GENERATION
→ RENDERING → QA → READY_FOR_DELIVERY → DELIVERED → NOTIFIED
DELIVERED / NOTIFIED → REGENERATING → （回到 腳本 / 素材 / 渲染）→ … → DELIVERED
任何階段 → FAILED（保留已完成產物，下次執行自動續跑）
```

- 每次轉換寫入 `state_transitions`；非法轉換直接拋錯。
- 每個階段是一個 `production_jobs` 紀錄（attempt、started/completed、error）。
- 產物以檔案存放，階段開始時若產物已存在則跳過（冪等）。素材層級亦同：第 17 張圖失敗只重試第 17 張。
- 每個階段開始前檢查單集預算，超過即停止並標記人工確認。

## 5. 內容策略落實

| 編輯原則 | 實作位置 |
|---|---|
| 歷史 → 城市 → 商業 → 文化 → 景點 為一條因果鏈 | `agents/script.py` 系統提示 + JSON schema 要求每段標記 `section` 與 `causal_link`；QA 檢查順序與連貫性 |
| 不以「大家好」開場、Hook 0:30 內 | Script schema 的 `hook` 段 + QA 規則檢查禁用開場 |
| 片長依實際語速 | `NARRATION_CHARS_PER_MINUTE` 初值 260，每集 TTS 後以實測語速寫回 `calibration.json` |
| 標題「｜世界先修課 EP.xx」 | `brand.py` 格式化與驗證；Title agent 產 ≥10 候選 + 10 維度評分 |
| 事實查核 | 研究輸出 claim/source/url/date/confidence；Fact check 以獨立 web search 複核，未通過者移除/改寫/標記人工審核 |
| 素材授權 | `assets` 表：source、creator、license、license_url、usage_rights、attribution_required |
| AI 內容揭露 | 若使用「寫實」AI 生成畫面 → 說明欄註記，通知信的上傳清單提示勾選「變造或合成內容」 |

## 6. Google Drive 與 GitHub Actions

- GitHub runner 每次都是全新機器：開工時從 Drive `_system/` 下載 SQLite 資料庫、配樂、未完成集數的工作檔（tar.gz），收工時（含失敗）寫回。
- 資料庫在任何連線建立前下載；上傳前執行 WAL checkpoint，避免檔案不一致。
- 可重建的場景片段（`video/s*.mp4`）不打包，縮小同步量；通知 7 天後刪除該集工作檔，成品保留在交付資料夾。
- OAuth 範圍：`https://www.googleapis.com/auth/drive`。因為成品要寫入使用者指定、非本程式建立的資料夾，`drive.file` 範圍無法存取。
- 公開 repo 不放授權音樂與金鑰：音樂放 Drive，金鑰放 GitHub Secrets。

## 7. 通知信

- 每天 08:00 寄出所有已交付、尚未通知的集數；沒有新集數時寄狀態信（含最後錯誤），確保你知道系統仍在運作。
- 內容：YouTube 標題、說明（含章節、資料來源、AI 揭露）、標籤、置頂留言、縮圖、Drive 連結、QA 警告、上傳檢查清單、重做方式。
- 製作失敗時另寄告警信。

## 8. 可觀測性與成本

- 結構化 JSON log（`logs/pipeline.jsonl`）：階段、API 請求、重試、渲染時間、上傳與 email 狀態。
- `costs` 表：每次 LLM 呼叫依 usage token × 單價、TTS 依字數、圖片依張數記錄。
- `DAILY_BUDGET_USD` / `EPISODE_BUDGET_USD`：超過時停止非必要生成（AI 圖改用卡片 / 素材）、並標記人工審核，不默默超支。
- 成本、狀態與錯誤記錄在 `costs`、`production_jobs`、`state_transitions` 表與 GitHub Actions 的 logs artifact；製作失敗即時寄告警信。

## 9. 最高風險元件

1. **視覺品質**：目前以照片＋推移鏡頭為主；建議加入 Pexels 影片、Wikimedia Commons 歷史圖像、Mapbox 地圖。
2. **事實正確性**：LLM 幻覺風險；以雙階段查核 + 低信心 claim 不進入腳本 + 圖表數字比對緩解。
3. **語音自然度**：Edge TTS 只適合開發，且可能被資料中心 IP 擋；正式應採 Azure zh-TW。
4. **GitHub 排程**：尖峰可能延遲或極少數被略過；單一 job 上限 6 小時，超過會由下次執行續跑。
5. **Google refresh token**：OAuth 同意畫面須為 In production，否則 7 天失效。
