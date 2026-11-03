import { ApiClient, ApiError, errorMessage } from './api.js';

const api = new ApiClient();
const app = document.querySelector('#app');
const dialog = document.querySelector('#app-dialog');
const statusNames = { todo: 'К выполнению', in_progress: 'В работе', in_review: 'На проверке', done: 'Готово' };
const priorities = { low: 'Низкий', medium: 'Средний', high: 'Высокий' };
const taskPluralRules = new Intl.PluralRules('ru');
const taskCountLabel = count => `${count} ${{ one: 'задача', few: 'задачи', many: 'задач', other: 'задачи' }[taskPluralRules.select(count)]}`;
const memberCountLabel = count => `${count} ${{ one: 'участник', few: 'участника', many: 'участников', other: 'участника' }[taskPluralRules.select(count)]}`;
const roles = { lead: 'Тимлид', student: 'Участник', teacher: 'Преподаватель' };
const projectStatuses = { active: 'Активный', final_review: 'На итоговой проверке', changes_requested: 'На доработке', completed: 'Завершён' };
const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  back: '<path d="M19 12H5m6-6-6 6 6 6"/>',
  people: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 4v2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  link: '<path d="m10 13 4-4m-6 7-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 2 1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(2 0)"/>',
  logout: '<path d="M9 4H4v16h5m6-12 4 4-4 4m-7-4h11"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  telegram: '<path d="m21 3-4 18-6-6-4 3 1-7L21 3ZM8 11l9-5-6 9"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 10v7m0-10v.1"/>',
};
const icon = (name, cls = '') => `<svg class="icon ${cls}" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.grid}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const initials = name => String(name || '?').trim().split(/\s+/).slice(0, 2).map(s => s[0]).join('').toUpperCase();
const date = value => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Срок не указан' : new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short', year: 'numeric' }).format(parsed);
};
const isOpen = project => ['active', 'changes_requested'].includes(project?.status);
let session = null;
let currentProject = null;
let members = [];
let tasks = [];
let invitations = [];
let createdInvite = null;
let taskInDialog = null;
let controller = null;
let routeSequence = 0;
let lastDialogTrigger = null;

function announce(message) { document.querySelector('#announcer').textContent = message; }
function toast(message) {
  const item = document.createElement('div');
  item.className = 'notice';
  item.textContent = message;
  document.querySelector('#toast-region').append(item);
  setTimeout(() => item.remove(), 5000);
}

function navigate(path, { replace = false } = {}) {
  closeDialog();
  if (replace) history.replaceState({}, '', path); else history.pushState({}, '', path);
  renderRoute();
  window.scrollTo(0, 0);
}

function shell(content, { project = null } = {}) {
  return `<div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="/projects" data-nav><span class="brand-mark">E<span></span></span>EduProject<span class="brand-dot">●</span></a>
      <nav aria-label="Основная навигация"><a class="sidebar-link active" href="/projects" data-nav>${icon('grid')}<span>Мои проекты</span></a></nav>
      ${project ? `<div class="sidebar-project"><div class="current-project-name"><span class="project-mini">${esc(initials(project.title))}</span><span>${esc(project.title)}</span></div></div>` : ''}
      <div class="account"><span class="avatar">${esc(initials(session?.user.display_name))}</span><div class="account-text"><strong>${esc(session?.user.display_name)}</strong>${project ? `<span class="account-role" title="Роль в этом проекте">${esc(roles[project.current_role])}</span>` : ''}</div><button class="icon-button" data-action="logout" aria-label="Выйти" title="Выйти">${icon('logout')}</button></div>
    </aside>
    <div class="workspace"><main id="main" tabindex="-1" class="main-content">${content}</main></div>
  </div>`;
}

function loading(title = 'Загрузка') {
  const content = `<div class="page-heading"><h1>${esc(title)}</h1></div><div class="skeleton-grid" aria-busy="true" aria-label="Загрузка"><div class="skeleton-card"></div><div class="skeleton-card"></div><div class="skeleton-card"></div></div>`;
  app.innerHTML = session ? shell(content) : `<main id="main" class="boot-screen"><span class="loader" aria-hidden="true"></span><p role="status">${esc(title)}</p></main>`;
}

function errorState(error, retry = true) {
  return `<div class="state-card error-state" role="alert"><span class="state-icon">${icon('info')}</span><h2>Не удалось открыть страницу</h2><p>${esc(errorMessage(error))}</p><div class="d-flex gap-2 justify-content-center flex-wrap">${retry ? '<button class="btn btn-primary" data-action="retry">Повторить попытку</button>' : ''}<a class="btn btn-outline-secondary" href="/projects" data-nav>Мои проекты</a></div></div>`;
}

function loginView(error = null, returnPath = '/projects') {
  const loginUrl = `/api/v1/auth/telegram/start?return_path=${encodeURIComponent(returnPath)}`;
  app.innerHTML = `<main id="main" class="auth-layout"><section class="auth-story" aria-label="EduProject"><a class="brand" href="/login" data-nav><span class="brand-mark">E<span></span></span>EduProject<span class="brand-dot">●</span></a><div class="auth-story-body"><h1>Управление учебными проектами</h1><div class="auth-illustration" aria-hidden="true"><div class="illustration-top"><i></i><i></i><i></i></div><div class="illustration-row"><span class="illustration-tick">✓</span><div><b></b><i></i></div><span class="illustration-avatar">●</span></div><div class="illustration-row"><span class="illustration-tick dim">✓</span><div><b></b><i></i></div><span class="illustration-avatar second">●</span></div><div class="illustration-progress"><span></span></div></div></div></section><section class="auth-form-side"><div class="auth-box"><h2>Вход в EduProject</h2><div class="login-error" role="alert">${error ? `<div class="inline-error">${esc(errorMessage(error))}</div>` : ''}</div><a class="btn btn-primary telegram-button" href="${esc(loginUrl)}" data-login>${icon('telegram')}<span>Войти через Telegram</span>${icon('arrow')}</a><p class="login-caption">При первом входе аккаунт создаётся автоматически</p></div></section></main>`;
  document.title = 'Вход — EduProject';
}

function projectCard(project) {
  return `<a class="project-card" href="/projects/${encodeURIComponent(project.id)}" data-nav><div class="project-card-top"><span class="project-monogram">${esc(initials(project.title))}</span><span class="status-pill ${project.status === 'completed' ? 'success' : ''}"><span></span>${esc(projectStatuses[project.status] || project.status)}</span></div><h2>${esc(project.title)}</h2><p class="project-description">${esc(project.description)}</p><div class="project-card-bottom"><span>${icon('clock')}${esc(date(project.due_at))}</span><span class="role-badge">${esc(roles[project.current_role])}</span></div></a>`;
}

async function projectsView(signal) {
  const page = Math.max(1, Number.parseInt(new URLSearchParams(location.search).get('page') || '1', 10) || 1);
  const data = await api.request(`/projects?page=${page}&page_size=12`, { signal });
  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
  if (page > totalPages) return navigate(`/projects?page=${totalPages}`, { replace: true });
  const cards = data.items.length ? `<div class="project-grid">${data.items.map(projectCard).join('')}</div>` : `<div class="state-card empty-projects"><span class="empty-art">${icon('grid')}</span><h2>Проектов пока нет</h2><p>Создайте проект или откройте ссылку-приглашение</p><button class="btn btn-primary" data-action="create-project">${icon('plus')}Создать проект</button></div>`;
  app.innerHTML = shell(`<div class="page-heading"><h1>Мои проекты <span class="heading-count">${data.total}</span></h1>${data.items.length ? `<button class="btn btn-primary" data-action="create-project">${icon('plus')}Создать проект</button>` : ''}</div>${cards}${totalPages > 1 ? `<nav class="pagination-row" aria-label="Страницы проектов">${page > 1 ? `<a class="btn btn-outline-secondary" href="/projects?page=${page - 1}" data-nav>Назад</a>` : '<span></span>'}<span>Страница ${page} из ${totalPages}</span>${page < totalPages ? `<a class="btn btn-outline-secondary" href="/projects?page=${page + 1}" data-nav>Далее</a>` : '<span></span>'}</nav>` : ''}`);
  document.title = 'Мои проекты — EduProject';
}

function projectTop(project, view) {
  return `<a class="back-link" href="/projects" data-nav>${icon('back')}Мои проекты</a><div class="page-heading project-heading"><div><div class="project-title-row"><span class="project-monogram">${esc(initials(project.title))}</span><h1>${esc(project.title)}</h1></div><div class="project-meta"><span class="status-pill"><span></span>${esc(projectStatuses[project.status])}</span><span>${icon('clock')}До ${esc(date(project.due_at))}</span><span>${icon('people')}${memberCountLabel(members.length)}</span></div></div>${view === 'board' && project.current_role === 'lead' && isOpen(project) ? `<button class="btn btn-primary" data-action="create-task">${icon('plus')}Создать задачу</button>` : ''}</div><nav class="project-tabs" aria-label="Разделы проекта">${[['board', 'Доска задач', 'grid'], ['team', 'Команда', 'people'], ['overview', 'О проекте', 'info']].map(([key, text, name]) => `<a class="${view === key ? 'selected' : ''}" href="/projects/${encodeURIComponent(project.id)}${key === 'board' ? '' : `?view=${key}`}" data-nav ${view === key ? 'aria-current="page"' : ''}>${icon(name)}${text}</a>`).join('')}</nav>`;
}

function taskCard(task) {
  const assignee = members.find(member => member.id === task.assignee_member_id);
  return `<button class="task-card" data-action="open-task" data-id="${esc(task.id)}"><div class="task-card-labels"><span class="priority priority-${esc(task.priority)}"><i></i>${esc(priorities[task.priority])}</span><span class="task-open-label" aria-hidden="true">↗</span></div><h3>${esc(task.title)}</h3><p>${esc(task.expected_result)}</p><div class="task-card-bottom"><span class="task-date ${task.is_overdue ? 'overdue' : task.is_due_soon ? 'due-soon' : ''}">${icon('clock')}${task.is_overdue ? 'Просрочено · ' : ''}${esc(date(task.due_at))}</span>${assignee ? `<span class="avatar avatar-sm" title="${esc(assignee.user.display_name)}" aria-label="Исполнитель: ${esc(assignee.user.display_name)}">${esc(initials(assignee.user.display_name))}</span>` : '<span class="unassigned" title="Исполнитель не назначен">—</span>'}</div></button>`;
}

function boardView() {
  return `<div class="board-heading"><h2>Задачи</h2><span class="muted">${taskCountLabel(tasks.length)}</span></div>${!isOpen(currentProject) ? '<div class="info-banner">Проект доступен только для просмотра</div>' : ''}<p class="board-mobile-hint">Прокрутите доску, чтобы увидеть остальные статусы</p><div class="board" aria-label="Доска задач">${Object.entries(statusNames).map(([key, label]) => {
    const columnTasks = tasks.filter(task => task.status === key);
    return `<section class="board-column status-${key}" aria-label="${label}"><div class="column-header"><h2><i></i>${label}<span>${columnTasks.length}</span></h2>${key === 'todo' && currentProject.current_role === 'lead' && isOpen(currentProject) ? `<button class="icon-button" data-action="create-task" aria-label="Создать задачу">${icon('plus')}</button>` : ''}</div><div class="column-tasks">${columnTasks.length ? columnTasks.map(taskCard).join('') : '<div class="column-empty"><p>Нет задач</p></div>'}</div></section>`;
  }).join('')}</div>`;
}

function invitationStatus(invitation) {
  if (invitation.revoked_at) return ['Отозвано', false];
  if (new Date(invitation.expires_at).getTime() <= Date.now()) return ['Срок истёк', false];
  return ['Действует', true];
}

function teamView() {
  return `<div class="section-heading"><h2>Участники</h2></div><div class="members-list">${members.map(member => `<div class="member-row"><span class="avatar">${esc(initials(member.user.display_name))}</span><div class="member-name"><strong>${esc(member.user.display_name)}${member.user.id === session.user.id ? '<span class="you-label">вы</span>' : ''}</strong>${member.user.group_name ? `<span>${esc(member.user.group_name)}</span>` : ''}</div><span class="role-badge ${member.role === 'lead' ? 'lead-role' : ''}">${esc(roles[member.role])}</span></div>`).join('')}</div>${currentProject.current_role === 'lead' ? `<section class="invitations-section"><div class="section-heading"><div><h2>Приглашения</h2><p>Ссылка действует 7 дней</p></div>${isOpen(currentProject) ? `<button class="btn btn-outline-primary" data-action="create-invite">${icon('link')}Создать ссылку</button>` : ''}</div>${createdInvite?.projectId === currentProject.id ? `<div class="created-invite"><strong>${icon('check')}Ссылка создана</strong><p>Сохраните ссылку: повторно посмотреть её нельзя</p><div class="copy-row"><input class="form-control" readonly aria-label="Ссылка приглашения" value="${esc(createdInvite.url)}"><button class="btn btn-primary" data-action="copy-invite">Копировать ссылку</button></div><span class="muted small">Действует до ${esc(date(createdInvite.expiresAt))}</span></div>` : ''}<div class="invitation-list">${invitations.length ? invitations.map(invitation => {
    const [status, active] = invitationStatus(invitation);
    return `<div class="invitation-row"><span class="invitation-icon">${icon('link')}</span><div><strong>Приглашение</strong><span>Срок действия: до ${esc(date(invitation.expires_at))}</span></div><span class="invitation-status ${active ? 'valid' : ''}">${status}</span>${active && isOpen(currentProject) ? `<button class="btn btn-sm btn-outline-secondary" data-action="revoke-invite" data-id="${esc(invitation.id)}">Отозвать</button>` : ''}</div>`;
  }).join('') : '<div class="subtle-empty">Приглашений пока нет</div>'}</div></section>` : ''}`;
}

function overviewView() {
  return `<div class="project-details"><section><h2>Описание</h2><p class="preserve-lines">${esc(currentProject.description)}</p></section><section><h2>Цель</h2><p class="preserve-lines">${esc(currentProject.goal)}</p></section><section><h2>Ожидаемый результат</h2><p class="preserve-lines">${esc(currentProject.expected_result)}</p></section><div class="details-foot"><span>Создан ${esc(date(currentProject.created_at))}</span></div></div>`;
}

async function projectView(id, signal) {
  const view = new URLSearchParams(location.search).get('view') || 'board';
  const project = await api.request(`/projects/${encodeURIComponent(id)}`, { signal });
  const [team, taskItems, inviteItems] = await Promise.all([
    api.all(`/projects/${encodeURIComponent(id)}/members`, { signal }),
    api.all(`/projects/${encodeURIComponent(id)}/tasks`, { signal }),
    view === 'team' && project.current_role === 'lead' ? api.all(`/projects/${encodeURIComponent(id)}/invitations`, { signal }) : Promise.resolve([]),
  ]);
  currentProject = project;
  members = team.filter(member => member.active);
  tasks = taskItems;
  invitations = inviteItems;
  const activeView = ['board', 'team', 'overview'].includes(view) ? view : 'board';
  app.innerHTML = shell(`${projectTop(project, activeView)}${activeView === 'team' ? teamView() : activeView === 'overview' ? overviewView() : boardView()}`, { project });
  document.title = `${project.title} — EduProject`;
}

async function invitationView(token, signal) {
  const preview = await api.request(`/invitations/${encodeURIComponent(token)}`, { signal });
  const returnPath = `/invitations/${encodeURIComponent(token)}`;
  const content = `<div class="invite-page"><span class="invite-hero-icon">${icon('people')}</span><h1>Приглашение в проект</h1><div class="invite-project-title">${esc(preview.project_title)}</div><div class="invite-facts"><span>${icon('people')}${preview.role === 'teacher' ? 'Преподаватель' : 'Участник'}</span><span>${icon('clock')}Действует до ${esc(date(preview.expires_at))}</span></div><div id="invite-error" class="login-error" role="alert"></div>${session ? `<button class="btn btn-primary btn-wide" data-action="accept-invite" data-token="${esc(token)}">Присоединиться${icon('arrow')}</button><p class="invite-account">Вы вошли как ${esc(session.user.display_name)}</p>` : `<a class="btn btn-primary btn-wide" href="/api/v1/auth/telegram/start?return_path=${encodeURIComponent(returnPath)}" data-login>${icon('telegram')}Войти через Telegram</a><p class="invite-account">После входа подтвердите вступление в проект</p>`}</div>`;
  app.innerHTML = session ? shell(content) : `<div class="public-layout"><header><a class="brand" href="/login" data-nav><span class="brand-mark">E<span></span></span>EduProject<span class="brand-dot">●</span></a></header><main id="main">${content}</main></div>`;
  document.title = 'Приглашение — EduProject';
}

async function renderRoute() {
  const sequence = ++routeSequence;
  controller?.abort();
  controller = new AbortController();
  const { signal } = controller;
  const path = location.pathname;
  currentProject = null;
  if (path === '/login') {
    if (session) return navigate('/projects', { replace: true });
    const error = new URLSearchParams(location.search).get('error');
    loginView(error ? new ApiError(400, error) : null);
    return;
  }
  const invitationMatch = path.match(/^\/invitations\/([A-Za-z0-9_-]{20,256})\/?$/);
  if (!session && !invitationMatch) return navigate('/login', { replace: true });
  loading(invitationMatch ? 'Проверка приглашения' : 'Загрузка');
  try {
    if (invitationMatch) await invitationView(invitationMatch[1], signal);
    else if (path === '/' || path === '/projects' || path === '/projects/') await projectsView(signal);
    else {
      const match = path.match(/^\/projects\/([0-9a-f-]{36})\/?$/i);
      if (!match) throw new ApiError(404, 'not_found');
      await projectView(match[1], signal);
    }
    if (sequence === routeSequence) announce('Страница загружена');
  } catch (error) {
    if (error.name === 'AbortError' || sequence !== routeSequence) return;
    if (error.status === 401) {
      session = null;
      loginView(error, invitationMatch ? path : '/projects');
    } else {
      app.innerHTML = session ? shell(errorState(error)) : `<main id="main" class="public-error">${errorState(error)}</main>`;
    }
  }
}

function closeDialog() {
  if (dialog.open) dialog.close();
  taskInDialog = null;
}

function recoverExpiredSession(error) {
  if (error.status !== 401) return false;
  const returnPath = location.pathname.replace(/\/$/, '') || '/projects';
  closeDialog();
  api.clearSession();
  session = null;
  currentProject = null;
  createdInvite = null;
  loginView(error, returnPath);
  return true;
}

function showDialog(content, { wide = false } = {}) {
  lastDialogTrigger = document.activeElement;
  dialog.className = wide ? 'wide-dialog' : '';
  dialog.innerHTML = content;
  if (!dialog.open) dialog.showModal();
}

const dialogHeader = title => `<header class="dialog-header"><h2 id="dialog-title">${esc(title)}</h2><button class="icon-button" type="button" data-action="close-dialog" aria-label="Закрыть">${icon('close')}</button></header>`;
const inputField = (name, label, { type = 'text', placeholder = '', max = 10000, rows = null } = {}) => `<div class="form-field"><label for="field-${name}" class="form-label">${esc(label)} <span aria-hidden="true">*</span></label>${rows ? `<textarea class="form-control" id="field-${name}" name="${name}" rows="${rows}" maxlength="${max}" required placeholder="${esc(placeholder)}"></textarea>` : `<input class="form-control" id="field-${name}" name="${name}" type="${type}" ${type === 'text' ? `maxlength="${max}"` : ''} required ${type === 'text' ? `placeholder="${esc(placeholder)}"` : ''}>`}<span class="field-error" id="error-${name}"></span></div>`;

function createForm(kind) {
  const project = kind === 'project';
  showDialog(`${dialogHeader(project ? 'Новый проект' : 'Новая задача')}<form id="create-form" data-kind="${kind}" novalidate><div class="dialog-body">${inputField('title', 'Название', { max: 200, placeholder: project ? 'Например, сайт студенческого клуба' : 'Например, подготовить макет страницы входа' })}${inputField('description', 'Описание', { rows: 3, placeholder: project ? 'Основные требования к проекту' : 'Требования к задаче' })}${project ? inputField('goal', 'Цель', { rows: 2, placeholder: 'Для чего нужен проект' }) : ''}${inputField('expected_result', 'Ожидаемый результат', { rows: 2, placeholder: 'Какой результат нужно получить' })}<div class="form-grid">${inputField('due_at', 'Срок', { type: 'datetime-local' })}${!project ? `<div class="form-field"><label class="form-label" for="field-priority">Приоритет <span aria-hidden="true">*</span></label><select class="form-select" id="field-priority" name="priority"><option value="low">Низкий</option><option value="medium" selected>Средний</option><option value="high">Высокий</option></select></div>` : ''}</div><p class="field-hint">Время в часовом поясе устройства</p><div class="form-error" role="alert"></div></div><footer class="dialog-footer"><button class="btn btn-outline-secondary" type="button" data-action="close-dialog">Отмена</button><button class="btn btn-primary" type="submit">${icon('plus')}${project ? 'Создать проект' : 'Создать задачу'}</button></footer></form>`, { wide: true });
}

function taskPath(id = taskInDialog?.id) { return `/projects/${encodeURIComponent(currentProject.id)}/tasks/${encodeURIComponent(id)}`; }

function taskDetailMarkup(task) {
  const assignee = members.find(member => member.id === task.assignee_member_id);
  const canAssign = currentProject.current_role === 'lead' && isOpen(currentProject) && ['todo', 'in_progress'].includes(task.status);
  const canStart = assignee?.user.id === session.user.id && ['student', 'lead'].includes(assignee.role) && task.status === 'todo' && isOpen(currentProject);
  const assigneeField = canAssign
    ? `<form id="assignee-form"><label class="form-label" for="assignee-select">Исполнитель</label><div class="assignment-row"><select class="form-select" id="assignee-select" name="assignee_member_id"><option value="">Не назначен</option>${members.filter(member => member.active && ['student', 'lead'].includes(member.role)).map(member => `<option value="${esc(member.id)}" ${member.id === task.assignee_member_id ? 'selected' : ''}>${esc(member.user.display_name)}${member.user.id === session.user.id ? ' (вы)' : ''}</option>`).join('')}</select><button class="btn btn-outline-primary" type="submit">Сохранить</button></div></form>`
    : `<div class="task-fact"><span>Исполнитель</span><strong>${assignee ? esc(assignee.user.display_name) : 'Не назначен'}</strong></div>`;
  return `${dialogHeader(task.title)}<div class="dialog-body task-detail"><div class="task-detail-tags"><span class="task-status status-${esc(task.status)}">${esc(statusNames[task.status])}</span><span class="priority priority-${esc(task.priority)}"><i></i>${esc(priorities[task.priority])} приоритет</span>${task.is_overdue ? '<span class="overdue">Просрочена</span>' : task.is_due_soon ? '<span class="due-soon">Срок приближается</span>' : ''}</div><div class="task-fact"><span>Срок</span><strong>${esc(date(task.due_at))}, ${esc(new Intl.DateTimeFormat('ru', { hour: '2-digit', minute: '2-digit' }).format(new Date(task.due_at)))}</strong></div>${assigneeField}<section><h3>Описание</h3><p class="preserve-lines">${esc(task.description)}</p></section><section><h3>Ожидаемый результат</h3><p class="preserve-lines">${esc(task.expected_result)}</p></section><div class="form-error" role="alert"></div><button class="btn btn-outline-secondary refresh-task" data-action="refresh-task" hidden>Обновить задачу</button>${isOpen(currentProject) && task.status === 'todo' && assignee && assignee.user.id !== session.user.id ? '<div class="task-state-note">Начать работу может только исполнитель</div>' : ''}</div><footer class="dialog-footer"><button class="btn btn-outline-secondary" data-action="close-dialog">Закрыть</button>${canStart ? `<button class="btn btn-primary" data-action="start-task">Начать работу${icon('arrow')}</button>` : ''}</footer>`;
}

async function openTask(id) {
  showDialog(`${dialogHeader('Задача')}<div class="dialog-loading" role="status"><span class="loader" aria-hidden="true"></span><p>Загрузка</p></div>`, { wide: true });
  try {
    const projectId = currentProject.id;
    const task = await api.request(taskPath(id));
    if (!dialog.open || currentProject?.id !== projectId) return;
    taskInDialog = task;
    dialog.innerHTML = taskDetailMarkup(task);
    dialog.querySelector('[data-action="close-dialog"]').focus();
  } catch (error) {
    if (recoverExpiredSession(error)) return;
    dialog.innerHTML = `${dialogHeader('Не удалось открыть задачу')}<div class="dialog-body"><div class="inline-error" role="alert">${esc(errorMessage(error))}</div></div><footer class="dialog-footer"><button class="btn btn-outline-secondary" data-action="close-dialog">Закрыть</button></footer>`;
  }
}

function showFormError(container, error) {
  const region = container.querySelector('.form-error');
  if (region) region.innerHTML = `<div class="inline-error">${esc(errorMessage(error))}</div>`;
  if ([409, 412, 428].includes(error.status)) container.querySelector('.refresh-task')?.removeAttribute('hidden');
  region?.scrollIntoView({ block: 'nearest' });
}

function validatedPayload(form) {
  let valid = true;
  const payload = {};
  form.querySelectorAll('[required]').forEach(field => {
    const value = field.value.trim();
    const badDate = field.type === 'datetime-local' && Number.isNaN(new Date(value).getTime());
    const message = !value ? 'Заполните поле' : badDate ? 'Укажите дату и время' : !field.checkValidity() ? 'Проверьте значение' : '';
    field.classList.toggle('is-invalid', !!message);
    field.setAttribute('aria-invalid', message ? 'true' : 'false');
    const error = form.querySelector(`#error-${field.name}`);
    if (error) { error.textContent = message; field.setAttribute('aria-describedby', error.id); }
    if (message) valid = false;
    else payload[field.name] = field.type === 'datetime-local' ? new Date(value).toISOString() : value;
  });
  if (form.elements.priority) payload.priority = form.elements.priority.value;
  if (!valid) form.querySelector('.is-invalid')?.focus();
  return valid ? payload : null;
}

