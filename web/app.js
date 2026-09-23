import {ApiClientError, buildRecommendationRequest, createApiClient, createLatestRecommender} from '../integration/api-client.mjs';

// Public deployment and run_web.py serve the API on the same origin.
const baseUrl = globalThis.EVENT_API_BASE_URL ?? globalThis.location.origin;
const api = createApiClient({baseUrl});
const latest = createLatestRecommender(api);
const form = document.getElementById('event-form');
const fields = Object.fromEntries(['city','date','event_format','category','budget_kzt','duration_hours','language','brief'].map(name => [name, form.elements.namedItem(name)]));
const mainPanel = document.getElementById('panel-main');
const alternativesPanel = document.getElementById('panel-alternatives');
const submitButton = document.getElementById('submit-button');
const optionsStatus = document.getElementById('options-status');
const money = value => `${new Intl.NumberFormat('ru-RU').format(value)} ₸`;
let options;
let response;
let requestSerial = 0;

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = String(content);
  return node;
}

function withRequestId(message, error) {
  return error instanceof ApiClientError && error.requestId
    ? `${message} Код обращения: ${error.requestId}` : message;
}

function setOptions(select, values, placeholder) {
  select.replaceChildren(new Option(placeholder, ''));
  for (const value of values) select.add(new Option(value, value));
}

function setFormEnabled(enabled) {
  for (const field of Object.values(fields)) field.disabled = !enabled;
  submitButton.disabled = !enabled;
}

function setOptionsStatus(message, type = '') {
  optionsStatus.className = `options-status ${type}`.trim();
  optionsStatus.replaceChildren(element('span', '', message));
  if (type === 'error') {
    const retry = element('button', 'retry-button', 'Повторить');
    retry.type = 'button';
    retry.addEventListener('click', loadOptions);
    optionsStatus.append(' ', retry);
  }
}

function populateCategory(selected = '') {
  const city = fields.city.value;
  const categories = options?.categories_by_city?.[city] ?? [];
  setOptions(fields.category, categories, city ? 'Выберите категорию' : 'Сначала выберите город');
  if (categories.includes(selected)) fields.category.value = selected;
}

async function loadOptions() {
  setFormEnabled(false);
  setOptionsStatus('Загружаем справочник…');
  try {
    options = await api.catalogueOptions();
    setOptions(fields.city, options.cities, 'Выберите город');
    setOptions(fields.event_format, options.event_formats, 'Выберите формат');
    setOptions(fields.language, options.languages, 'Любой');
    fields.date.min = options.calendar_start;
    fields.date.max = options.calendar_end;
    document.getElementById('date-hint').textContent = `По календарю каталога: ${displayDate(options.calendar_start)} — ${displayDate(options.calendar_end)}.`;
    populateCategory();
    setFormEnabled(true);
    setOptionsStatus('', 'ready');
    updateTicket();
  } catch (error) {
    setOptionsStatus(error instanceof ApiClientError ? withRequestId(error.message, error) : 'Не удалось загрузить справочник.', 'error');
  }
}

function displayDate(iso) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(iso))) return String(iso ?? '');
  const [year, month, day] = iso.split('-');
  return `${day}.${month}.${year}`;
}

function updateTicket() {
  const date = fields.date.value;
  document.getElementById('ticket-date').textContent = date ? `${date.slice(8,10)}.${date.slice(5,7)}` : '—';
  document.getElementById('ticket-year').textContent = date ? date.slice(0,4) : '2026';
  document.getElementById('ticket-city').textContent = fields.city.value || 'Выберите город';
  document.getElementById('ticket-format').textContent = fields.event_format.value || 'Формат события';
}

function clearErrors() {
  for (const name of Object.keys(fields)) {
    document.getElementById(`error-${name}`).textContent = '';
    fields[name].removeAttribute('aria-invalid');
  }
}

