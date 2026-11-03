"use strict";

const state = {
  csrf: "",
  username: "",
  pages: { users: 1, messages: 1, contacts: 1, audit: 1 },
  search: { users: "", messages: "", contacts: "" },
  filters: { userType: "", userStatus: "", messageType: "" },
  stats: null,
};

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);
const shortDate = (timestamp) => timestamp ? new Date(Number(timestamp) * 1000).toLocaleString(undefined, {
  month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
}) : "—";
const relativeDate = (timestamp) => {
  if (!timestamp) return "No activity";
  const minutes = Math.max(0, Math.floor((Date.now() - Number(timestamp) * 1000) / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
};
const formatBytes = (value) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
};

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET" && state.csrf) {
    headers.set("X-CSRF-Token", state.csrf);
  }
  const response = await fetch(`/admin/api${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }
  return payload;
}

function showToast(message, isError = false) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => toast.classList.remove("visible"), 3000);
}

function showLogin(configured = true) {
  state.csrf = "";
  document.getElementById("app-shell").hidden = true;
  document.getElementById("login-shell").hidden = false;
  document.getElementById("setup-notice").hidden = configured;
  document.getElementById("login-form").querySelectorAll("input, button").forEach((input) => {
    input.disabled = !configured;
  });
  document.getElementById("login-error").hidden = true;
}

function showApp(username) {
  state.username = username;
  state.csrf = state.csrf || "";
  document.getElementById("login-shell").hidden = true;
  document.getElementById("app-shell").hidden = false;
  document.getElementById("admin-name").textContent = username;
  document.getElementById("admin-avatar").textContent = username.slice(0, 1).toUpperCase();
  document.getElementById("topbar-identity").textContent = username;
  document.getElementById("greeting-name").textContent = username;
  document.getElementById("today-date").textContent = new Intl.DateTimeFormat(undefined, {
    weekday: "long", month: "short", day: "numeric", year: "numeric",
  }).format(new Date());
  loadOverview().catch((error) => showToast(error.message, true));
}

function setView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  document.querySelectorAll(".nav-link").forEach((link) => link.classList.toggle("active", link.dataset.view === name));
  document.getElementById("breadcrumb").textContent = name.charAt(0).toUpperCase() + name.slice(1);
  document.getElementById("sidebar").classList.remove("open");
  const loaders = {
    overview: loadOverview,
    users: loadUsers,
    messages: loadMessages,
    contacts: loadContacts,
    database: loadDatabase,
    audit: loadAudit,
  };
  loaders[name]().catch((error) => showToast(error.message, true));
}

function renderStats(stats) {
  const cards = [
    ["Members", stats.total_users, "♙", "", "Person accounts in this workspace"],
    ["Groups", stats.groups, "◇", "blue", "Group accounts"],
    ["Messages", stats.total_messages, "▤", "gold", `${stats.messages_today} in the last 24 hours`],
    ["Contacts", stats.total_contacts, "↔", "blue", "Connection records"],
    ["Unread messages", stats.unread_messages, "◉", "gold", "Across all conversations"],
    ["Blocked accounts", stats.blocked_users, "⊘", "red", "Access denied to app endpoints"],
  ];
  document.getElementById("stats-grid").innerHTML = cards.map(([label, value, icon, tone, foot]) => `
    <article class="stat-card"><div class="stat-top"><span>${label}</span><span class="stat-icon ${tone}">${icon}</span></div><strong class="stat-value">${Number(value).toLocaleString()}</strong><div class="stat-foot">${escapeHtml(foot)}</div></article>
  `).join("");
  document.getElementById("user-count").textContent = (
    Number(stats.total_users) + Number(stats.groups)
  ).toLocaleString();
}

async function loadOverview() {
  const [stats, database, audit] = await Promise.all([
    request("/stats"),
    request("/database"),
    request("/audit?page=1&page_size=5"),
  ]);
  state.stats = stats;
  renderStats(stats);
  document.getElementById("database-meta").innerHTML = `
    <div><strong>${escapeHtml(formatBytes(database.size_bytes))}</strong><span>Database size</span></div>
    <div><strong>${escapeHtml(database.sqlite_version)}</strong><span>SQLite version</span></div>
    <div><strong>${database.tables.length}</strong><span>Tables</span></div>`;
  document.getElementById("table-summary").innerHTML = database.tables
    .filter((table) => ["user", "message", "contact_registry"].includes(table.name))
    .map((table) => `<div class="table-summary-row"><span>${escapeHtml(table.name)}</span><span>${Number(table.rows).toLocaleString()} rows</span></div>`)
    .join("");
  document.getElementById("recent-audit").innerHTML = audit.items.length
    ? audit.items.map(renderCompactAudit).join("")
    : '<div class="empty-state">No administrative activity yet.</div>';
}

function renderCompactAudit(item) {
  return `<div class="audit-row"><span class="audit-icon">${auditIcon(item.action)}</span><span class="audit-main"><strong>${escapeHtml(item.action.replaceAll(".", " "))}</strong><small>${escapeHtml(item.actor)}</small></span><span class="audit-record">${escapeHtml(item.entity)} · ${escapeHtml(item.entity_id)}</span><span class="audit-time">${escapeHtml(relativeDate(item.created_at))}</span></div>`;
}

function auditIcon(action) {
  if (action.includes("deleted")) return "−";
  if (action.includes("blocked")) return "⊘";
  if (action.includes("login")) return "↗";
  if (action.includes("created")) return "+";
  return "✎";
}

function renderPagination(targetId, result, pageKey, loader) {
  const target = document.getElementById(targetId);
  const start = result.total ? (result.page - 1) * result.page_size + 1 : 0;
  const end = Math.min(result.page * result.page_size, result.total);
  const pageButtons = [];
  const first = Math.max(1, result.page - 2);
  const last = Math.min(result.pages, result.page + 2);
  for (let page = first; page <= last; page += 1) {
    pageButtons.push(`<button class="${page === result.page ? "current" : ""}" data-page="${page}">${page}</button>`);
  }
  target.innerHTML = `<span>Showing ${start}–${end} of ${Number(result.total).toLocaleString()}</span><div class="page-controls"><button data-page="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""} aria-label="Previous page">‹</button>${pageButtons.join("")}<button data-page="${result.page + 1}" ${result.page >= result.pages ? "disabled" : ""} aria-label="Next page">›</button></div>`;
  target.querySelectorAll("button[data-page]").forEach((button) => {
    button.addEventListener("click", () => {
      state.pages[pageKey] = Number(button.dataset.page);
      loader().catch((error) => showToast(error.message, true));
    });
  });
}

