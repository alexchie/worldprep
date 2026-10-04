# 實作計畫

## MVP 邊界

第一個可用版本必須能：**從選題一路做到成品交付 Google Drive，並於 08:00 寄出含 YouTube 標題與說明的通知信**，完整一集。YouTube 由頻道主手動上傳。

MVP 內：
- 選題、研究（web search）、事實查核、腳本、分鏡
- 素材：Pexels 授權素材 + 自製標題卡/數據圖/地圖卡 + 選用 AI 圖（OpenAI Images）
- 語音（Azure / Edge TTS）、FFmpeg 渲染、zh-TW 字幕（SRT，燒錄 + 上傳）
- 品牌：Logo 系統、片頭 3 秒、EP 編號、標題產生器、縮圖產生器
- QA 閘門、Google Drive 交付
- 08:00 通知信（標題、說明、標籤、上傳清單）
- GitHub Actions 排程、重試、續跑、狀態機、成本追蹤

MVP 外（Phase 6）：英文字幕、真實地理底圖（Natural Earth）、AI 影片片段、標題/縮圖 A/B、YouTube Analytics 回饋選題、Celery 分散式佇列。

## 階段與狀態

| Phase | 內容 | 狀態 |
|---|---|---|
| 1 Core pipeline | topic / research / fact check / script / storyboard / visuals / voice / render / subtitles | 已實作 |
| 2 Branding | logo、slogan、title agent、thumbnail、EP、片頭、頻道說明 | 已實作 |
| 3 交付 | Google Drive 交付與狀態同步 | 已實作（需 OAuth 憑證實測） |
| 4 通知 | 08:00 通知信、失敗告警、手動重做 workflow | 已實作 |
| 5 Automation | 排程、重試、續跑、狀態機、監控 | 已實作 |
| 6 Optimization | 成本追蹤（已做）、provider fallback（已做基本）、品質評分、analytics、A/B | 部分 |

## 上線步驟

見 README「一次性設定」。

## 驗證方式

- `pytest`：標題格式、狀態機、Drive 同步、mock 全流程（交付 + 通知信 + 重做）。
- `run --mock`：無需任何外部金鑰，產出完整 mp4 / srt / 縮圖，用以檢查剪輯與品牌。
- 真實一集：檢查 Drive 交付資料夾、通知信、GitHub Actions logs artifact、`costs` 表。