function showFieldErrors(details) {
  let first;
  for (const item of details ?? []) {
    const field = fields[item.field];
    if (!field) continue;
    document.getElementById(`error-${item.field}`).textContent = item.message;
    field.setAttribute('aria-invalid', 'true');
    first ??= field;
  }
  first?.focus();
}

function valuesFromForm() {
  return Object.fromEntries(Object.entries(fields).map(([name, field]) => [name, field.value]));
}

function validateForm() {
  const issues = [];
  for (const name of ['city','date','event_format','category','budget_kzt']) {
    if (!fields[name].value) issues.push({field: name, message: 'Заполните это поле.'});
  }
  if (fields.date.value && !fields.date.checkValidity()) issues.push({field: 'date', message: 'Выберите дату в пределах календаря каталога.'});
  if (fields.brief.value.length > 500) issues.push({field: 'brief', message: 'Не более 500 символов.'});
  if (issues.length) {
    showFieldErrors(issues);
    return null;
  }
  try { return buildRecommendationRequest(valuesFromForm()); }
  catch (error) {
    showFieldErrors(error.details);
    return null;
  }
}

function switchTab(which) {
  const main = which === 'main';
  document.getElementById('tab-main').classList.toggle('active', main);
  document.getElementById('tab-main').setAttribute('aria-selected', String(main));
  document.getElementById('tab-alternatives').classList.toggle('active', !main);
  document.getElementById('tab-alternatives').setAttribute('aria-selected', String(!main));
  mainPanel.hidden = !main;
  alternativesPanel.hidden = main;
}

function notice(text, kind = '') {
  return element('div', `message-strip ${kind}`.trim(), text);
}

function waiting(title, message, icon = '✳') {
  const box = element('div', 'waiting-state');
  box.append(element('div', 'waiting-icon', icon), element('h4', '', title), element('p', '', message));
  return box;
}

function renderLoading() {
  mainPanel.replaceChildren(waiting('Ищем подходящих людей', 'Проверяем условия, календарь и факты каталога…', '↗'));
  alternativesPanel.replaceChildren(waiting('Проверяем запасные пути', 'Посмотрим, есть ли подтверждённые варианты при изменении условий.', '✳'));
  document.getElementById('results-title').textContent = 'Подбираем варианты';
  document.getElementById('results-subtitle').textContent = 'Один момент — ответ приходит из каталога проекта.';
  document.getElementById('results-count').textContent = 'Подбор идёт';
  document.getElementById('main-tab-count').textContent = '';
  document.getElementById('alternatives-tab-count').textContent = '';
}

function renderFailure(error) {
  let message = error?.message || 'Не удалось получить ответ. Попробуйте ещё раз.';
  if (error?.kind === 'network') message = 'Нет связи с API. Убедитесь, что сервер проекта запущен, и повторите запрос.';
  if (error?.kind === 'timeout') message = 'Сервер отвечает слишком долго. Попробуйте ещё раз.';
  message = withRequestId(message, error);
  document.getElementById('results-title').textContent = 'Подбор не завершён';
  document.getElementById('results-subtitle').textContent = 'Ваши данные остались в форме.';
  document.getElementById('results-count').textContent = 'Повторите запрос';
  mainPanel.replaceChildren(notice(message, 'error'));
  alternativesPanel.replaceChildren(waiting('Пока нет запасных вариантов', 'Они появятся после успешного ответа сервера.'));
  if (error?.status === 422) showFieldErrors(error.details);
  switchTab('main');
}

async function submitRequest(request) {
  const current = ++requestSerial;
  response = undefined;
  clearErrors();
  submitButton.disabled = true;
  submitButton.firstElementChild.textContent = 'Подбираем…';
  switchTab('main');
  renderLoading();
  try {
    const result = await latest.recommend(request);
    if (current !== requestSerial || result === null) return;
    response = result;
    renderResponse(result);
  } catch (error) {
    if (current === requestSerial) renderFailure(error);
  } finally {
    if (current === requestSerial) {
      submitButton.disabled = false;
      submitButton.firstElementChild.textContent = 'Найти подходящих';
    }
  }
}

