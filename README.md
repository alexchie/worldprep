# 世界先修課 — 自動化紀錄片生產系統

> 先看懂世界，再出發。

每天自動完成：選題 → 研究 → 事實查核 → 腳本 → 分鏡 → 素材 → 旁白 → 剪輯 → 字幕 → 縮圖 → 標題/說明 → QA → **交付到 Google Drive**；每天 08:00（Asia/Taipei）寄信，附上可直接貼到 YouTube 的標題、說明、標籤與上傳檢查清單。YouTube 由你看過後自行上傳。

架構見 [ARCHITECTURE.md](ARCHITECTURE.md)，計畫見 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

## 運作方式（GitHub Actions，不需要開著電腦）

```
每天 22:00  daily-produce ：Drive 取回狀態 → 製作一集 → QA → 成品交付 Drive → 狀態寫回 Drive
每天 08:00  daily-email   ：寄通知信（標題、YouTube 說明、標籤、置頂留言、縮圖、Drive 連結、上傳檢查清單）
手動        regenerate    ：不滿意時重做 標題 / 縮圖 / 腳本 / 指定場景 / 整支影片，完成後下一封 08:00 信通知
```

**指定主題**：每天回覆通知信兩件事——明天的主題（從信中「主題選擇」選一個或自己寫）與後天的城市（企劃會在下一封信提供 3 個主題選項）。22:00 開工前系統會讀取回覆，依收到順序排隊製作。只接受 `EMAIL_TO` 信箱寄出、且通過 Gmail 寄件人驗證、主旨含「世界先修課」的信。沒有指定時依 `TOPIC_FALLBACK`：`auto` 從選題池自動選題，`skip` 當天不製作。

**模型分配**：見 `worldprep/providers/llm_claude.py` 的 `TASK_TIER`。研究用 Sonnet＋網搜、事實查核用 Opus（不同模型互相把關）、腳本與腳本審查用 Opus，其餘用 Sonnet / Haiku。研究與選題以外的步驟走 Batch API（半價，單步等待上限 `BATCH_WAIT_MINUTES`，逾時自動改即時呼叫）；腳本審查的事實清單與網搜續傳使用 prompt caching。

Drive 結構（`DRIVE_FOLDER_ID` 指定的資料夾內）：

```
世界先修課/EP.01_東京/   episode.mp4、thumbnail.jpg、thumbnail_candidates/、zh-Hant.srt、metadata.json、
                         qa_report.json、brief.json（本集企劃）、script.txt、研究與查核資料
_system/                 資料庫、工作檔（續跑/重做用，通知後保留 7 天）、授權配樂
```

## 一次性設定

1. **Google Cloud**：建立專案 → 啟用 **Google Drive API** → OAuth 同意畫面（External，發佈為 In production）→ 建立 OAuth client（Desktop app）→ 下載為 `secrets/client_secret.json`。
2. 本機：
   ```bash
   python -m venv .venv && .venv/Scripts/activate
   pip install -r requirements.txt
   python -m worldprep.cli google-auth      # 產生 secrets/google_token.json
   ```
3. **GitHub**：建立 repo 並 push；Settings → Secrets and variables → Actions：

| Secret | 內容 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude（研究、查核、腳本、分鏡、標題） |
| `GOOGLE_CLIENT_SECRET_JSON` | `secrets/client_secret.json` 全文 |
| `GOOGLE_TOKEN_JSON` | `secrets/google_token.json` 全文 |
| `DRIVE_FOLDER_ID` | 成品資料夾 ID（網址 `drive.google.com/drive/folders/<ID>`） |
| `SMTP_USER` / `SMTP_PASSWORD` | Gmail 帳號與 App Password |
| `EMAIL_TO` | 收信信箱 |
| `PIXABAY_API_KEY` | 建議：實景照片與 1080p 影片片段（Wikimedia 歷史圖像不需 key） |
| `GEMINI_API_KEY` | 建議：投影片畫面（Nano Banana 2.1，Batch 半價；需在 AI Studio 綁定付款） |
| `AZURE_SPEECH_KEY` | 選用：正式旁白（另設 Variable `VOICE_PROVIDER=azure`） |
| `OPENAI_API_KEY` | 選用：AI 重建畫面（另設 Variable `IMAGE_PROVIDER=openai`） |

Variables（選用）：`TOPIC_FALLBACK`、`VOICE_PROVIDER`、`IMAGE_PROVIDER`、`AZURE_SPEECH_REGION`、`TARGET_VIDEO_LENGTH_MINUTES`、`DAILY_BUDGET_USD`、`EPISODE_BUDGET_USD`。

4. 配樂（選用）：`python -m worldprep.cli upload-music music/xxx.mp3`，並在 `music/library.json` 登記授權（沒有登記授權的檔案不會被使用）：
   ```json
   [{"file": "xxx.mp3", "title": "...", "artist": "...", "license": "...", "license_url": "...", "attribution_required": false}]
   ```

缺少選用金鑰時自動降級：無 Azure → Edge TTS；無 Pixabay → 改用 Wikimedia 歷史圖像與品牌字卡。

注意：GitHub 排程在尖峰時段可能延遲數分鐘到數十分鐘；公開 repo 60 天沒有 commit 會停用排程，到 Actions 頁面重新啟用即可。

## 本機使用

```bash
python -m worldprep.cli run --mock                    # 離線 mock，不需任何金鑰；成品在 storage_mock/drive_mock/
python -m worldprep.cli run --destination 京都         # 本機真實製作一集
python -m worldprep.cli status
python -m worldprep.cli resume <episode_id>
python -m worldprep.cli regenerate <id> --target title --feedback "更有懸念"
python -m worldprep.cli schedule                      # 本機排程（電腦需開機）
pytest
```

## 目錄

```
worldprep/
  agents/      topic research factcheck script storyboard visual voice edit subtitles thumbnail metadata qa notify
  providers/   Claude、Azure/Edge TTS、Pexels、OpenAI Images、SMTP、mock
  render/      ffmpeg、logo、品牌圖卡
  drive.py     Google Drive 交付與狀態同步
  pipeline.py  狀態機與續跑
config/destinations.yaml   選題池
brand/                     logo 各版本
.github/workflows/         daily-produce、daily-email、regenerate、tests
```