async function loadUsers() {
  const params = new URLSearchParams({
    page: String(state.pages.users),
    page_size: "20",
  });
  if (state.search.users) params.set("search", state.search.users);
  if (state.filters.userType) params.set("user_type", state.filters.userType);
  if (state.filters.userStatus) params.set("status", state.filters.userStatus);
  const result = await request(`/users?${params}`);
  document.getElementById("users-result-count").textContent = `${result.total.toLocaleString()} accounts`;
  document.getElementById("users-table").innerHTML = result.items.length
    ? result.items.map(renderUser).join("")
    : '<tr><td class="empty-state" colspan="6">No accounts match your filters.</td></tr>';
  renderPagination("users-pagination", result, "users", loadUsers);
}

function renderUser(user) {
  const nickname = escapeHtml(user.user_nickname);
  const type = Number(user.user_type) === 2 ? "Group" : "Person";
  const blocked = Boolean(user.is_blocked);
  const initials = nickname.slice(0, 2).toUpperCase();
  return `<tr>
    <td><div class="account-cell"><span class="account-avatar">${initials}</span><span><strong>${nickname}</strong><small>Account #${user.id}</small></span></div></td>
    <td><span class="type-chip ${type === "Group" ? "group" : ""}"><i></i>${type}</span></td>
    <td>${Number(user.message_count).toLocaleString()}</td>
    <td>${escapeHtml(relativeDate(user.last_active))}</td>
    <td><span class="status-chip ${blocked ? "blocked" : ""}"><i></i>${blocked ? "Blocked" : "Active"}</span></td>
    <td><div class="action-cell"><button class="row-action" data-action="edit-user" data-id="${user.id}" data-type="${user.user_type}" data-blocked="${blocked}">Edit</button><button class="row-action" data-action="toggle-user" data-id="${user.id}" data-blocked="${blocked}">${blocked ? "Unblock" : "Block"}</button><button class="row-action danger" data-action="delete-user" data-id="${user.id}" data-name="${nickname}">Delete</button></div></td>
  </tr>`;
}