async function submitCreate(form) {
  const payload = validatedPayload(form);
  if (!payload) return;
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  form.querySelector('.form-error').textContent = '';
  try {
    const project = form.dataset.kind === 'project';
    const result = await api.request(project ? '/projects' : `/projects/${encodeURIComponent(currentProject.id)}/tasks`, { method: 'POST', body: payload });
    closeDialog();
    toast(project ? 'Проект создан' : 'Задача создана');
    if (project) navigate(`/projects/${encodeURIComponent(result.id)}`);
    else { await renderRoute(); await openTask(result.id); }
  } catch (error) { if (!recoverExpiredSession(error)) showFormError(form, error); }
  finally { button.disabled = false; button.removeAttribute('aria-busy'); }
}

async function mutateTask(action, form = null) {
  if (!taskInDialog) return;
  const path = taskPath();
  const taskId = taskInDialog.id;
  const buttons = [...dialog.querySelectorAll('button, select')];
  buttons.forEach(button => { button.disabled = true; });
  dialog.setAttribute('aria-busy', 'true');
  try {
    const result = await api.request(`${path}/${action}`, {
      method: action === 'assignee' ? 'PUT' : 'POST',
      ...(action === 'assignee' ? { body: { assignee_member_id: form.elements.assignee_member_id.value || null } } : {}),
      versionPath: path,
    });
    taskInDialog = result;
    await renderRoute();
    await openTask(taskId);
    toast(action === 'start' ? 'Задача в работе' : result.assignee_member_id ? 'Исполнитель сохранён' : 'Исполнитель снят');
  } catch (error) { if (!recoverExpiredSession(error)) showFormError(dialog, error); }
  finally { buttons.forEach(button => { button.disabled = false; }); dialog.removeAttribute('aria-busy'); }
}

