import { test } from 'node:test';
import assert from 'node:assert/strict';

// This minimal DOM harness runs the actual app module. It does not open a browser,
// assert pixels or replace any production module with simulated data.
class Element {
  constructor() { this.innerHTML = ''; this.textContent = ''; this.open = false; this.disabled = false; this.dataset = {}; this.attrs = new Map(); this.regions = new Map(); this.listeners = {}; this.isConnected = true; }
  querySelector(selector) { if (!this.regions.has(selector)) this.regions.set(selector, new Element()); return this.regions.get(selector); }
  querySelectorAll() { return []; }
  setAttribute(name, value) { this.attrs.set(name, value); }
  getAttribute(name) { return this.attrs.get(name); }
  removeAttribute(name) { this.attrs.delete(name); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  focus() {}
  scrollIntoView() {}
  append() {}
  remove() {}
}

const projectId = '11111111-1111-4111-8111-111111111111';
const taskId = '22222222-2222-4222-8222-222222222222';
const session = { user: { id: 'user-me', display_name: 'Самир', group_name: null, timezone: 'Asia/Yekaterinburg' }, csrf_token: 'csrf-from-server', expires_at: '2026-12-01T00:00:00Z' };
const project = { id: projectId, title: 'Общий проект', description: 'Описание команды', goal: 'Цель', expected_result: 'Приложение', due_at: '2026-12-01T12:00:00Z', status: 'active', current_role: 'student', version: 1, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z' };
const member = { id: 'member-me', project_id: projectId, user: session.user, role: 'student', active: true, joined_at: '2026-10-01T00:00:00Z', left_at: null };
const task = { id: taskId, project_id: projectId, title: 'Реальная задача', description: 'Условие', expected_result: 'Готовый макет', due_at: '2026-12-01T12:00:00Z', priority: 'medium', assignee_member_id: 'member-me', status: 'todo', version: 1, is_overdue: false, is_due_soon: false, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z' };
const page = items => ({ items, page: 1, page_size: 100, total: items.length });
const response = (data, status = 200, etag = null) => new Response(status === 204 ? null : JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', ...(etag ? { ETag: etag } : {}) } });
const unauthorized = () => response({ error: { code: 'unauthenticated', message: 'auth', details: {}, request_id: 'test' } }, 401);
const settle = async () => { for (let i = 0; i < 12; i += 1) await new Promise(resolve => setImmediate(resolve)); };
let importId = 0;

function validCreateForm(kind) {
  const form = new Element();
  form.id = 'create-form';
  form.dataset.kind = kind;
  form.elements = kind === 'task' ? { priority: { value: 'medium' } } : {};
  const values = { title: 'Новый результат', description: 'Описание', expected_result: 'Рабочий результат', due_at: '2026-12-01T17:00' };
  if (kind === 'project') values.goal = 'Учебная цель';
  const fields = Object.entries(values).map(([name, value]) => {
    const field = new Element();
    Object.assign(field, { name, value, type: name === 'due_at' ? 'datetime-local' : 'text' });
    field.classList = { toggle() {} };
    field.checkValidity = () => true;
    return field;
  });
  form.querySelectorAll = () => fields;
  return form;
}

async function harness(path, fetchHandler) {
  const app = new Element();
  const dialog = new Element();
  const doc = { '#app': app, '#app-dialog': dialog, '#announcer': new Element(), '#toast-region': new Element() };
  const events = {};
  const calls = [];
  const assignments = [];
  globalThis.document = { title: '', activeElement: new Element(), querySelector: selector => doc[selector], addEventListener: (name, callback) => { events[name] = callback; }, createElement: () => new Element() };
  globalThis.location = { pathname: path.split('?')[0], search: path.includes('?') ? `?${path.split('?')[1]}` : '', assign: value => assignments.push(value) };
  const setPath = value => { location.pathname = value.split('?')[0]; location.search = value.includes('?') ? `?${value.split('?')[1]}` : ''; };
  globalThis.history = { pushState: (_data, _title, value) => setPath(value), replaceState: (_data, _title, value) => setPath(value) };
  globalThis.window = { scrollTo() {}, addEventListener() {} };
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return fetchHandler(url, options); };
  await import(`../assets/app.js?test=${++importId}`);
  await settle();
  return {
    app, dialog, calls, assignments,
    async submit(form) { dialog.listeners.submit({ preventDefault() {}, target: form }); await settle(); },
    async click(action, dataset = {}, { login = false, href = '' } = {}) {
      const button = new Element();
      button.dataset = { action, ...dataset };
      button.attrs.set('href', href);
      const event = { target: { closest: selector => selector === 'a[data-login]' ? (login ? button : null) : selector === 'button[data-action]' ? (!login ? button : null) : null }, preventDefault() {}, stopPropagation() {} };
      await events.click(event);
      await settle();
    },
  };
}

test('anonymous login checks configuration and never creates a simulated session', async () => {
  const view = await harness('/login', async url => {
    if (url === '/api/v1/auth/session') return unauthorized();
    if (url === '/api/v1/auth/configuration') return response({ telegram_login_configured: false });
    assert.fail(`Unexpected request ${url}`);
  });
  assert.match(view.app.innerHTML, /Войти через Telegram/);
  assert.doesNotMatch(view.app.innerHTML, /Мои проекты/);
  await view.click(null, {}, { login: true, href: '/api/v1/auth/telegram/start?return_path=%2Fprojects' });
  assert.equal(view.assignments.length, 0);
  assert.match(view.app.querySelector('.login-error').innerHTML, /Вход через Telegram недоступен/);
  assert.equal(view.calls.length, 2);
});

test('configured login navigates to Telegram start without inventing an authentication result', async () => {
  const view = await harness('/login', async url => url.endsWith('/session') ? unauthorized() : response({ telegram_login_configured: true }));
  const startPath = '/api/v1/auth/telegram/start?return_path=%2Fprojects';
  await view.click(null, {}, { login: true, href: startPath });
  assert.deepEqual(view.assignments, [startPath]);
  assert.doesNotMatch(view.app.innerHTML, /Мои проекты/);
});

test('empty project screen uses real API state and escapes profile text', async () => {
  const view = await harness('/projects', async url => {
    if (url.endsWith('/session')) return response({ ...session, user: { ...session.user, display_name: '<script>unsafe</script>' } });
    if (url.startsWith('/api/v1/projects?')) return response({ items: [], page: 1, page_size: 12, total: 0 });
    assert.fail(`Unexpected request ${url}`);
  });
  assert.match(view.app.innerHTML, /Проектов пока нет/);
  assert.match(view.app.innerHTML, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.doesNotMatch(view.app.innerHTML, /<script>unsafe/);
  assert.match(view.app.innerHTML, /Создать проект/);
});

test('assignee opens the real task, starts it with ETag and sees its new board state', async () => {
  let currentTask = { ...task };
  const base = `/api/v1/projects/${projectId}`;
  const view = await harness(`/projects/${projectId}`, async (url, options) => {
    if (url.endsWith('/session')) return response(session);
    if (url === base) return response(project, 200, '"1"');
    if (url.startsWith(`${base}/members?`)) return response(page([member]));
    if (url.startsWith(`${base}/tasks?`)) return response(page([currentTask]));
    if (url === `${base}/tasks/${taskId}`) return response(currentTask, 200, `"${currentTask.version}"`);
    if (url === `${base}/tasks/${taskId}/start`) {
      assert.equal(options.method, 'POST');
      assert.equal(options.headers['If-Match'], '"1"');
      assert.equal(options.headers['X-CSRF-Token'], session.csrf_token);
      currentTask = { ...currentTask, status: 'in_progress', version: 2 };
      return response(currentTask, 200, '"2"');
    }
    assert.fail(`Unexpected request ${url}`);
  });
  assert.match(view.app.innerHTML, /Реальная задача/);
  assert.doesNotMatch(view.app.innerHTML, /Новая задача/);
  await view.click('open-task', { id: taskId });
  assert.match(view.dialog.innerHTML, /data-action="start-task"/);
  assert.doesNotMatch(view.dialog.innerHTML, /assignee-form/);
  await view.click('start-task');
  assert.equal(currentTask.status, 'in_progress');
  assert.match(view.dialog.innerHTML, /class="task-status status-in_progress"/);
  assert.doesNotMatch(view.dialog.innerHTML, /data-action="start-task"/);
  assert.equal(view.calls.filter(call => call.url.endsWith('/start')).length, 1);
});

test('a lead can assign only active student or lead memberships in the current project', async () => {
  const base = `/api/v1/projects/${projectId}`;
  const team = [
    { ...member, role: 'lead' },
    { ...member, id: 'member-student', user: { ...session.user, id: 'other', display_name: 'Студент' } },
    { ...member, id: 'member-teacher', role: 'teacher', user: { ...session.user, id: 'teacher', display_name: 'Преподаватель' } },
    { ...member, id: 'member-inactive', active: false, user: { ...session.user, id: 'old', display_name: 'Исключённый' } },
  ];
  const view = await harness(`/projects/${projectId}`, async url => {
    if (url.endsWith('/session')) return response(session);
    if (url === base) return response({ ...project, current_role: 'lead' }, 200, '"1"');
    if (url.startsWith(`${base}/members?`)) return response(page(team));
    if (url.startsWith(`${base}/tasks?`)) return response(page([{ ...task, assignee_member_id: null }]));
    if (url === `${base}/tasks/${taskId}`) return response({ ...task, assignee_member_id: null }, 200, '"1"');
    assert.fail(`Unexpected request ${url}`);
  });
  await view.click('open-task', { id: taskId });
  assert.match(view.dialog.innerHTML, /assignee-form/);
  assert.match(view.dialog.innerHTML, /value="member-student"/);
  assert.match(view.dialog.innerHTML, /value="member-me"/);
  assert.doesNotMatch(view.dialog.innerHTML, /member-teacher|member-inactive/);
  assert.doesNotMatch(view.dialog.innerHTML, /data-action="start-task"/);
});

test('public invitation preserves its return route and exposes no project materials', async () => {
  const token = 'test-invitation-token-with-32-characters';
  const view = await harness(`/invitations/${token}`, async url => {
    if (url.endsWith('/session')) return unauthorized();
    if (url === `/api/v1/invitations/${token}`) return response({ project_title: 'Приглашённый проект', role: 'student', expires_at: '2026-12-01T00:00:00Z', requires_login: true });
    assert.fail(`Unexpected request ${url}`);
  });
  assert.match(view.app.innerHTML, /Приглашение в проект/);
  assert.match(view.app.innerHTML, /Войти через Telegram/);
  assert.match(view.app.innerHTML, new RegExp(encodeURIComponent(`/invitations/${token}`)));
  assert.doesNotMatch(view.app.innerHTML, /Реальная задача|data-action="accept-invite"/);
  assert.equal(view.calls.length, 2);
});

test('project form rejects whitespace, trims actual fields and submits a timezone-aware deadline', async () => {
  let created = false;
  let sent = null;
  const base = `/api/v1/projects/${projectId}`;
  const view = await harness('/projects', async (url, options) => {
    if (url.endsWith('/session')) return response(session);
    if (url.startsWith('/api/v1/projects?')) return response({ items: [], page: 1, page_size: 12, total: 0 });
    if (url === '/api/v1/projects' && options.method === 'POST') {
      sent = JSON.parse(options.body);
      created = true;
      return response({ ...project, current_role: 'lead' }, 201, '"1"');
    }
    if (url === base) return response({ ...project, current_role: 'lead' }, 200, '"1"');
    if (url.startsWith(`${base}/members?`)) return response(page([{ ...member, role: 'lead' }]));
    if (url.startsWith(`${base}/tasks?`)) return response(page([]));
    assert.fail(`Unexpected request ${url}`);
  });
  await view.click('create-project');
  assert.match(view.dialog.innerHTML, /name="goal"/);
  assert.match(view.dialog.innerHTML, /name="expected_result"/);
  assert.match(view.dialog.innerHTML, /type="datetime-local"/);
  const form = new Element();
  form.id = 'create-form';
  form.dataset.kind = 'project';
  form.elements = {};
  const values = { title: '   ', description: ' Описание ', goal: ' Цель ', expected_result: ' Результат ', due_at: '2026-12-01T17:00' };
  const fields = Object.entries(values).map(([name, value]) => {
    const field = new Element();
    field.name = name;
    field.value = value;
    field.type = name === 'due_at' ? 'datetime-local' : 'text';
    field.classList = { toggle() {} };
    field.checkValidity = () => true;
    return field;
  });
  form.querySelectorAll = () => fields;
  await view.submit(form);
  assert.equal(created, false);
  assert.match(form.querySelector('#error-title').textContent, /Заполните/);
  fields[0].value = ' Новый проект ';
  await view.submit(form);
  assert.equal(created, true);
  assert.equal(sent.title, 'Новый проект');
  assert.equal(sent.description, 'Описание');
  assert.equal(sent.goal, 'Цель');
  assert.equal(sent.expected_result, 'Результат');
  assert.equal(sent.due_at, new Date(values.due_at).toISOString());
  assert.match(sent.due_at, /Z$/);
  assert.equal(location.pathname, `/projects/${projectId}`);
  assert.equal(view.dialog.open, false);
});

test('a conflict keeps the task open and asks for an explicit refresh without silent replay', async () => {
  const base = `/api/v1/projects/${projectId}`;
  const view = await harness(`/projects/${projectId}`, async url => {
    if (url.endsWith('/session')) return response(session);
    if (url === base) return response(project, 200, '"1"');
    if (url.startsWith(`${base}/members?`)) return response(page([member]));
    if (url.startsWith(`${base}/tasks?`)) return response(page([task]));
    if (url === `${base}/tasks/${taskId}`) return response(task, 200, '"1"');
    if (url === `${base}/tasks/${taskId}/start`) return response({ error: { code: 'version_conflict', message: 'old version', details: {}, request_id: 'test' } }, 412);
    assert.fail(`Unexpected request ${url}`);
  });
  await view.click('open-task', { id: taskId });
  await view.click('start-task');
  assert.equal(view.dialog.open, true);
  assert.match(view.dialog.querySelector('.form-error').innerHTML, /Другой участник уже изменил задачу/);
  assert.match(view.dialog.innerHTML, /Обновить задачу/);
  assert.equal(view.calls.filter(call => call.url.endsWith('/start')).length, 1);
});

for (const kind of ['project', 'task']) {
  test(`expired session during ${kind} creation closes the dialog and requires a new login without replay`, async () => {
    const base = `/api/v1/projects/${projectId}`;
    const path = kind === 'project' ? '/projects' : `/projects/${projectId}`;
    const createUrl = kind === 'project' ? '/api/v1/projects' : `${base}/tasks`;
    const view = await harness(path, async (url, options) => {
      if (url.endsWith('/session')) return response(session);
      if (url === createUrl && options.method === 'POST') return unauthorized();
      if (url.startsWith('/api/v1/projects?')) return response({ items: [], page: 1, page_size: 12, total: 0 });
      if (url === base) return response({ ...project, current_role: 'lead' }, 200, '"1"');
      if (url.startsWith(`${base}/members?`)) return response(page([{ ...member, role: 'lead' }]));
      if (url.startsWith(`${base}/tasks?`)) return response(page([]));
      assert.fail(`Unexpected request ${url}`);
    });
    await view.click(`create-${kind}`);
    assert.equal(view.dialog.open, true);
    await view.submit(validCreateForm(kind));
    assert.equal(view.dialog.open, false);
    assert.match(view.app.innerHTML, /Войти через Telegram/);
    assert.match(view.app.innerHTML, /Сессия истекла/);
    assert.ok(view.app.innerHTML.includes(`return_path=${encodeURIComponent(path)}`));
    assert.equal(view.calls.filter(call => call.options.method === 'POST').length, 1);
    const callsAfterFailure = view.calls.length;
    await view.click('retry');
    assert.equal(location.pathname, '/login');
    assert.equal(view.calls.length, callsAfterFailure, 'local authentication is cleared before revisiting a protected route');
  });
}

for (const action of ['assignee', 'start']) {
  test(`expired session during task ${action} closes the dialog without replaying the mutation`, async () => {
    const base = `/api/v1/projects/${projectId}`;
    const taskUrl = `${base}/tasks/${taskId}`;
    const view = await harness(`/projects/${projectId}`, async (url, options) => {
      if (url.endsWith('/session')) return response(session);
      if (url === base) return response({ ...project, current_role: 'lead' }, 200, '"1"');
      if (url.startsWith(`${base}/members?`)) return response(page([{ ...member, role: 'lead' }]));
      if (url.startsWith(`${base}/tasks?`)) return response(page([task]));
      if (url === taskUrl) return response(task, 200, '"7"');
      if (url === `${taskUrl}/${action}`) {
        assert.equal(options.method, action === 'assignee' ? 'PUT' : 'POST');
        assert.equal(options.headers['X-CSRF-Token'], session.csrf_token);
        assert.equal(options.headers['If-Match'], '"7"');
        return unauthorized();
      }
      assert.fail(`Unexpected request ${url}`);
    });
    await view.click('open-task', { id: taskId });
    if (action === 'assignee') {
      const form = new Element();
      form.id = 'assignee-form';
      form.elements = { assignee_member_id: { value: member.id } };
      await view.submit(form);
    } else await view.click('start-task');
    assert.equal(view.dialog.open, false);
    assert.match(view.app.innerHTML, /Войти через Telegram/);
    assert.ok(view.app.innerHTML.includes(`return_path=${encodeURIComponent(`/projects/${projectId}`)}`));
    assert.equal(view.calls.filter(call => call.url === `${taskUrl}/${action}`).length, 1);
    assert.equal(view.calls.filter(call => call.url === taskUrl).length, 1, 'an expired mutation never triggers a follow-up task reload');
    const callsAfterFailure = view.calls.length;
    await view.click('retry');
    assert.equal(location.pathname, '/login');
    assert.equal(view.calls.length, callsAfterFailure);
  });
}

test('expired session while opening a task closes its loading dialog and preserves the login return route', async () => {
  const base = `/api/v1/projects/${projectId}`;
  const taskUrl = `${base}/tasks/${taskId}`;
  const view = await harness(`/projects/${projectId}?view=board`, async url => {
    if (url.endsWith('/session')) return response(session);
    if (url === base) return response(project, 200, '"1"');
    if (url.startsWith(`${base}/members?`)) return response(page([member]));
    if (url.startsWith(`${base}/tasks?`)) return response(page([task]));
    if (url === taskUrl) return unauthorized();
    assert.fail(`Unexpected request ${url}`);
  });
  await view.click('open-task', { id: taskId });
  assert.equal(view.dialog.open, false);
  assert.match(view.app.innerHTML, /Войти через Telegram/);
  assert.ok(view.app.innerHTML.includes(`return_path=${encodeURIComponent(`/projects/${projectId}`)}`));
  assert.equal(view.calls.filter(call => call.url === taskUrl).length, 1);
});