async function loadMessages() {
  const params = new URLSearchParams({ page: String(state.pages.messages), page_size: "20" });
  if (state.search.messages) params.set("search", state.search.messages);
  if (state.filters.messageType !== "") params.set("message_type", state.filters.messageType);
  const result = await request(`/messages?${params}`);
  document.getElementById("messages-result-count").textContent = `${result.total.toLocaleString()} messages`;
  document.getElementById("messages-table").innerHTML = result.items.length
    ? result.items.map((message) => `<tr>
      <td><span class="message-content" title="${escapeHtml(message.message_content)}">${escapeHtml(message.message_content)}</span><small class="message-caption">Message #${message.id}</small></td>
      <td>${escapeHtml(message.sender_nickname)}</td><td>${escapeHtml(message.recipient_nickname)}</td>
      <td>${Number(message.message_type) === 0 ? "Text" : `Media (${message.message_type})`}</td>
      <td>${escapeHtml(shortDate(message.timestamp_unix))}</td>
      <td><div class="action-cell"><button class="row-action danger" data-action="delete-message" data-id="${message.id}">Delete</button></div></td>
    </tr>`).join("")
    : '<tr><td class="empty-state" colspan="6">No messages match your search.</td></tr>';
  renderPagination("messages-pagination", result, "messages", loadMessages);
}

async function loadContacts() {
  const params = new URLSearchParams({ page: String(state.pages.contacts), page_size: "20" });
  if (state.search.contacts) params.set("search", state.search.contacts);
  const result = await request(`/contacts?${params}`);
  document.getElementById("contacts-result-count").textContent = `${result.total.toLocaleString()} contacts`;
  document.getElementById("contacts-table").innerHTML = result.items.length
    ? result.items.map((contact) => `<tr><td>${escapeHtml(contact.sender_nickname)}</td><td>${escapeHtml(contact.recipient_nickname)}</td><td>${Number(contact.unread_messages_count).toLocaleString()}</td><td><div class="action-cell"><button class="row-action danger" data-action="delete-contact" data-id="${contact.id}">Remove</button></div></td></tr>`).join("")
    : '<tr><td class="empty-state" colspan="4">No contacts match your search.</td></tr>';
  renderPagination("contacts-pagination", result, "contacts", loadContacts);
}

async function loadDatabase() {
  const database = await request("/database");
  document.getElementById("database-cards").innerHTML = [
    ["Database file", database.path],
    ["Storage size", formatBytes(database.size_bytes)],
    ["SQLite version", database.sqlite_version],
  ].map(([label, value]) => `<article class="database-card"><span>${label}</span><strong>${escapeHtml(value)}</strong></article>`).join("");
  document.getElementById("schema-table").innerHTML = database.tables.map((table) => `
    <tr><td><strong>${escapeHtml(table.name)}</strong></td><td><span class="column-list">${table.columns.map((column) => `<code>${escapeHtml(column)}</code>`).join("")}</span></td><td>${Number(table.rows).toLocaleString()}</td></tr>
  `).join("");
}

