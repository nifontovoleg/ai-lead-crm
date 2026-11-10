/** Настройки CRM_API_URL и CRM_TRIGGER_TOKEN хранятся в Script Properties. */
function onOpen() {
  SpreadsheetApp.getUi().createMenu('CRM')
    .addItem('Настроить подключение', 'setupConnection')
    .addItem('Проверить связь без AI', 'checkConnection')
    .addItem('Обработать лиды', 'processLeads')
    .addItem('Включить каждые 5 минут', 'installTrigger')
    .addItem('Остановить расписание', 'removeTrigger')
    .addToUi();
}

function setupConnection() {
  const ui = SpreadsheetApp.getUi();
  const urlInput = ui.prompt('Адрес CRM', 'Вставьте HTTPS адрес из START-HTTPS.ps1', ui.ButtonSet.OK_CANCEL);
  if (urlInput.getSelectedButton() !== ui.Button.OK) return;
  const url = urlInput.getResponseText().trim().replace(/\/$/, '');
  if (!/^https:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(url)) throw new Error('Нужен HTTPS адрес без пути и параметров');
  const tokenInput = ui.prompt('Токен подключения', 'Вставьте CRM_TRIGGER_TOKEN из локального файла настроек подключения', ui.ButtonSet.OK_CANCEL);
  if (tokenInput.getSelectedButton() !== ui.Button.OK) return;
  const token = tokenInput.getResponseText().trim();
  if (token.length < 32) throw new Error('Токен должен содержать не менее 32 символов');
  PropertiesService.getScriptProperties().setProperties({CRM_API_URL: url, CRM_TRIGGER_TOKEN: token});
  checkConnection();
}

function checkConnection() {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('CRM_API_URL');
  const token = props.getProperty('CRM_TRIGGER_TOKEN');
  if (!url || !/^https:\/\//.test(url) || !token || token.length < 32) throw new Error('Сначала настройте подключение CRM');
  const response = UrlFetchApp.fetch(url.replace(/\/$/, '') + '/connection-check', {
    method: 'get', headers: {'X-CRM-Token': token}, muteHttpExceptions: true, followRedirects: false
  });
  if (response.getResponseCode() !== 200) throw new Error('Проверка связи: HTTP ' + response.getResponseCode());
  const result = JSON.parse(response.getContentText());
  props.setProperty('CRM_LAST_CONNECTION_CHECK', JSON.stringify(result));
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const dashboard = spreadsheet && spreadsheet.getSheetByName('Dashboard');
  if (dashboard) dashboard.getRange('J1:K5').setValues([
    ['Проверка Apps Script', new Date().toISOString()],
    ['Связь с Python', result.status === 'ok' ? 'OK' : 'Ошибка'],
    ['Лидов прочитано', result.lead_count],
    ['AI вызовов при проверке', result.ai_calls],
    ['Telegram при проверке', result.telegram_calls]
  ]);
  return result;
}

function processLeads() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    const props = PropertiesService.getScriptProperties();
    const url = props.getProperty('CRM_API_URL');
    const token = props.getProperty('CRM_TRIGGER_TOKEN');
    if (!url || !/^https:\/\//.test(url) || !token || token.length < 32) {
      throw new Error('Настройте HTTPS CRM_API_URL и CRM_TRIGGER_TOKEN в Script Properties');
    }
    const response = UrlFetchApp.fetch(url.replace(/\/$/, '') + '/process', {
      method: 'post', headers: {'X-CRM-Token': token},
      muteHttpExceptions: true, followRedirects: false
    });
    const status = response.getResponseCode();
    props.setProperty('CRM_LAST_RUN_UTC', new Date().toISOString());
    props.setProperty('CRM_LAST_HTTP_STATUS', String(status));
    if (status === 409) return;
    if (status !== 200) throw new Error('CRM HTTP ' + status);
    const result = JSON.parse(response.getContentText());
    props.setProperty('CRM_LAST_RESULT', JSON.stringify(result));
    if (result.errors > 0) {
      // Останавливаем расписание, чтобы не повторять неудачные платные пакеты.
      removeTrigger();
      throw new Error('Есть ошибки обработки. Расписание остановлено; проверьте лист Лиды');
    }
  } finally {
    lock.releaseLock();
  }
}

function installTrigger() {
  const connection = checkConnection();
  if (!connection.processing_enabled) throw new Error('Обработка на сервере выключена. Сначала включите её локально; расписание не создано.');
  removeTrigger();
  ScriptApp.newTrigger('processLeads').timeBased().everyMinutes(5).create();
}

function removeTrigger() {
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'processLeads')
    .forEach(t => ScriptApp.deleteTrigger(t));
}