function list(items, className) {
  const ul = element('ul', className);
  for (const item of items) ul.append(element('li', '', item));
  return ul;
}

function createCard(card, index) {
  const article = element('article', 'contractor-card');
  const head = element('div', 'card-head');
  const avatar = element('div', 'card-avatar', card.name.slice(0,1).toLocaleUpperCase('ru'));
  const identity = element('div');
  identity.append(element('h4', '', card.name), element('div', 'card-meta', `${card.category} · ${card.city}`));
  const price = element('div', 'card-price');
  price.append(element('span', '', 'Стартовая цена'), element('strong', '', `от ${money(card.price_from_kzt)}`));
  head.append(avatar, identity, price);
  article.append(head);

  const flags = [];
  if (card.synthetic) flags.push('Синтетический профиль');
  if (card.city_imputed) flags.push('Город восстановлен в данных');
  if (card.price_imputed) flags.push('Цена оценочная');
  if (flags.length) {
    const flagBox = element('div', 'flags-list');
    for (const flag of flags) flagBox.append(element('span', '', flag));
    article.append(flagBox);
  }

  const details = element('details', 'card-details');
  details.append(element('summary', '', 'Подробнее'));
  const description = element('section', 'detail-section');
  description.append(element('h5', '', 'Описание'), element('p', '', card.evidence_quote ? `Фрагмент описания: «${card.evidence_quote}»` : 'Описание в каталоге не указано.'));
  if (card.evidence_note) description.append(element('p', 'detail-note', card.evidence_note));
  const fit = element('section', 'detail-section');
  fit.append(element('h5', '', 'Почему подходит именно вам'));
  if (card.explanation) fit.append(element('p', '', card.explanation));
  else if (card.why_fits?.length) fit.append(list(card.why_fits, 'detail-list'));
  else fit.append(element('p', '', 'Дополнительного объяснения нет.'));
  const clarify = element('section', 'detail-section');
  clarify.append(element('h5', '', 'Уточнить у подрядчика'));
  clarify.append(card.to_clarify?.length ? list(card.to_clarify, 'detail-list') : element('p', '', 'Дополнительные вопросы не указаны.'));
  const differences = element('section', 'detail-section');
  differences.append(element('h5', '', 'Отличия среди показанных'));
  differences.append(card.differences?.length ? list(card.differences, 'detail-list') : element('p', '', 'Сравнение с другими показанными подрядчиками недоступно.'));
  details.append(description, fit, clarify, differences);
  article.append(details);
  article.dataset.cardIndex = String(index);
  return article;
}

function renderUnderstanding(understanding) {
  const styles = understanding?.styles ?? [];
  const notes = understanding?.notes ?? [];
  const unwanted = understanding?.unwanted ?? [];
  const language = understanding?.effective_language;
  if (!styles.length && !notes.length && !unwanted.length && !language) return null;
  const box = element('div', 'understanding');
  box.append(element('strong', '', 'Как мы поняли пожелания'));
  const parts = [];
  if (styles.length) parts.push(`Стиль: ${styles.join(', ')}.`);
  if (unwanted.length) parts.push(`Нежелательно: ${unwanted.join(', ')}.`);
  if (language) parts.push(`Язык: ${language}${understanding.language_source === 'brief' ? ' — из текста пожеланий' : ''}.`);
  if (parts.length) box.append(element('p', '', parts.join(' ')));
  if (notes.length) box.append(list(notes, 'understanding-notes'));
  return box;
}

function renderMain(result) {
  mainPanel.replaceChildren();
  if (result.cards.length) {
    const cards = element('div', 'cards-list');
    result.cards.forEach((card, index) => cards.append(createCard(card, index)));
    mainPanel.append(cards);
  } else {
    mainPanel.append(waiting(result.status === 'category_absent' ? 'В этом городе нет такой категории' : 'Пока никто не подходит',
      result.status === 'category_absent' ? 'Выберите другой город или категорию.' : 'Откройте запасные варианты: сервер проверил ближайшие изменения условий.'));
  }
  const understanding = renderUnderstanding(result.understanding);
  if (understanding) mainPanel.append(understanding);
  mainPanel.append(renderSelectionSummary(result));
}