async function loadAudit() {
  const result = await request(`/audit?page=${state.pages.audit}&page_size=25`);
  document.getElementById("audit-table").innerHTML = result.items.length
    ? result.items.map((item) => `<tr><td>${escapeHtml(item.actor)}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.entity)} #${escapeHtml(item.entity_id)}</td><td>${escapeHtml(JSON.stringify(item.details))}</td><td>${escapeHtml(shortDate(item.created_at))}</td></tr>`).join("")
    : '<tr><td class="empty-state" colspan="5">No administrative activity has been recorded.</td></tr>';
  renderPagination("audit-pagination", result, "audit", loadAudit);
}

function openCreateUser() {
  const form = document.getElementById("user-form");
  form.reset();
  form.elements.user_id.value = "";
  form.elements.user_type.disabled = false;
  document.getElementById("user-dialog-kicker").textContent = "NEW ACCOUNT";
  document.getElementById("user-dialog-title").textContent = "Create user";
  document.getElementById("user-dialog-description").textContent = "Add a person or group to the workspace.";
  document.getElementById("nickname-field").hidden = false;
  document.getElementById("blocked-field").hidden = true;
  document.getElementById("save-user-button").textContent = "Create user";
  document.getElementById("user-dialog").showModal();
  form.elements.user_nickname.focus();
}

function openEditUser(button) {
  const form = document.getElementById("user-form");
  form.reset();
  form.elements.user_id.value = button.dataset.id;
  form.elements.user_type.value = button.dataset.type;
  form.elements.user_type.disabled = false;
  form.elements.is_blocked.checked = button.dataset.blocked === "true";
  document.getElementById("user-dialog-kicker").textContent = "ACCOUNT SETTINGS";
  document.getElementById("user-dialog-title").textContent = "Edit account";
  document.getElementById("user-dialog-description").textContent = "Update the account type or access status.";
  document.getElementById("nickname-field").hidden = true;
  document.getElementById("blocked-field").hidden = false;
  document.getElementById("save-user-button").textContent = "Save changes";
  document.getElementById("user-dialog").showModal();
  form.elements.user_type.focus();
}

async function handleTableAction(button) {
  const { action, id, blocked, name } = button.dataset;
  if (action === "edit-user") {
    openEditUser(button);
    return;
  }
  if (action === "toggle-user") {
    const nextBlocked = blocked !== "true";
    const confirmed = window.confirm(`${nextBlocked ? "Block" : "Unblock"} this account?`);
    if (!confirmed) return;
    await request(`/users/${id}`, { method: "PATCH", body: JSON.stringify({ is_blocked: nextBlocked }) });
    showToast(`Account ${nextBlocked ? "blocked" : "unblocked"}.`);
    await Promise.all([loadUsers(), loadOverview()]);
    return;
  }
  const confirmation = action === "delete-user"
    ? `Permanently delete ${name} and all of their messages and contacts?`
    : action === "delete-message"
      ? "Permanently delete this message?"
      : "Remove this contact record?";
  if (!window.confirm(confirmation)) return;
  const path = action === "delete-user" ? `/users/${id}` : action === "delete-message" ? `/messages/${id}` : `/contacts/${id}`;
  await request(path, { method: "DELETE" });
  showToast(action === "delete-user" ? "User and related records deleted." : action === "delete-message" ? "Message deleted." : "Contact removed.");
  const reload = {
    "delete-user": () => Promise.all([loadUsers(), loadOverview()]),
    "delete-message": () => Promise.all([loadMessages(), loadOverview()]),
    "delete-contact": () => Promise.all([loadContacts(), loadOverview()]),
  };
  await reload[action]();
}

