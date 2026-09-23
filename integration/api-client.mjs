/** Framework-neutral browser/Node client. No selection logic or credentials. */

export class ApiClientError extends Error {
  constructor(kind, message, {status, code, details = [], requestId} = {}) {
    super(message);
    this.name = 'ApiClientError';
    this.kind = kind;
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const strings = value => Array.isArray(value) && value.every(item => typeof item === 'string');
const count = value => Number.isSafeInteger(value) && value >= 0;
const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
const nullableText = value => value == null || typeof value === 'string';
const isoDate = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
  && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;

function validRequest(value) {
  return record(value) && ['city', 'category', 'event_format'].every(key => typeof value[key] === 'string' && value[key].trim())
    && isoDate(value.date) && count(value.budget_kzt) && value.budget_kzt > 0
    && (value.duration_hours == null || positive(value.duration_hours))
    && nullableText(value.language) && nullableText(value.brief);
}

function validAlternative(value) {
  if (!record(value) || !validRequest(value.request) || typeof value.message !== 'string'
    || !count(value.eligible_count) || !value.eligible_count || !count(value.added_count) || !value.added_count
    || value.added_count > value.eligible_count || !strings(value.candidate_ids)
    || value.candidate_ids.length !== value.eligible_count || new Set(value.candidate_ids).size !== value.candidate_ids.length
    || !Array.isArray(value.changes) || !value.changes.length || value.changes.length > 3) return false;
  const seen = new Set();
  return value.changes.every(change => {
    if (!record(change) || !['date', 'budget_kzt', 'duration_hours'].includes(change.field) || seen.has(change.field)) return false;
    seen.add(change.field);
    const valid = change.field === 'date' ? isoDate : change.field === 'budget_kzt' ? value => count(value) && value > 0 : positive;
    return valid(change.from_value) && valid(change.to_value) && change.from_value !== change.to_value
      && value.request[change.field] === change.to_value;
  });
}

function assertPayload(kind, value) {
  let valid = record(value);
  if (kind === 'health') valid &&= value.status === 'ok';
  if (kind === 'options') {
    valid &&= strings(value.cities) && record(value.categories_by_city)
      && Object.values(value.categories_by_city).every(strings)
      && value.cities.every(city => Object.hasOwn(value.categories_by_city, city))
      && strings(value.event_formats) && strings(value.languages)
      && isoDate(value.calendar_start) && isoDate(value.calendar_end) && value.calendar_start <= value.calendar_end;
  }
  if (kind === 'recommendations') {
    valid &&= ['matched', 'category_absent', 'no_eligible'].includes(value.status)
      && typeof value.message === 'string' && record(value.reasons)
      && Object.values(value.reasons).every(count)
      && ['openai', 'nvidia', 'brev', 'fallback', 'not_used'].includes(value.ai_mode)
      && Array.isArray(value.cards) && value.cards.length <= 3
      && value.cards.every(card => record(card) && typeof card.id === 'string'
        && ['name', 'city', 'category', 'explanation', 'evidence_quote'].every(key => typeof card[key] === 'string')
        && ['synthetic', 'city_imputed', 'price_imputed'].every(key => typeof card[key] === 'boolean')
        && ['why_fits', 'to_clarify', 'differences'].every(key => card[key] === undefined || strings(card[key]))
        && nullableText(card.evidence_note) && count(card.price_from_kzt) && card.price_from_kzt > 0)
      && new Set(value.cards.map(card => card.id)).size === value.cards.length
      && (value.status === 'matched' ? value.cards.length > 0 : value.cards.length === 0)
      && record(value.counts)
      && ['category_total', 'eligible_total', 'returned_total', 'excluded_total'].every(key => count(value.counts[key]))
      && value.counts.returned_total === value.cards.length && value.counts.returned_total === Math.min(3, value.counts.eligible_total)
      && value.counts.category_total === value.counts.eligible_total + value.counts.excluded_total
      && (value.status === 'category_absent' ? value.counts.category_total === 0 : value.counts.category_total > 0)
      && record(value.understanding)
      && ['styles', 'notes', 'unwanted', 'languages'].every(key => strings(value.understanding[key]))
      && nullableText(value.understanding.effective_language)
      && ['field', 'brief', 'unspecified'].includes(value.understanding.language_source)
      && nullableText(value.alternatives_note)
      && (value.alternatives === undefined || (Array.isArray(value.alternatives) && value.alternatives.length <= 3 && value.alternatives.every(validAlternative)));
  }
  if (!valid) throw new ApiClientError('invalid_response', 'Сервер вернул неожиданный формат ответа.');
  return value;
}

function checkBaseUrl(value) {
  if (typeof value !== 'string') throw new TypeError('baseUrl должен быть строкой.');
  value = value.trim().replace(/\/+$/, '');
  if (/[\\\s\u0000-\u001f\u007f]/.test(value)) throw new TypeError('baseUrl содержит недопустимые символы.');
  if (!value || (/^\/(?!\/)/.test(value) && !/[?#]/.test(value))) return value;
  const parsed = new URL(value);
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new TypeError('baseUrl: используйте HTTP(S) адрес или /api без ключей и query.');
  }
  return value;
}

export function createApiClient({baseUrl = '', timeoutMs = 10000, fetchImpl = globalThis.fetch} = {}) {
  const base = checkBaseUrl(baseUrl);
  if (typeof fetchImpl !== 'function' || !Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError('Нужны fetch и положительный timeoutMs.');
  }

  async function call(path, kind, init, {signal} = {}) {
    const controller = new AbortController();
    let requestId;
    let rejectAbort;
    let abortError;
    const aborted = new Promise((_, reject) => { rejectAbort = reject; });
    const abort = reason => {
      abortError = new ApiClientError(reason, reason === 'timeout'
        ? 'Сервер не ответил вовремя. Попробуйте ещё раз.' : 'Запрос отменён.', {requestId});
      controller.abort();
      rejectAbort(abortError);
    };
    const onCancel = () => abort('cancelled');
    if (signal?.aborted) throw new ApiClientError('cancelled', 'Запрос отменён.');
    signal?.addEventListener('abort', onCancel, {once: true});
    const timer = setTimeout(() => abort('timeout'), timeoutMs);
    async function execute() {
      try {
        const response = await fetchImpl(base + path, {
          ...init, signal: controller.signal, credentials: 'omit', cache: 'no-store',
          headers: {Accept: 'application/json', ...(init.body ? {'Content-Type': 'application/json'} : {})},
        });
        const responseId = response.headers?.get?.('X-Request-ID');
        if (typeof responseId === 'string' && /^[a-f0-9]{32}$/i.test(responseId)) requestId = responseId.toLowerCase();
        let payload;
        try { payload = await response.json(); }
        catch { payload = null; }
        if (!response.ok) {
          const error = record(payload?.error) ? payload.error : {};
          const details = Array.isArray(error.details) ? error.details.filter(item =>
            record(item) && typeof item.field === 'string' && typeof item.message === 'string') : [];
          throw new ApiClientError('http', typeof error.message === 'string' ? error.message : 'Ошибка ответа сервера.', {
            status: response.status, code: typeof error.code === 'string' ? error.code : `http_${response.status}`, details, requestId,
          });
        }
        return assertPayload(kind, payload);
      } catch (error) {
        if (abortError) throw abortError;
        if (error instanceof ApiClientError) { error.requestId ??= requestId; throw error; }
        throw new ApiClientError('network', 'Не удалось связаться с сервером. Проверьте соединение и адрес API.', {requestId});
      }
    }
    try { return await Promise.race([execute(), aborted]); }
    finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onCancel);
    }
  }

  return {
    health: options => call('/health', 'health', {method: 'GET'}, options),
    catalogueOptions: options => call('/catalogue/options', 'options', {method: 'GET'}, options),
    recommend: (request, options) => call('/recommendations', 'recommendations',
      {method: 'POST', body: JSON.stringify(request)}, options),
  };
}

/** Convert form controls, not business conditions. Server remains authoritative. */
export function buildRecommendationRequest(form) {
  const issue = (field, message) => {
    throw new ApiClientError('input', message, {code: 'invalid_request', details: [{field, message}]});
  };
  const text = (field, optional = false) => {
    const value = form[field];
    if (optional && (value === undefined || value === null || value === '')) return null;
    if (typeof value !== 'string' || (!optional && !value.trim())) issue(field, 'Заполните текстовое поле.');
    return value.trim() || null;
  };
  const number = (field, optional, integer) => {
    const value = form[field];
    if (optional && (value === undefined || value === null || (typeof value === 'string' && !value.trim()))) return null;
    if (typeof value !== 'number' && typeof value !== 'string') issue(field, 'Укажите число.');
    if (typeof value === 'string' && !/^\d+(?:\.\d+)?$/.test(value.trim())) issue(field, 'Укажите число без знака валюты и разделителей тысяч.');
    const result = Number(value);
    if (!Number.isFinite(result) || result <= 0 || (integer && !Number.isSafeInteger(result))) issue(field, 'Укажите положительное число допустимого размера.');
    return result;
  };
  const request = {
    city: text('city'), date: text('date'), event_format: text('event_format'), category: text('category'),
    budget_kzt: number('budget_kzt', false, true), duration_hours: number('duration_hours', true, false),
    language: text('language', true), brief: text('brief', true),
  };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(request.date)) issue('date', 'Дата должна иметь формат ГГГГ-ММ-ДД.');
  return request;
}

/** One instance per form: a stale result resolves to null and cannot overwrite the latest. */
export function createLatestRecommender(client) {
  let sequence = 0;
  let controller;
  return {
    async recommend(request) {
      const current = ++sequence;
      controller?.abort();
      controller = new AbortController();
      try {
        const result = await client.recommend(request, {signal: controller.signal});
        return current === sequence ? result : null;
      } catch (error) {
        if (current !== sequence) return null;
        throw error;
      }
    },
    cancel() { sequence++; controller?.abort(); },
  };
}
