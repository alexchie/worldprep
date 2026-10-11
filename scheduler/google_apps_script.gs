/**
 * 世界先修課｜外部排程（Google Apps Script）
 * GitHub 內建排程常延遲或漏跑，這裡用 Google 的定時觸發去叫 GitHub Actions 開工。
 * GitHub 金鑰放在「專案設定 → 指令碼屬性」的 GITHUB_TOKEN，不要寫在程式碼裡。
 * 第一次使用：先執行 testConnection，再執行 install（建立定時觸發）。
 */
const REPO = 'alexchie/worldprep';
const TZ = 'Asia/Taipei';

function dispatch_(workflow) {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) throw new Error('請先在指令碼屬性設定 GITHUB_TOKEN');
  const res = UrlFetchApp.fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: 'post',
    contentType: 'application/json',
    headers: {Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'},
    payload: JSON.stringify({ref: 'main'}),
    muteHttpExceptions: true,
  });
  // 204 = 成功；失敗時拋出錯誤，Google 會寄失敗通知信給你
  if (res.getResponseCode() !== 204) throw new Error(`${workflow} 觸發失敗：${res.getResponseCode()} ${res.getContentText()}`);
  console.log(`${workflow} 已觸發`);
}

/** 每天早上寄信（同一天重複觸發不會重複寄） */
function triggerEmail() {
  dispatch_('daily-email.yml');
}

/** 每小時呼叫一次，只在台北 22:00–07:00 叫 GitHub 製作（當晚已開過新集數就只續跑未完成的） */
function triggerProduce() {
  const hour = Number(Utilities.formatDate(new Date(), TZ, 'H'));
  if (hour >= 22 || hour < 7) dispatch_('daily-produce.yml');
}

/** 確認金鑰可以讀到這個 repo 的 Actions */
function testConnection() {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  const res = UrlFetchApp.fetch(`https://api.github.com/repos/${REPO}/actions/workflows`, {
    headers: {Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json'},
    muteHttpExceptions: true,
  });
  const ok = res.getResponseCode() === 200;
  console.log(ok ? `連線成功，找到 ${JSON.parse(res.getContentText()).total_count} 個 workflow` : `失敗：${res.getResponseCode()} ${res.getContentText()}`);
}

/** 建立定時觸發：寄信 07:45 與 08:30（備援），製作每小時 */
function install() {
  ScriptApp.getProjectTriggers().forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('triggerEmail').timeBased().atHour(7).nearMinute(45).everyDays(1).inTimezone(TZ).create();
  ScriptApp.newTrigger('triggerEmail').timeBased().atHour(8).nearMinute(30).everyDays(1).inTimezone(TZ).create();
  ScriptApp.newTrigger('triggerProduce').timeBased().everyHours(1).create();
  console.log('已建立 3 個定時觸發');
}
