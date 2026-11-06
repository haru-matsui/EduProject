import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient, ApiError, errorMessage } from '../assets/api.js';

const response = (body, { status = 200, etag } = {}) => new Response(status === 204 ? null : JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json', ...(etag ? { ETag: etag } : {}) },
});
const serverError = (status, code) => response({ error: { code, message: 'Internal details must not appear in UI', details: {}, request_id: 'test' } }, { status });

test('real session response supplies CSRF and every fetch uses same-origin cookies', async () => {
  const calls = [];
  const client = new ApiClient(async (url, options) => {
    calls.push({ url, options });
    return response({ user: { id: 'member-1' }, csrf_token: 'server-csrf-token', expires_at: '2026-12-01T00:00:00Z' });
  });
  const session = await client.session();
  assert.equal(session.user.id, 'member-1');
  assert.equal(client.csrf, 'server-csrf-token');
  assert.equal(calls[0].url, '/api/v1/auth/session');
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.headers['X-CSRF-Token'], undefined);
});

test('a missing or malformed session never grants a usable session', async () => {
  const client = new ApiClient(async () => response({ user: { id: 'member-1' } }));
  await assert.rejects(client.session(), error => error instanceof ApiError && error.code === 'invalid_response');
  assert.equal(client.csrf, null);
});

test('anonymous mutation is stopped before any fetch', async () => {
  let calls = 0;
  const client = new ApiClient(async () => { calls += 1; return response({}); });
  await assert.rejects(client.request('/projects', { method: 'POST', body: {} }), error => error.status === 401);
  assert.equal(calls, 0);
});

test('public invitation preview is readable without a CSRF token', async () => {
  const client = new ApiClient(async (url, options) => {
    assert.equal(url, '/api/v1/invitations/public-token');
    assert.equal(options.method, 'GET');
    return response({ project_title: 'Командный проект', role: 'student' });
  });
  const data = await client.request('/invitations/public-token');
  assert.equal(data.role, 'student');
});

test('assignment sends exact quoted ETag, CSRF and membership identifier', async () => {
  const taskPath = '/projects/project-1/tasks/task-1';
  const calls = [];
  const client = new ApiClient(async (url, options) => {
    calls.push({ url, options });
    return response({ id: 'task-1', version: calls.length }, { etag: `"${calls.length}"` });
  });
  client.csrf = 'csrf-from-session';
  await client.request(taskPath);
  await client.request(`${taskPath}/assignee`, { method: 'PUT', body: { assignee_member_id: 'member-2' }, versionPath: taskPath });
  assert.equal(calls[1].options.headers['If-Match'], '"1"');
  assert.equal(calls[1].options.headers['X-CSRF-Token'], 'csrf-from-session');
  assert.deepEqual(JSON.parse(calls[1].options.body), { assignee_member_id: 'member-2' });
  assert.equal(client.etags.get(taskPath), '"2"');
});

test('start uses the newest task ETag and sends no invented body', async () => {
  const taskPath = '/projects/p/tasks/t';
  const client = new ApiClient(async (url, options) => {
    assert.equal(url, `/api/v1${taskPath}/start`);
    assert.equal(options.headers['If-Match'], '"8"');
    assert.equal(options.method, 'POST');
    assert.equal(options.body, undefined);
    assert.equal(options.headers['Content-Type'], undefined);
    return response({ id: 't', status: 'in_progress' }, { etag: '"9"' });
  });
  client.csrf = 'csrf';
  client.etags.set(taskPath, '"8"');
  const task = await client.request(`${taskPath}/start`, { method: 'POST', versionPath: taskPath });
  assert.equal(task.status, 'in_progress');
  assert.equal(client.etags.get(taskPath), '"9"');
});

test('mutation without an observed resource ETag never guesses a version', async () => {
  let calls = 0;
  const client = new ApiClient(async () => { calls += 1; return response({}); });
  client.csrf = 'csrf';
  await assert.rejects(client.request('/projects/p/tasks/t/start', { method: 'POST', versionPath: '/projects/p/tasks/t' }), error => error.status === 428);
  assert.equal(calls, 0);
});

test('a 412 conflict is surfaced once and never retried silently', async () => {
  let calls = 0;
  const client = new ApiClient(async () => { calls += 1; return serverError(412, 'version_conflict'); });
  client.csrf = 'csrf';
  client.etags.set('/task', '"1"');
  await assert.rejects(client.request('/task/start', { method: 'POST', versionPath: '/task' }), error => {
    assert.equal(error.status, 412);
    assert.match(errorMessage(error), /уже изменил/);
    return true;
  });
  assert.equal(calls, 1);
});

test('expired cookie session clears CSRF and all stored ETags', async () => {
  const client = new ApiClient(async () => serverError(401, 'unauthenticated'));
  client.csrf = 'old-csrf';
  client.etags.set('/task', '"4"');
  await assert.rejects(client.request('/auth/session'), error => error.status === 401);
  assert.equal(client.csrf, null);
  assert.equal(client.etags.size, 0);
});

test('empty successful DELETE response is handled without parsing JSON', async () => {
  const client = new ApiClient(async () => response(null, { status: 204 }));
  client.csrf = 'csrf';
  assert.equal(await client.request('/projects/p/invitations/i', { method: 'DELETE' }), null);
});