document.querySelectorAll(".nav-link").forEach((link) => link.addEventListener("click", () => setView(link.dataset.view)));
document.querySelectorAll("[data-go]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.go)));
document.getElementById("mobile-menu").addEventListener("click", () => document.getElementById("sidebar").classList.toggle("open"));
document.getElementById("create-user-button").addEventListener("click", openCreateUser);
document.getElementById("close-user-dialog").addEventListener("click", () => document.getElementById("user-dialog").close());
document.getElementById("cancel-user-dialog").addEventListener("click", () => document.getElementById("user-dialog").close());
document.getElementById("refresh-database").addEventListener("click", () => loadDatabase().then(() => showToast("Database information refreshed.")).catch((error) => showToast(error.message, true)));
document.getElementById("users-table").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (button) handleTableAction(button).catch((error) => showToast(error.message, true));
});
document.getElementById("messages-table").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (button) handleTableAction(button).catch((error) => showToast(error.message, true));
});
document.getElementById("contacts-table").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (button) handleTableAction(button).catch((error) => showToast(error.message, true));
});
document.getElementById("user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const userId = form.elements.user_id.value;
  const submitButton = document.getElementById("save-user-button");
  submitButton.disabled = true;
  try {
    if (userId) {
      await request(`/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({
          user_type: Number(form.elements.user_type.value),
          is_blocked: form.elements.is_blocked.checked,
        }),
      });
      showToast("Account updated.");
    } else {
      await request("/users", {
        method: "POST",
        body: JSON.stringify({
          user_nickname: form.elements.user_nickname.value,
          user_type: Number(form.elements.user_type.value),
        }),
      });
      showToast("User created. They can now register their device in the app.");
    }
    document.getElementById("user-dialog").close();
    state.pages.users = 1;
    await Promise.all([loadUsers(), loadOverview()]);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    submitButton.disabled = false;
  }
});

for (const [elementId, key] of [["user-search", "users"], ["message-search", "messages"], ["contact-search", "contacts"]]) {
  document.getElementById(elementId).addEventListener("input", (event) => {
    state.search[key] = event.target.value;
    state.pages[key] = 1;
    clearTimeout(document.getElementById(elementId).searchTimeout);
    document.getElementById(elementId).searchTimeout = setTimeout(() => {
      const loaders = { users: loadUsers, messages: loadMessages, contacts: loadContacts };
      loaders[key]().catch((error) => showToast(error.message, true));
    }, 250);
  });
}
document.getElementById("user-type-filter").addEventListener("change", (event) => {
  state.filters.userType = event.target.value;
  state.pages.users = 1;
  loadUsers().catch((error) => showToast(error.message, true));
});
document.getElementById("user-status-filter").addEventListener("change", (event) => {
  state.filters.userStatus = event.target.value;
  state.pages.users = 1;
  loadUsers().catch((error) => showToast(error.message, true));
});
document.getElementById("message-type-filter").addEventListener("change", (event) => {
  state.filters.messageType = event.target.value;
  state.pages.messages = 1;
  loadMessages().catch((error) => showToast(error.message, true));
});
document.getElementById("logout-button").addEventListener("click", async () => {
  try {
    await request("/logout", { method: "POST" });
  } catch (error) {
    if (state.csrf) showToast(error.message, true);
  }
  showLogin();
});
document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const errorBox = document.getElementById("login-error");
  const submitButton = form.querySelector('button[type="submit"]');
  errorBox.hidden = true;
  submitButton.disabled = true;
  try {
    const response = await request("/login", {
      method: "POST",
      body: JSON.stringify({
        username: form.elements.username.value,
        password: form.elements.password.value,
      }),
    });
    state.csrf = response.csrf_token;
    form.elements.password.value = "";
    showApp(response.username);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    submitButton.disabled = false;
  }
});

async function initialize() {
  const session = await request("/session");
  if (!session.authenticated) {
    showLogin(session.configured);
    return;
  }
  state.csrf = session.csrf_token;
  showApp(session.username);
}

initialize().catch((error) => {
  showLogin(false);
  const notice = document.getElementById("setup-notice");
  notice.textContent = `Unable to reach the admin service: ${error.message}`;
  notice.hidden = false;
});
