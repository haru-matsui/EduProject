/** Same-origin cookie session; credentials and invite tokens are never persisted. */
export class ApiError extends Error {
  constructor(status, code, details = {}) {
    super(code);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class ApiClient {
  constructor(fetcher = (...args) => fetch(...args), { hostname = globalThis.location?.hostname ?? '' } = {}) {
    this.fetcher = fetcher;
    this.csrf = null;
    this.etags = new Map();
    this.skipPinggyScreen = hostname.endsWith('.run.pinggy-free.link') || hostname.endsWith('.free.pinggy.net');
  }

  clearSession() { this.csrf = null; this.etags.clear(); }

  async session() {
    const data = await this.request('/auth/session');
    if (!data?.user?.id || !data.csrf_token) throw new ApiError(502, 'invalid_response');
    this.csrf = data.csrf_token;
    return data;
  }

  async request(path, { method = 'GET', body, versionPath, signal } = {}) {
    const headers = { Accept: 'application/json' };
    // Free Pinggy tunnels otherwise return an HTML notice to browser API calls.
    if (this.skipPinggyScreen) headers['X-Pinggy-No-Screen'] = '1';
    if (method !== 'GET') {
      if (!this.csrf) throw new ApiError(401, 'session_required');
      headers['X-CSRF-Token'] = this.csrf;
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (versionPath) {
      const etag = this.etags.get(versionPath);
      if (!etag) throw new ApiError(428, 'version_missing');
      headers['If-Match'] = etag;
    }
    let response;
    try {
      response = await this.fetcher(`/api/v1${path}`, {
        method, headers, credentials: 'same-origin', cache: 'no-store', signal,
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new ApiError(0, 'network_error');
    }
    let data = null;
    if (response.status !== 204) {
      try { data = await response.json(); }
      catch { throw new ApiError(response.status >= 400 ? response.status : 502, 'invalid_response'); }
    }
    if (!response.ok) {
      if (response.status === 401) this.clearSession();
      throw new ApiError(response.status, data?.error?.code || 'request_failed', data?.error?.details || {});
    }
    const etag = response.headers.get('ETag');
    if (etag) this.etags.set(versionPath || path, etag);
    return data;
  }

  async all(path, { signal } = {}) {
    const items = [];
    let page = 1;
    let total = Infinity;
    while (items.length < total) {
      const data = await this.request(`${path}${path.includes('?') ? '&' : '?'}page=${page}&page_size=100`, { signal });
      if (!data || !Array.isArray(data.items) || !Number.isInteger(data.total) || data.total < 0 || data.page !== page) {
        throw new ApiError(502, 'invalid_response');
      }
      total = data.total;
      items.push(...data.items);
      if (!data.items.length && items.length < total) throw new ApiError(502, 'incomplete_page');
      page += 1;
      if (page > 10000) throw new ApiError(502, 'invalid_response');
    }
    // Deduplicate if another member created an item between requests.
    return [...new Map(items.map(item => [item.id, item])).values()];
  }
}

export function errorMessage(error) {
  const codes = {
    authorization_failed: 'Не удалось войти. Повторите вход через Telegram',
    oidc_not_configured: 'Вход через Telegram недоступен. Обратитесь к администратору',
    auth_not_configured: 'Вход через Telegram недоступен. Обратитесь к администратору',
    invitation_expired: 'Срок приглашения истёк. Попросите тимлида новую ссылку',
    invitation_revoked: 'Приглашение отозвано. Попросите тимлида новую ссылку',
    invitation_unavailable: 'Приглашение недействительно. Попросите тимлида новую ссылку',
    role_conflict: 'Вы уже участвуете в проекте с другой ролью. Обратитесь к тимлиду',
    project_locked: 'Проект доступен только для просмотра',
    network_error: 'Нет соединения с сервером. Проверьте подключение и повторите попытку',
    invalid_response: 'Не удалось получить данные. Повторите попытку позже',
    incomplete_page: 'Не удалось загрузить все данные. Обновите страницу',
    version_missing: 'Обновите задачу перед изменением',
  };
  if (codes[error?.code]) return codes[error.code];
  const statuses = {
    401: 'Сессия истекла. Войдите снова',
    403: 'У вас нет прав на это действие',
    404: 'Страница не найдена или недоступна',
    409: 'Действие недоступно. Обновите страницу',
    410: 'Приглашение недействительно. Попросите тимлида новую ссылку',
    412: 'Другой участник уже изменил задачу. Обновите её и повторите действие',
    422: 'Проверьте заполненные поля',
    428: 'Обновите задачу перед изменением',
    429: 'Слишком много попыток. Попробуйте позже',
    503: 'Сервис временно недоступен. Попробуйте позже',
  };
  return statuses[error?.status] || 'Не удалось выполнить действие. Повторите попытку';
}