const reasonLabels = {
  busy: 'Заняты на дату',
  over_budget: 'Стартовая цена выше бюджета',
  wrong_format: 'Не работают с указанным форматом',
  wrong_language: 'Не работают на указанном языке',
  too_short: 'Не могут работать столько часов',
};

function renderSelectionSummary(result) {
  const summary = element('section', 'selection-summary');
  summary.setAttribute('aria-label', 'Итоги подбора');
  summary.append(element('h4', '', 'Итоги подбора'));
  const items = [];
  if (result.status === 'category_absent') {
    items.push(result.message);
  } else {
    items.push(`Подобрано подрядчиков: ${result.cards.length}.`);
    if (result.reasons?.busy) items.push(`Заняты на выбранную дату: ${result.reasons.busy}.`);
    if (result.cards.length < 3) {
      items.push(`Меньше трёх: из ${result.counts.category_total} профилей категории подходят ${result.counts.eligible_total}.`);
    } else if (result.counts.eligible_total > 3) {
      items.push(`Всего подходят ${result.counts.eligible_total}; показаны первые три по ранжированию.`);
    }
  }
  const reasons = Object.entries(reasonLabels)
    .filter(([key]) => key !== 'busy' && result.reasons?.[key])
    .map(([key, label]) => `${label}: ${result.reasons[key]}.`);
  const summaryList = list(items, 'selection-summary-list');
  if (reasons.length) {
    const reasonItem = element('li', '', 'Не подошли по другим условиям:');
    reasonItem.append(list(reasons, 'selection-reasons'));
    summaryList.append(reasonItem);
  }
  summary.append(summaryList);
  if (Object.values(result.reasons ?? {}).some(Boolean)) {
    summary.append(element('p', 'selection-summary-note', 'Причины могут пересекаться.'));
  }
  return summary;
}

const fieldNames = {date:'Дата',budget_kzt:'Бюджет',duration_hours:'Длительность'};
function changeValue(change, value) {
  if (change.field === 'date') return displayDate(value);
  if (change.field === 'budget_kzt') return money(value);
  if (change.field === 'duration_hours') return `${value} ч`;
  return String(value);
}

function alternativeCard(alternative, index) {
  const card = element('article', 'alternative-card');
  const head = element('div', 'alternative-head');
  head.append(element('span', 'alternative-kicker', `Вариант ${index + 1}`), element('span', 'candidate-count', `${alternative.eligible_count} подходят`));
  card.append(head, element('h4', '', alternative.changes.map(change => fieldNames[change.field] ?? change.field).join(' + ')));
  const changes = element('div', 'change-list');
  for (const change of alternative.changes) {
    changes.append(element('span', 'change-chip', `${fieldNames[change.field] ?? change.field}: ${changeValue(change, change.from_value)} → ${changeValue(change, change.to_value)}`));
  }
  card.append(changes, element('p', '', alternative.message));
  const apply = element('button', 'apply-button', 'Применить этот вариант ↗');
  apply.type = 'button';
  apply.addEventListener('click', () => {
    fillForm(alternative.request);
    submitRequest(alternative.request);
    document.getElementById('selection').scrollIntoView({behavior:'smooth',block:'start'});
  });
  card.append(apply);
  return card;
}

