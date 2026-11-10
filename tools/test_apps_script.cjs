const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function fixture({status = 200, errors = 0, token = 'x'.repeat(32)} = {}) {
  const properties = {CRM_API_URL: 'https://crm.example.test', CRM_TRIGGER_TOKEN: token};
  const calls = [];
  const removed = [];
  const triggers = [
    {getHandlerFunction: () => 'processLeads'},
    {getHandlerFunction: () => 'anotherFunction'},
  ];
  let released = false;
  const context = vm.createContext({
    SpreadsheetApp: {getActiveSpreadsheet: () => null},
    PropertiesService: {getScriptProperties: () => ({getProperty: k => properties[k], setProperty: (k, v) => properties[k] = v})},
    LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => released = true})},
    UrlFetchApp: {fetch: (url, opts) => {calls.push({url, opts}); return {getResponseCode: () => status, getContentText: () => JSON.stringify({processed: 1, errors, status: 'ok', lead_count: 6, ai_calls: 0, telegram_calls: 0})};}},
    ScriptApp: {getProjectTriggers: () => triggers, deleteTrigger: t => removed.push(t)},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../apps-script/Code.gs'), 'utf8'), context);
  return {context, properties, calls, removed, released: () => released};
}

test('trigger sends authenticated POST without redirect and stores run summary', () => {
  const f = fixture();
  f.context.processLeads();
  assert.equal(f.calls[0].url, 'https://crm.example.test/process');
  assert.equal(f.calls[0].opts.method, 'post');
  assert.equal(f.calls[0].opts.headers['X-CRM-Token'], 'x'.repeat(32));
  assert.equal(f.calls[0].opts.followRedirects, false);
  assert.equal(f.properties.CRM_LAST_HTTP_STATUS, '200');
  assert.ok(f.released());
});

test('processing errors stop only CRM schedule', () => {
  const f = fixture({errors: 1});
  assert.throws(() => f.context.processLeads(), /Расписание остановлено/);
  assert.equal(f.removed.length, 1);
  assert.equal(f.removed[0].getHandlerFunction(), 'processLeads');
  assert.ok(f.released());
});

test('concurrent processing response does not cause retry or remove schedule', () => {
  const f = fixture({status: 409});
  f.context.processLeads();
  assert.equal(f.calls.length, 1);
  assert.equal(f.removed.length, 0);
});

test('unconfigured token prevents request', () => {
  const f = fixture({token: ''});
  assert.throws(() => f.context.processLeads(), /Настройте/);
  assert.equal(f.calls.length, 0);
  assert.ok(f.released());
});

test('connection check uses authenticated GET without starting processing', () => {
  const f = fixture();
  f.context.checkConnection();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url, 'https://crm.example.test/connection-check');
  assert.equal(f.calls[0].opts.method, 'get');
  assert.equal(f.calls[0].opts.followRedirects, false);
});

test('schedule is not installed while processing is disabled', () => {
  const f = fixture();
  assert.throws(() => f.context.installTrigger(), /расписание не создано/);
  assert.equal(f.removed.length, 0);
  assert.equal(f.calls.length, 1);
});

test('successful connection leaves a checkable receipt in Dashboard', () => {
  const f = fixture();
  const writes = [];
  f.context.SpreadsheetApp = {getActiveSpreadsheet: () => ({getSheetByName: title => {
    assert.equal(title, 'Dashboard');
    return {getRange: range => {assert.equal(range, 'J1:K5'); return {setValues: rows => writes.push(rows)};}};
  }})};
  f.context.checkConnection();
  assert.equal(writes.length, 1);
  assert.equal(writes[0][1][1], 'OK');
  assert.equal(writes[0][2][1], 6);
  assert.equal(writes[0][3][1], 0);
  assert.equal(writes[0][4][1], 0);
});
