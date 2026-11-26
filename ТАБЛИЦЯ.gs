/**
 * CRM у Google Таблиці для бота записів.
 *
 * Бот надсилає сюди кожну подію: новий запис, оплату, скасування.
 * Скрипт сам створює аркуш «Записи» і заголовки, а далі оновлює
 * потрібний рядок за номером запису — дублів не буде.
 *
 * Як поставити:
 *   1. Відкрийте таблицю → Розширення → Apps Script.
 *   2. Видаліть усе, що там є, і вставте цей файл.
 *   3. Впишіть свій SECRET нижче — будь-який рядок, який знаєте тільки ви.
 *   4. Розгорнути → Новий розгортання → тип «Веб-додаток»:
 *        Виконувати як: Я
 *        Хто має доступ: Будь-хто
 *   5. Скопіюйте URL і впишіть його боту в config.json:
 *        "sheet_webhook": "сюди URL",
 *        "sheet_secret":  "сюди той самий SECRET"
 */

var SECRET = 'ЗАМІНІТЬ_НА_СВІЙ_КЛЮЧ';
var VERSION = 'ТАБЛИЦЯ v4 — вартість, передоплата, до оплати';
var SHEET = 'Записи';

var COLUMNS = [
  ['id',       'Запис №'],
  ['created',  'Створено'],
  ['status',   'Статус'],
  ['service',  'Послуга'],
  ['master',   'Майстер'],
  ['when',     'Коли'],
  ['name',     "Ім'я"],
  ['phone',    'Телефон'],
  ['username', 'Telegram'],
  ['price',    'Вартість послуги'],
  ['discount', 'Знижка'],
  ['deposit',  'Передоплата'],
  ['due',      'До оплати'],
  ['paid',     'Оплачено'],
  ['paid_at',  'Коли оплачено'],
  ['comment',  'Коментар'],
  ['event',    'Остання подія']
];

/**
 * Якщо колись захочете дату й час окремими стовпцями — бот шле і такі
 * поля теж. Досить замінити рядок created на два:
 *   ['created_date', 'Створено'], ['created_time', 'Час створення'],
 * так само when -> slot_date + slot_time, paid_at -> paid_date + paid_time.
 * Шапка підлаштується сама при наступному записі.
 */

function doGet() {
  // Відкрийте адресу розгортання у браузері — побачите, яка версія
  // скрипта працює насправді. Якщо браузер пише «Script function not
  // found: doGet», значить розгорнута ще стара версія.
  return ContentService.createTextOutput(VERSION);
}

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    if (SECRET && data.secret !== SECRET) {
      return reply({ok: false, error: 'wrong secret'});
    }
    writeRow(data);
    return reply({ok: true});
  } catch (err) {
    return reply({ok: false, error: String(err)});
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function sheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(SHEET) || ss.insertSheet(SHEET);
  var head = COLUMNS.map(function (c) { return c[1]; });

  var current = sh.getLastRow() > 0
    ? sh.getRange(1, 1, 1, Math.max(sh.getLastColumn(), head.length)).getValues()[0]
    : [];
  var same = current.length >= head.length;
  for (var i = 0; same && i < head.length; i++) {
    if (String(current[i]) !== head[i]) same = false;
  }
  if (same) return sh;

  // Набір стовпців змінився (або аркуш новий) — переписуємо шапку.
  sh.getRange(1, 1, 1, head.length).setValues([head]);
  var r = sh.getRange(1, 1, 1, head.length);
  r.setFontWeight('bold').setBackground('#1f2937').setFontColor('#ffffff');
  r.setFontFamily('Montserrat').setVerticalAlignment('middle');
  sh.setFrozenRows(1);
  sh.getRange(2, 1, sh.getMaxRows() - 1, head.length).setFontFamily('Nunito');
  sh.autoResizeColumns(1, head.length);
  return sh;
}

function writeRow(data) {
  var sh = sheet();
  var values = COLUMNS.map(function (c) {
    var v = data[c[0]];
    if (c[0] === 'paid') return v ? 'так' : 'ні';
    return v === undefined || v === null ? '' : v;
  });

  var ids = sh.getLastRow() > 1
    ? sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues()
    : [];
  var line = 0;
  for (var i = 0; i < ids.length; i++) {
    if (String(ids[i][0]) === String(data.id)) { line = i + 2; break; }
  }
  if (!line) {
    sh.appendRow(values);
    line = sh.getLastRow();
  } else {
    sh.getRange(line, 1, 1, values.length).setValues([values]);
  }
  paint(sh, line, data);
}

function paint(sh, line, data) {
  var colour = '#ffffff';
  if (data.status === 'cancelled') colour = '#fee2e2';
  else if (data.paid) colour = '#dcfce7';
  else if (Number(data.deposit) > 0) colour = '#fef9c3';
  sh.getRange(line, 1, 1, COLUMNS.length).setBackground(colour);
}