function renderAlternatives(result) {
  alternativesPanel.replaceChildren();
  alternativesPanel.append(element('p', 'alternatives-intro', 'Здесь только проверенные сервером изменения исходного запроса. Условия меняются лишь после вашего выбора.'));
  if (result.alternatives?.length) {
    const list = element('div', 'alternative-list');
    result.alternatives.forEach((item, index) => list.append(alternativeCard(item, index)));
    alternativesPanel.append(list);
  } else {
    alternativesPanel.append(notice(result.status === 'matched'
      ? 'По текущим условиям уже достаточно подходящих кандидатов либо ближайшее изменение не добавляет новых.'
      : 'Проверенных соседних вариантов для этих условий нет.', 'info'));
  }
  if (result.alternatives_note) alternativesPanel.append(element('p', 'alternative-note', result.alternatives_note));

  const withNuances = result.cards.filter(card => card.to_clarify?.length || card.differences?.length);
  if (withNuances.length || fields.brief.value.trim()) {
    const section = element('section', 'preference-section');
    section.append(element('h4', '', 'Нюансы ваших пожеланий'), element('p', '', 'Это пояснения к уже показанным кандидатам, а не дополнительные проверенные подрядчики.'));
    for (const card of withNuances) {
      const row = element('div', 'preference-item');
      const body = element('div');
      body.append(element('strong', '', card.name), element('p', '', card.to_clarify?.[1] ?? card.to_clarify?.[0] ?? card.differences?.[0]));
      row.append(body);
      section.append(row);
    }
    const edit = element('button', 'edit-wishes-button', 'Изменить пожелания ↗');
    edit.type = 'button';
    edit.addEventListener('click', () => {fields.brief.focus();fields.brief.scrollIntoView({behavior:'smooth',block:'center'});});
    section.append(edit);
    alternativesPanel.append(section);
  }
}

function renderResponse(result) {
  document.getElementById('results-title').textContent = result.status === 'matched' ? 'Люди для вашего события' : 'Результат подбора';
  document.getElementById('results-subtitle').textContent = result.status === 'matched'
    ? 'Причины выбора и точные фрагменты описаний — в каждой карточке.'
    : 'Измените запрос или посмотрите проверенные запасные пути.';
  document.getElementById('results-count').textContent = result.status === 'matched'
    ? `${result.cards.length} из ${result.counts?.eligible_total ?? result.cards.length} подходящих`
    : 'Нет совпадений';
  document.getElementById('main-tab-count').textContent = String(result.cards.length);
  document.getElementById('alternatives-tab-count').textContent = result.alternatives?.length ? String(result.alternatives.length) : '';
  renderMain(result);
  renderAlternatives(result);
}

function fillForm(request) {
  for (const [name, field] of Object.entries(fields)) {
    if (name === 'category') continue;
    field.value = request[name] ?? '';
  }
  populateCategory(request.category);
  document.getElementById('brief-count').textContent = `${fields.brief.value.length} / 500`;
  updateTicket();
}

form.addEventListener('submit', event => {
  event.preventDefault();
  clearErrors();
  const request = validateForm();
  if (request) submitRequest(request);
});
fields.city.addEventListener('change', () => {populateCategory();updateTicket();});
fields.date.addEventListener('input', updateTicket);
fields.event_format.addEventListener('change', updateTicket);
fields.brief.addEventListener('input', () => {document.getElementById('brief-count').textContent = `${fields.brief.value.length} / 500`;});
for (const field of Object.values(fields)) field.addEventListener('input', () => {
  const error = document.getElementById(`error-${field.name}`);
  if (error) error.textContent = '';
  field.removeAttribute('aria-invalid');
});
document.getElementById('tab-main').addEventListener('click', () => switchTab('main'));
document.getElementById('tab-alternatives').addEventListener('click', () => switchTab('alternatives'));
document.getElementById('tab-main').addEventListener('keydown', event => {if (event.key === 'ArrowRight') {document.getElementById('tab-alternatives').focus();switchTab('alternatives');}});
document.getElementById('tab-alternatives').addEventListener('keydown', event => {if (event.key === 'ArrowLeft') {document.getElementById('tab-main').focus();switchTab('main');}});
window.addEventListener('pagehide', () => latest.cancel());

alternativesPanel.append(waiting('Запасные пути появятся здесь', 'После подбора покажем соседние условия и то, что нужно уточнить по пожеланиям.'));
loadOptions();