test('board pagination loads every page and preserves an existing query', async () => {
  const urls = [];
  const client = new ApiClient(async url => {
    urls.push(url);
    const page = Number(new URL(`http://local${url}`).searchParams.get('page'));
    return response({ items: page === 1 ? [{ id: 'a' }, { id: 'b' }] : [{ id: 'c' }], page, page_size: 2, total: 3 });
  });
  const items = await client.all('/projects/p/tasks?status=todo');
  assert.deepEqual(items.map(item => item.id), ['a', 'b', 'c']);
  assert.deepEqual(urls, ['/api/v1/projects/p/tasks?status=todo&page=1&page_size=100', '/api/v1/projects/p/tasks?status=todo&page=2&page_size=100']);
});

test('inconsistent pagination produces an error instead of an incomplete board', async () => {
  const client = new ApiClient(async () => response({ items: [], page: 1, page_size: 100, total: 8 }));
  await assert.rejects(client.all('/projects/p/tasks'), error => error.code === 'incomplete_page');
});

test('empty project collections are valid', async () => {
  const client = new ApiClient(async () => response({ items: [], page: 1, page_size: 100, total: 0 }));
  assert.deepEqual(await client.all('/projects'), []);
});

test('network failures have a useful safe message and abort is preserved', async () => {
  const client = new ApiClient(async () => { throw new TypeError('secret endpoint or technical stack'); });
  await assert.rejects(client.request('/projects'), error => {
    assert.equal(error.code, 'network_error');
    assert.match(errorMessage(error), /соединения с сервером/);
    assert.doesNotMatch(errorMessage(error), /secret|TypeError/);
    return true;
  });
  const abortingClient = new ApiClient(async () => { throw new DOMException('Aborted', 'AbortError'); });
  await assert.rejects(abortingClient.request('/projects'), error => error.name === 'AbortError');
});

test('unexpected non-JSON response is never interpreted as successful data', async () => {
  const client = new ApiClient(async () => new Response('<html>gateway</html>', { status: 200 }));
  await assert.rejects(client.request('/projects'), error => error.code === 'invalid_response');
});

test('authorization, validation and configuration messages do not expose server details', () => {
  assert.match(errorMessage(new ApiError(503, 'auth_not_configured')), /Вход через Telegram недоступен/);
  assert.match(errorMessage(new ApiError(403, 'forbidden')), /нет прав/);
  assert.match(errorMessage(new ApiError(422, 'validation_error', { password: 'never display' })), /Проверьте/);
  assert.doesNotMatch(errorMessage(new ApiError(500, 'database_password_123')), /database|password|123/);
});

test('ordinary hosts and lookalike domains never receive a Pinggy screening header', async () => {
  for (const hostname of ['localhost', 'eduproject.example', 'site.run.pinggy-free.link.evil.example', 'site.free.pinggy.net.evil.example']) {
    let calls = 0;
    const body = { title: 'ТЕСТ — проект', description: 'Проверка JSON-запроса' };
    const client = new ApiClient(async (url, options) => {
      calls += 1;
      assert.equal(url, '/api/v1/projects');
      assert.equal(options.headers['X-Pinggy-No-Screen'], undefined, hostname);
      assert.equal(options.headers['X-CSRF-Token'], 'csrf-from-session');
      assert.equal(options.headers.Accept, 'application/json');
      assert.equal(options.headers['Content-Type'], 'application/json');
      assert.equal(options.credentials, 'same-origin');
      assert.deepEqual(JSON.parse(options.body), body);
      return response({ id: 'project-1' }, { status: 201 });
    }, { hostname });
    client.csrf = 'csrf-from-session';
    assert.deepEqual(await client.request('/projects', { method: 'POST', body }), { id: 'project-1' });
    assert.equal(calls, 1, hostname);
  }
});

test('Pinggy API mutations retain CSRF and JSON semantics without retrying a screening response', async () => {
  for (const hostname of ['sample.run.pinggy-free.link', 'sample.free.pinggy.net']) {
    for (const screened of [false, true]) {
      let calls = 0;
      const body = { title: 'ТЕСТ — проект', description: 'Проверка через туннель' };
      const client = new ApiClient(async (url, options) => {
        calls += 1;
        assert.equal(url, '/api/v1/projects');
        assert.equal(options.method, 'POST');
        assert.equal(options.headers['X-Pinggy-No-Screen'], '1', hostname);
        assert.equal(options.headers['X-CSRF-Token'], 'csrf-from-session');
        assert.equal(options.headers.Accept, 'application/json');
        assert.equal(options.headers['Content-Type'], 'application/json');
        assert.equal(options.credentials, 'same-origin');
        assert.equal(options.cache, 'no-store');
        assert.deepEqual(JSON.parse(options.body), body);
        return screened
          ? new Response('<html>temporary tunnel screening</html>', { status: 200, headers: { 'Content-Type': 'text/html' } })
          : response({ id: 'project-1' }, { status: 201 });
      }, { hostname });
      await assert.rejects(client.request('/projects', { method: 'POST', body }), error => error.status === 401);
      assert.equal(calls, 0, 'the tunnel header never substitutes for an authenticated CSRF session');
      client.csrf = 'csrf-from-session';
      if (screened) {
        await assert.rejects(client.request('/projects', { method: 'POST', body }), error => error.code === 'invalid_response' && error.status === 502);
      } else {
        assert.deepEqual(await client.request('/projects', { method: 'POST', body }), { id: 'project-1' });
      }
      assert.equal(calls, 1, 'a POST is never silently repeated, including when a gateway returns HTML');
    }
  }
});
