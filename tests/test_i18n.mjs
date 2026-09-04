import test from 'node:test';
import assert from 'node:assert/strict';
import { messages, translateText } from '../ytloadlib/static/i18n.mjs';

test('translations cover captions, errors and queue controls in both languages', () => {
  for (const [source, translations] of Object.entries(messages)) {
    for (const locale of ['de', 'ru']) {
      assert.ok(translations[locale === 'de' ? 0 : 1]?.trim(), source);
    }
  }
  assert.equal(translateText('Add to downloads', 'de'), 'Download hinzufügen');
  assert.equal(translateText('Add to downloads', 'ru'), 'Добавить в загрузки');
  assert.equal(translateText('No caption tracks were found for this video.', 'ru'), 'Для этого видео субтитры не найдены.');
});

test('dynamic download status and limits are localized', () => {
  assert.equal(translateText('2 links added', 'ru'), 'Добавлено ссылок: 2');
  assert.equal(translateText('5 links', 'ru'), '5 ссылок');
  assert.equal(translateText('1 link', 'de'), '1 Link');
  assert.equal(translateText('3 files saved to your folder', 'de'), '3 Dateien im Ordner gespeichert');
  assert.equal(translateText('Best available · original codecs', 'ru'), 'Лучшее доступное · исходные кодеки');
  assert.equal(translateText('Up to Full HD · 1080p · original codecs', 'de'), 'Bis zu Full HD · 1080p · Originalcodecs');
  assert.equal(translateText('Small file · up to 720p · MKV', 'ru'), 'Небольшой файл · до 720p · MKV');
  assert.equal(translateText('Retry Sample', 'de'), 'Erneut versuchen: Sample');
  assert.equal(translateText('Public limit exceeded', 'ru'), 'Превышен публичный лимит');
  assert.equal(translateText('Temporary file expired. Run the download again.', 'de'), 'Temporäre Datei abgelaufen. Starten Sie den Download erneut.');
  assert.equal(translateText('Open direct source', 'ru'), 'Открыть напрямую');
});

test('filenames, language codes and unknown content remain intact', () => {
  assert.equal(translateText('sample.en.vtt', 'de'), 'sample.en.vtt');
  assert.equal(translateText('en.*,ru-orig', 'ru'), 'en.*,ru-orig');
  assert.equal(translateText('Add to downloads', 'en'), 'Add to downloads');
});