async function doAction(button) {
  const action = button.dataset.action;
  if (action === 'create-project') return createForm('project');
  if (action === 'create-task') return createForm('task');
  if (action === 'close-dialog') return closeDialog();
  if (action === 'open-task') return openTask(button.dataset.id);
  if (action === 'refresh-task') return openTask(taskInDialog.id);
  if (action === 'retry') return renderRoute();
  if (action === 'start-task') return mutateTask('start');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    if (action === 'logout') {
      await api.request('/auth/logout', { method: 'POST' });
      api.clearSession();
      session = null;
      createdInvite = null;
      navigate('/login', { replace: true });
    } else if (action === 'create-invite') {
      const result = await api.request(`/projects/${encodeURIComponent(currentProject.id)}/invitations`, { method: 'POST', body: { role: 'student' } });
      createdInvite = { projectId: currentProject.id, url: result.invite_url, expiresAt: result.invitation.expires_at };
      await renderRoute();
      announce('Ссылка создана. Скопируйте её');
    } else if (action === 'copy-invite') {
      try { await navigator.clipboard.writeText(createdInvite.url); toast('Ссылка скопирована'); }
      catch { const input = app.querySelector('.copy-row input'); input?.focus(); input?.select(); toast('Скопируйте выделенную ссылку вручную'); }
    } else if (action === 'revoke-invite') {
      await api.request(`/projects/${encodeURIComponent(currentProject.id)}/invitations/${encodeURIComponent(button.dataset.id)}`, { method: 'DELETE' });
      createdInvite = null;
      await renderRoute();
      toast('Приглашение отозвано');
    } else if (action === 'accept-invite') {
      const member = await api.request(`/invitations/${encodeURIComponent(button.dataset.token)}/accept`, { method: 'POST' });
      toast('Вы присоединились к проекту');
      navigate(`/projects/${encodeURIComponent(member.project_id)}`, { replace: true });
    }
  } catch (error) {
    if (action === 'accept-invite') document.querySelector('#invite-error').innerHTML = `<div class="inline-error">${esc(errorMessage(error))}</div>`;
    else toast(errorMessage(error));
    if (error.status === 401) { session = null; loginView(error, location.pathname.startsWith('/invitations/') ? location.pathname : '/projects'); }
  } finally { button.disabled = false; button.removeAttribute('aria-busy'); }
}

