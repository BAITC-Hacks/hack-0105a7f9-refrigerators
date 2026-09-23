/** Framework-neutral browser/Node client. No selection logic or credentials. */

export class ApiClientError extends Error {
  constructor(kind, message, {status, code, details = []} = {}) {
    super(message);
    this.name = 'ApiClientError';
    this.kind = kind;
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const strings = value => Array.isArray(value) && value.every(item => typeof item === 'string');

function assertPayload(kind, value) {
  let valid = record(value);
  if (kind === 'health') valid &&= value.status === 'ok';
  if (kind === 'options') {
    valid &&= strings(value.cities) && record(value.categories_by_city)
      && Object.values(value.categories_by_city).every(strings)
      && strings(value.event_formats) && strings(value.languages)
      && typeof value.calendar_start === 'string' && typeof value.calendar_end === 'string';
  }
  if (kind === 'recommendations') {
    valid &&= ['matched', 'category_absent', 'no_eligible'].includes(value.status)
      && typeof value.message === 'string' && record(value.reasons)
      && typeof value.ai_mode === 'string' && Array.isArray(value.cards) && value.cards.length <= 3
      && value.cards.every(card => record(card) && typeof card.id === 'string'
        && typeof card.name === 'string' && typeof card.explanation === 'string'
        && typeof card.evidence_quote === 'string' && Number.isSafeInteger(card.price_from_kzt))
      && (value.status === 'matched' ? value.cards.length > 0 : value.cards.length === 0)
      && (value.alternatives === undefined || Array.isArray(value.alternatives));
  }
  if (!valid) throw new ApiClientError('invalid_response', 'Сервер вернул неожиданный формат ответа.');
  return value;
}

function checkBaseUrl(value) {
  if (typeof value !== 'string') throw new TypeError('baseUrl должен быть строкой.');
  value = value.trim().replace(/\/+$/, '');
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
    let rejectAbort;
    let abortError;
    const aborted = new Promise((_, reject) => { rejectAbort = reject; });
    const abort = reason => {
      abortError = new ApiClientError(reason, reason === 'timeout'
        ? 'Сервер не ответил вовремя. Попробуйте ещё раз.' : 'Запрос отменён.');
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
        let payload;
        try { payload = await response.json(); }
        catch { payload = null; }
        if (!response.ok) {
          const error = record(payload?.error) ? payload.error : {};
          const details = Array.isArray(error.details) ? error.details.filter(item =>
            record(item) && typeof item.field === 'string' && typeof item.message === 'string') : [];
          throw new ApiClientError('http', typeof error.message === 'string' ? error.message : 'Ошибка ответа сервера.', {
            status: response.status, code: typeof error.code === 'string' ? error.code : `http_${response.status}`, details,
          });
        }
        return assertPayload(kind, payload);
      } catch (error) {
        if (abortError) throw abortError;
        if (error instanceof ApiClientError) throw error;
        throw new ApiClientError('network', 'Не удалось связаться с сервером. Проверьте соединение и адрес API.');
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