document.addEventListener('click', async event => {
  const loginLink = event.target.closest('a[data-login]');
  if (loginLink) {
    event.preventDefault();
    if (loginLink.getAttribute('aria-busy') === 'true') return;
    loginLink.setAttribute('aria-busy', 'true');
    try {
      const config = await api.request('/auth/configuration');
      if (!config.telegram_login_configured) throw new ApiError(503, 'auth_not_configured');
      location.assign(loginLink.getAttribute('href'));
    } catch (error) {
      const errorRegion = app.querySelector('.login-error');
      if (errorRegion) errorRegion.innerHTML = `<div class="inline-error">${esc(errorMessage(error))}</div>`;
      else toast(errorMessage(error));
    } finally { loginLink.removeAttribute('aria-busy'); }
    return;
  }
  const link = event.target.closest('a[data-nav]');
  if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); navigate(link.getAttribute('href')); return; }
  const button = event.target.closest('button[data-action]');
  if (button && !button.disabled) doAction(button).catch(() => toast('Не удалось выполнить действие. Обновите страницу'));
});
dialog.addEventListener('submit', event => {
  event.preventDefault();
  if (event.target.id === 'create-form') submitCreate(event.target);
  else if (event.target.id === 'assignee-form') mutateTask('assignee', event.target);
});
dialog.addEventListener('close', () => { taskInDialog = null; if (lastDialogTrigger?.isConnected) lastDialogTrigger.focus(); });
window.addEventListener('popstate', () => { closeDialog(); renderRoute(); });

async function start() {
  try { session = await api.session(); }
  catch (error) {
    if (error.status !== 401) {
      app.innerHTML = `<main id="main" class="public-error">${errorState(error)}</main>`;
      // A retry after a connection failure must read the session again.
      app.querySelector('[data-action="retry"]')?.addEventListener('click', event => { event.stopPropagation(); loading('Проверка входа'); start(); });
      return;
    }
  }
  await renderRoute();
}
start();
