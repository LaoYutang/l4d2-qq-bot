// Adapted from LaoYutang/l4d2-with-astrbot pages/settings/app.js (AGPL-3.0).
const PASSWORD_KEY = "l4d2-config-password";
let password = "";
try { password = localStorage.getItem(PASSWORD_KEY) || ""; } catch {}

async function api(method, body) {
  const response = await fetch("/api/config", {
    method,
    cache: "no-store",
    headers: { "X-Config-Password": password, ...(body ? { "Content-Type": "application/json" } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(15000),
  });
  const result = await response.json();
  if (response.status === 401) {
    try { localStorage.removeItem(PASSWORD_KEY); } catch {}
    showLogin("密码错误");
  }
  if (!response.ok) {
    const errors = result.errors?.map(error => error.field + "：" + error.message).join("\n");
    throw new Error(errors || result.error || "请求失败");
  }
  return result;
}

function showLogin(message = "") {
  document.getElementById("loginPanel").hidden = false;
  document.querySelector(".page-shell").hidden = true;
  document.getElementById("loginError").textContent = message;
  document.getElementById("passwordInput").focus();
}


const elements = {
  alertBox: document.getElementById("alertBox"),
  connect_base_url: document.getElementById("connect_base_url"),
  map_name_url: document.getElementById("map_name_url"),
  hhBotId: document.getElementById("hhBotId"),
  hhBotToken: document.getElementById("hhBotToken"),
  safe_mode: document.getElementById("safe_mode"),
  groupCount: document.getElementById("groupCount"),
  serverCount: document.getElementById("serverCount"),
  safe_modeState: document.getElementById("safe_modeState"),
  saveState: document.getElementById("saveState"),
  saveButton: document.getElementById("saveButton"),
  reloadButton: document.getElementById("reloadButton"),
  addGroupButton: document.getElementById("addGroupButton"),
  emptyAddGroupButton: document.getElementById("emptyAddGroupButton"),
  groupsContainer: document.getElementById("groupsContainer"),
  emptyGroups: document.getElementById("emptyGroups"),
  loadingLayer: document.getElementById("loadingLayer"),
  confirmLayer: document.getElementById("confirmLayer"),
  confirmMessage: document.getElementById("confirmMessage"),
  confirmCancel: document.getElementById("confirmCancel"),
  confirmAccept: document.getElementById("confirmAccept"),
  toast: document.getElementById("toast"),
};

let state = createEmptyConfig();
let revision = "";
let baseline = "";
let busy = true;
let loaded = false;
let openGroups = new Set();
let openServers = new Set();
let toastTimer = null;
let confirmResolver = null;

function createEmptyConfig() {
  return {
    connect_base_url: "",
    map_name_url: "",
    safe_mode: false,
    heybox_bot_id: "",
    heybox_token: "",
    query: { timeout_seconds: 3, concurrency: 4, allow_private_connect: false },
    integrations: { timeout_seconds: 5, concurrency: 4, workshop_api_url: "", workshop_max_items: 20 },
    groups: [],
  };
}

function createEmptyGroup() {
  return {
    group_openid: "",
    name: "",
    hh_room_id: "",
    admin_openids: [],
    servers: [createEmptyServer()],
  };
}

function createEmptyServer() {
  return {
    name: "",
    address: "",
    hh_channel_id: "",
    aliases: [],
    connect_url: "",
    rcon_password_env: "",
    panel_token_env: "",
    rcon_password: "",
    panel_url: "",
    panel_token: "",
  };
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function asText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "";
}

function normalizeConfig(rawConfig) {
  const source = isPlainObject(rawConfig) ? rawConfig : {};
  const groups = Array.isArray(source.groups) ? source.groups : [];

  return {
    ...createEmptyConfig(),
    ...source,
    connect_base_url: asText(source.connect_base_url),
    map_name_url: asText(source.map_name_url),
    safe_mode: source.safe_mode === true,
    heybox_bot_id: asText(source.heybox_bot_id),
    heybox_token: asText(source.heybox_token),
    groups: groups.filter(isPlainObject).map((group) => ({
      ...group,
      group_openid: asText(group.group_openid),
      name: asText(group.name),
      hh_room_id: asText(group.hh_room_id),
      admin_openids: Array.isArray(group.admin_openids)
        ? [...new Set(group.admin_openids.map(asText).filter(Boolean))]
        : [],
      servers: Array.isArray(group.servers)
        ? group.servers.filter(isPlainObject).map((server) => ({
            ...server,
            aliases: Array.isArray(server.aliases) ? server.aliases : [],
            connect_url: asText(server.connect_url),
            rcon_password_env: asText(server.rcon_password_env),
            panel_token_env: asText(server.panel_token_env),
            name: asText(server.name),
            address: asText(server.address),
            hh_channel_id: asText(server.hh_channel_id),
            rcon_password: asText(server.rcon_password),
            panel_url: asText(server.panel_url),
            panel_token: asText(server.panel_token),
          }))
        : [],
    })),
  };
}

function snapshot(value) {
  return JSON.stringify(value);
}

function isDirty() {
  return snapshot(state) !== baseline;
}

function setTheme(context) {
  document.documentElement.dataset.theme = context?.isDark ? "dark" : "light";
  document.documentElement.lang = context?.locale || "zh-CN";
}

function setBusy(nextBusy, message = "正在读取配置…") {
  busy = nextBusy;
  elements.loadingLayer.hidden = !nextBusy;
  const messageNode = elements.loadingLayer.querySelector("span");
  if (messageNode) messageNode.textContent = message;
  elements.reloadButton.disabled = nextBusy;
  elements.saveButton.disabled = nextBusy || !loaded || !isDirty();
  elements.addGroupButton.disabled = nextBusy;
  elements.emptyAddGroupButton.disabled = nextBusy;
}

function refreshStatus(mode = null) {
  const dirty = isDirty();
  elements.saveState.classList.remove("is-saved", "is-dirty", "is-error");

  if (mode === "error") {
    elements.saveState.classList.add("is-error");
    elements.saveState.querySelector(".status-label").textContent = "需要处理";
  } else if (dirty) {
    elements.saveState.classList.add("is-dirty");
    elements.saveState.querySelector(".status-label").textContent = "有未保存修改";
  } else {
    elements.saveState.classList.add("is-saved");
    elements.saveState.querySelector(".status-label").textContent = "已同步";
  }

  elements.saveButton.disabled = busy || !loaded || !dirty;
  renderStats();
}

function markDirty() {
  hideAlert();
  refreshStatus();
}

function renderStats() {
  const groups = state.groups || [];
  const servers = groups.reduce(
    (total, group) => total + (Array.isArray(group.servers) ? group.servers.length : 0),
    0,
  );
  elements.groupCount.textContent = String(groups.length);
  elements.serverCount.textContent = String(servers);
  elements.safe_modeState.textContent = state.safe_mode ? "开启" : "关闭";
  const switchText = elements.safe_mode.closest(".switch")?.querySelector(".switch-text");
  if (switchText) switchText.textContent = state.safe_mode ? "开启" : "关闭";
}

function showAlert(messages) {
  const list = Array.isArray(messages) ? messages : [messages];
  elements.alertBox.replaceChildren();

  if (list.length === 1) {
    elements.alertBox.textContent = list[0];
  } else {
    const title = document.createElement("strong");
    title.textContent = `保存前请处理以下 ${list.length} 个问题：`;
    const ul = document.createElement("ul");
    for (const message of list) {
      const li = document.createElement("li");
      li.textContent = message;
      ul.append(li);
    }
    elements.alertBox.append(title, ul);
  }

  elements.alertBox.hidden = false;
  elements.alertBox.scrollIntoView({ behavior: "smooth", block: "center" });
}

function hideAlert() {
  elements.alertBox.hidden = true;
  elements.alertBox.replaceChildren();
  document.querySelectorAll('[aria-invalid="true"]').forEach((input) => {
    input.removeAttribute("aria-invalid");
  });
}

function showToast(message, type = "success") {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", type === "error");
  elements.toast.classList.add("is-visible");
  toastTimer = window.setTimeout(() => {
    elements.toast.classList.remove("is-visible");
  }, 3200);
}

function bindGlobalControls() {
  const textBindings = [
    [elements.connect_base_url, "connect_base_url"],
    [elements.map_name_url, "map_name_url"],
    [elements.hhBotId, "heybox_bot_id"],
    [elements.hhBotToken, "heybox_token"],
  ];

  for (const input of document.querySelectorAll("[data-global-path]")) {
    input.addEventListener("input", () => {
      const [section, key] = input.dataset.globalPath.split(".");
      state[section][key] = input.type === "checkbox" ? input.checked :
        input.type === "number" ? Number(input.value) : input.value;
      markDirty();
    });
  }
  for (const [input, key] of textBindings) {
    input.addEventListener("input", () => {
      state[key] = input.value;
      markDirty();
    });
  }

  elements.safe_mode.addEventListener("change", () => {
    state.safe_mode = elements.safe_mode.checked;
    markDirty();
  });

  document.querySelectorAll("[data-secret-target]").forEach((button) => {
    button.addEventListener("click", () => toggleSecret(button));
  });

  elements.addGroupButton.addEventListener("click", addGroup);
  elements.emptyAddGroupButton.addEventListener("click", addGroup);
  elements.saveButton.addEventListener("click", saveConfig);
  elements.reloadButton.addEventListener("click", () => loadConfig(false));
  elements.confirmCancel.addEventListener("click", () => closeConfirmation(false));
  elements.confirmAccept.addEventListener("click", () => closeConfirmation(true));
  elements.confirmLayer.addEventListener("click", (event) => {
    if (event.target === elements.confirmLayer) closeConfirmation(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !elements.confirmLayer.hidden) {
      closeConfirmation(false);
    }
  });
}

function renderGlobalControls() {
  elements.connect_base_url.value = state.connect_base_url;
  elements.map_name_url.value = state.map_name_url;
  elements.hhBotId.value = state.heybox_bot_id;
  elements.hhBotToken.value = state.heybox_token;
  elements.safe_mode.checked = state.safe_mode;
  for (const input of document.querySelectorAll("[data-global-path]")) {
    const [section, key] = input.dataset.globalPath.split(".");
    if (input.type === "checkbox") input.checked = state[section][key];
    else input.value = state[section][key];
  }
  renderStats();
}

function toggleSecret(button) {
  const input = document.getElementById(button.dataset.secretTarget);
  if (!input) return;
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  button.textContent = showing ? "显示" : "隐藏";
}

function addGroup() {
  state.groups.push(createEmptyGroup());
  const newIndex = state.groups.length - 1;
  openGroups.add(newIndex);
  renderGroups();
  markDirty();
  requestAnimationFrame(() => {
    document.querySelector(`[data-group-index="${newIndex}"] input[data-field="group_openid"]`)?.focus();
  });
}

async function removeGroup(groupIndex) {
  const group = state.groups[groupIndex];
  const label = group.name || group.group_openid || `#${groupIndex + 1}`;
  const confirmed = await requestConfirmation(
    `确定删除群组 ${label} 及其全部服务器配置吗？`,
    "确认删除群组",
  );
  if (!confirmed) return;
  state.groups.splice(groupIndex, 1);
  forgetGroupOpenState(groupIndex);
  renderGroups();
  markDirty();
}

function addServer(groupIndex) {
  const group = state.groups[groupIndex];
  group.servers.push(createEmptyServer());
  const newServerIndex = group.servers.length - 1;
  openGroups.add(groupIndex);
  openServers.add(serverKey(groupIndex, newServerIndex));
  renderGroups();
  markDirty();
  requestAnimationFrame(() => {
    document.querySelector(
      `[data-group-index="${groupIndex}"] [data-server-index="${newServerIndex}"] input[data-field="name"]`,
    )?.focus();
  });
}

async function removeServer(groupIndex, serverIndex) {
  const server = state.groups[groupIndex].servers[serverIndex];
  const label = server.name || `#${serverIndex + 1}`;
  const confirmed = await requestConfirmation(
    `确定删除服务器 ${label} 吗？此操作会在保存配置后生效。`,
    "确认删除服务器",
  );
  if (!confirmed) return;
  state.groups[groupIndex].servers.splice(serverIndex, 1);
  shiftServerOpenState(groupIndex, serverIndex);
  renderGroups();
  markDirty();
}

function moveGroup(groupIndex, offset) {
  const groups = state.groups;
  const targetIndex = groupIndex + offset;
  if (targetIndex < 0 || targetIndex >= groups.length) return;

  [groups[groupIndex], groups[targetIndex]] = [groups[targetIndex], groups[groupIndex]];
  swapMembership(openGroups, groupIndex, targetIndex);
  remapGroupInServerOpenState(groupIndex, targetIndex);
  renderGroups();
  markDirty();
  focusMoveButton(`[data-group-index="${targetIndex}"] > summary`, offset);
}

function moveServer(groupIndex, serverIndex, offset) {
  const servers = state.groups[groupIndex]?.servers;
  if (!servers) return;
  const targetIndex = serverIndex + offset;
  if (targetIndex < 0 || targetIndex >= servers.length) return;

  [servers[serverIndex], servers[targetIndex]] = [servers[targetIndex], servers[serverIndex]];
  openGroups.add(groupIndex);
  swapMembership(
    openServers,
    serverKey(groupIndex, serverIndex),
    serverKey(groupIndex, targetIndex),
  );
  renderGroups();
  markDirty();
  focusMoveButton(
    `[data-group-index="${groupIndex}"] [data-server-index="${targetIndex}"]`,
    offset,
  );
}

function serverKey(groupIndex, serverIndex) {
  return `${groupIndex}:${serverIndex}`;
}

// 展开状态都是按位置记录的：卡片互换位置时状态跟着互换，
// 删除时被删的项丢弃、它后面的项前移，否则展开的会是另一张卡片。
function swapMembership(set, keyA, keyB) {
  const aIn = set.has(keyA);
  const bIn = set.has(keyB);
  set.delete(keyA);
  set.delete(keyB);
  if (bIn) set.add(keyA);
  if (aIn) set.add(keyB);
}

function shiftServerOpenState(groupIndex, removedIndex) {
  openServers = new Set(
    [...openServers]
      .map((key) => {
        const [groupPart, serverPart] = key.split(":");
        return { group: Number(groupPart), server: Number(serverPart) };
      })
      .filter((entry) => entry.group !== groupIndex || entry.server !== removedIndex)
      .map((entry) =>
        serverKey(
          entry.group,
          entry.group === groupIndex && entry.server > removedIndex
            ? entry.server - 1
            : entry.server,
        ),
      ),
  );
}

function remapGroupInServerOpenState(fromGroup, toGroup) {
  openServers = new Set(
    [...openServers].map((key) => {
      const [groupPart, serverPart] = key.split(":");
      const group = Number(groupPart);
      if (group !== fromGroup && group !== toGroup) return key;
      return serverKey(group === fromGroup ? toGroup : fromGroup, Number(serverPart));
    }),
  );
}

function forgetGroupOpenState(removedGroup) {
  openGroups = new Set(
    [...openGroups]
      .filter((index) => index !== removedGroup)
      .map((index) => (index > removedGroup ? index - 1 : index)),
  );
  openServers = new Set(
    [...openServers]
      .map((key) => {
        const [groupPart, serverPart] = key.split(":");
        return { group: Number(groupPart), server: Number(serverPart) };
      })
      .filter((entry) => entry.group !== removedGroup)
      .map((entry) =>
        serverKey(entry.group > removedGroup ? entry.group - 1 : entry.group, entry.server),
      ),
  );
}

function focusMoveButton(scopeSelector, offset) {
  const upSelector = `${scopeSelector} .move-up:not([disabled])`;
  const downSelector = `${scopeSelector} .move-down:not([disabled])`;
  const preferred = offset < 0 ? upSelector : downSelector;
  const alternative = offset < 0 ? downSelector : upSelector;
  requestAnimationFrame(() => {
    const button =
      document.querySelector(preferred) || document.querySelector(alternative);
    button?.focus();
  });
}

function renderGroups() {
  elements.groupsContainer.replaceChildren();
  const groups = state.groups || [];
  elements.emptyGroups.hidden = groups.length !== 0;
  elements.groupsContainer.hidden = groups.length === 0;

  groups.forEach((group, groupIndex) => {
    elements.groupsContainer.append(createGroupCard(group, groupIndex));
  });
  renderStats();
}

function createGroupCard(group, groupIndex) {
  const details = document.createElement("details");
  details.className = "group-card";
  details.dataset.groupIndex = String(groupIndex);
  details.open = openGroups.has(groupIndex);
  if (details.open) openGroups.add(groupIndex);

  details.innerHTML = `
    <summary class="group-summary">
      <div class="group-summary-main">
        <span class="disclosure" aria-hidden="true">›</span>
        <span class="group-order">${String(groupIndex + 1).padStart(2, "0")}</span>
        <span class="group-title-wrap">
          <span class="group-title"></span>
          <span class="group-meta"></span>
        </span>
      </div>
      <div class="group-summary-actions">
        <span class="server-count-pill"></span>
        <span class="order-buttons">
          <button class="icon-button move-up move-group" type="button" aria-label="上移群组" title="上移群组">↑</button>
          <button class="icon-button move-down move-group" type="button" aria-label="下移群组" title="下移群组">↓</button>
        </span>
        <button class="icon-button remove-group" type="button" aria-label="删除群组" title="删除群组">×</button>
      </div>
    </summary>
    <div class="group-body">
      <div class="form-grid group-fields">
        <label class="field">
          <span class="field-label">群组名称</span>
          <span class="field-hint">仅用于配置页展示，方便识别群组，可留空</span>
          <input type="text" data-field="name" autocomplete="off" placeholder="例如 主群" />
        </label>
        <label class="field">
          <span class="field-label">群组 ID</span>
          <span class="field-hint">在群中发送“获取群ID”后复制结果</span>
          <input type="text" data-field="group_openid" autocomplete="off" placeholder="QQ群 OpenID" />
        </label>
        <label class="field">
          <span class="field-label">黑盒语音房间 ID</span>
          <span class="field-hint">该群组对应的语音房间，可留空</span>
          <input type="text" data-field="hh_room_id" autocomplete="off" placeholder="Room ID" />
        </label>
        <div class="admin-editor">
          <span class="field-label">管理员用户</span>
          <span class="field-hint">在群中发送“获取用户ID”后复制结果；支持回车或逗号批量添加。</span>
          <div class="tag-input-row">
            <input class="admin-input" type="text" autocomplete="off" placeholder="输入成员 OpenID" />
            <button class="button button-secondary button-small add-admin" type="button">添加</button>
          </div>
          <div class="tag-list" aria-label="管理员列表"></div>
        </div>
      </div>
      <div class="servers-block">
        <div class="server-list-heading">
          <div>
            <h3>服务器列表</h3>
            <p>服务器名用于查询指令匹配，地址支持 host 或 host:port。↑ ↓ 可调整顺序，该顺序就是列表与综合查询中的展示顺序。</p>
          </div>
          <button class="button button-secondary button-small add-server" type="button">＋ 添加服务器</button>
        </div>
        <div class="servers-list"></div>
      </div>
    </div>
  `;

  const title = details.querySelector(".group-title");
  const meta = details.querySelector(".group-meta");
  const serverCount = details.querySelector(".server-count-pill");
  const groupNameInput = details.querySelector('input[data-field="name"]');
  const groupIdInput = details.querySelector('input[data-field="group_openid"]');
  const roomIdInput = details.querySelector('input[data-field="hh_room_id"]');
  const adminInput = details.querySelector(".admin-input");
  const tagList = details.querySelector(".tag-list");
  const serversList = details.querySelector(".servers-list");

  function updateGroupSummary() {
    title.textContent =
      group.name || (group.group_openid ? `群组 ${group.group_openid}` : "未命名群组");
    meta.textContent = group.hh_room_id ? `黑盒房间 ${group.hh_room_id}` : "未关联黑盒语音房间";
    serverCount.textContent = `${group.servers.length} 台服务器`;
  }

  groupNameInput.value = group.name;
  groupNameInput.dataset.path = `groups.${groupIndex}.name`;
  groupIdInput.value = group.group_openid;
  groupIdInput.dataset.path = `groups.${groupIndex}.group_openid`;
  roomIdInput.value = group.hh_room_id;
  roomIdInput.dataset.path = `groups.${groupIndex}.hh_room_id`;
  updateGroupSummary();

  groupNameInput.addEventListener("input", () => {
    group.name = groupNameInput.value;
    updateGroupSummary();
    markDirty();
  });
  groupIdInput.addEventListener("input", () => {
    group.group_openid = groupIdInput.value;
    updateGroupSummary();
    markDirty();
  });
  roomIdInput.addEventListener("input", () => {
    group.hh_room_id = roomIdInput.value;
    updateGroupSummary();
    markDirty();
  });

  details.addEventListener("toggle", () => {
    if (details.open) openGroups.add(groupIndex);
    else openGroups.delete(groupIndex);
  });
  details.querySelector(".remove-group").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    removeGroup(groupIndex);
  });
  const moveUpButton = details.querySelector(".move-group.move-up");
  const moveDownButton = details.querySelector(".move-group.move-down");
  moveUpButton.disabled = groupIndex === 0;
  moveDownButton.disabled = groupIndex === state.groups.length - 1;
  for (const [button, offset] of [
    [moveUpButton, -1],
    [moveDownButton, 1],
  ]) {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      moveGroup(groupIndex, offset);
    });
  }
  details.querySelector(".add-server").addEventListener("click", () => addServer(groupIndex));

  function commitAdmins() {
    const values = adminInput.value
      .split(/[\s,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (values.length === 0) return;
    group.admin_openids = [...new Set([...group.admin_openids, ...values])];
    adminInput.value = "";
    renderAdminTags(tagList, group, groupIndex);
    markDirty();
  }

  details.querySelector(".add-admin").addEventListener("click", commitAdmins);
  adminInput.addEventListener("blur", commitAdmins);
  adminInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === "," || event.key === "，") {
      event.preventDefault();
      commitAdmins();
    }
  });

  renderAdminTags(tagList, group, groupIndex);
  if (group.servers.length === 0) {
    const empty = document.createElement("div");
    empty.className = "server-empty";
    empty.textContent = "当前群组还没有服务器，点击“添加服务器”开始配置。";
    serversList.append(empty);
  } else {
    group.servers.forEach((server, serverIndex) => {
      serversList.append(createServerCard(server, groupIndex, serverIndex));
    });
  }

  return details;
}

function renderAdminTags(container, group, groupIndex) {
  container.replaceChildren();
  if (group.admin_openids.length === 0) {
    const empty = document.createElement("span");
    empty.className = "field-hint";
    empty.textContent = "暂无管理员";
    container.append(empty);
    return;
  }

  group.admin_openids.forEach((userId, userIndex) => {
    const chip = document.createElement("span");
    chip.className = "tag-chip";
    const label = document.createElement("span");
    label.textContent = userId;
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "chip-remove";
    removeButton.setAttribute("aria-label", `移除管理员 ${userId}`);
    removeButton.textContent = "×";
    removeButton.addEventListener("click", () => {
      group.admin_openids.splice(userIndex, 1);
      renderAdminTags(container, group, groupIndex);
      markDirty();
    });
    chip.append(label, removeButton);
    container.append(chip);
  });
}

function createServerCard(server, groupIndex, serverIndex) {
  const card = document.createElement("details");
  card.className = "server-card";
  card.dataset.serverIndex = String(serverIndex);
  const openKey = serverKey(groupIndex, serverIndex);
  card.open = openServers.has(openKey);
  card.innerHTML = `
    <summary class="server-heading">
      <div class="server-title">
        <span class="disclosure disclosure-sm" aria-hidden="true">›</span>
        <span class="server-index">${String(serverIndex + 1).padStart(2, "0")}</span>
        <span class="server-name-preview"></span>
        <span class="server-meta"></span>
      </div>
      <div class="server-heading-actions">
        <span class="order-buttons">
          <button class="icon-button move-up move-server" type="button" aria-label="上移服务器" title="上移服务器">↑</button>
          <button class="icon-button move-down move-server" type="button" aria-label="下移服务器" title="下移服务器">↓</button>
        </span>
        <button class="button button-danger button-small remove-server" type="button">删除</button>
      </div>
    </summary>
    <div class="server-fields">
      <label class="field">
        <span class="field-label">查询快捷名</span>
        <span class="field-hint">例如 主服务器、对抗服</span>
        <input type="text" data-field="name" autocomplete="off" placeholder="服务器名称" />
      </label>
      <label class="field">
        <span class="field-label">连接地址</span>
        <span class="field-hint">不填写端口时默认使用 27015</span>
        <input type="text" data-field="address" autocomplete="off" placeholder="127.0.0.1:27015" />
      </label>
      <label class="field">
        <span class="field-label">黑盒语音频道 ID</span>
        <span class="field-hint">服务器对应的语音频道，可留空</span>
        <input type="text" data-field="hh_channel_id" autocomplete="off" placeholder="Channel ID" />
      </label>
      <label class="field">
        <span class="field-label">RCON 密码</span>
        <span class="field-hint">用于设置和重启指令，可留空</span>
        <span class="secret-control">
          <input type="password" data-field="rcon_password" autocomplete="new-password" placeholder="RCON password" />
          <button class="secret-toggle server-secret-toggle" type="button" data-target="rcon_password" aria-label="显示或隐藏 RCON 密码">显示</button>
        </span>
      </label>
      <label class="field">
        <span class="field-label">面板地址</span>
        <span class="field-hint">L4D2 面板地址，用于闪传链接上传地图，可留空</span>
        <input type="text" data-field="panel_url" autocomplete="off" placeholder="http://1.2.3.4:27020" />
      </label>
      <label class="field">
        <span class="field-label">面板凭据</span>
        <span class="field-hint">面板密码或临时授权码，需与管理面板地址同时填写</span>
        <span class="secret-control">
          <input type="password" data-field="panel_token" autocomplete="new-password" placeholder="面板密码 / 授权码" />
          <button class="secret-toggle server-secret-toggle" type="button" data-target="panel_token" aria-label="显示或隐藏面板凭据">显示</button>
        </span>
      </label>
    </div>
  `;

  const preview = card.querySelector(".server-name-preview");
  const meta = card.querySelector(".server-meta");
  for (const [key, label, hint] of [
    ["aliases", "查询别名", "多个别名用逗号分隔"],
    ["connect_url", "单服连接 URL", "留空使用全局连接基础 URL"],
    ["rcon_password_env", "RCON 密码环境变量", "兼容已有配置；留空时使用上方密码"],
    ["panel_token_env", "面板凭据环境变量", "兼容已有配置；留空时使用上方面板凭据"],
  ]) {
    const field = document.createElement("label");
    field.className = "field";
    const title = document.createElement("span");
    title.className = "field-label"; title.textContent = label;
    const note = document.createElement("span");
    note.className = "field-hint"; note.textContent = hint;
    const input = document.createElement("input");
    input.type = "text"; input.dataset.field = key; input.autocomplete = "off";
    field.append(title, note, input);
    card.querySelector(".server-fields").append(field);
  }
  const fieldNames = ["name", "address", "hh_channel_id", "rcon_password", "panel_url",
    "panel_token", "aliases", "connect_url", "rcon_password_env", "panel_token_env"];
  for (const fieldName of fieldNames) {
    const input = card.querySelector(`input[data-field="${fieldName}"]`);
    input.value = fieldName === "aliases" ? (server.aliases || []).join(", ") : server[fieldName] || "";
    input.dataset.path = `groups.${groupIndex}.servers.${serverIndex}.${fieldName}`;
    input.addEventListener("input", () => {
      server[fieldName] = fieldName === "aliases" ? input.value.split(/[,，]/).map(v => v.trim()).filter(Boolean) : input.value;
      if (fieldName === "name") {
        preview.textContent = input.value || "未命名服务器";
      }
      if (fieldName === "address") {
        meta.textContent = input.value;
      }
      markDirty();
    });
  }
  preview.textContent = server.name || "未命名服务器";
  meta.textContent = server.address || "";

  card.addEventListener("toggle", () => {
    if (card.open) openServers.add(openKey);
    else openServers.delete(openKey);
  });

  card.querySelectorAll(".server-secret-toggle").forEach((toggle) => {
    toggle.addEventListener("click", (clickEvent) => {
      const target = clickEvent.currentTarget.dataset.target;
      const input = card.querySelector(`input[data-field="${target}"]`);
      if (!input) return;
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      clickEvent.currentTarget.textContent = showing ? "显示" : "隐藏";
    });
  });
  card.querySelector(".remove-server").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    removeServer(groupIndex, serverIndex);
  });
  const serverTotal = state.groups[groupIndex].servers.length;
  const moveUpButton = card.querySelector(".move-server.move-up");
  const moveDownButton = card.querySelector(".move-server.move-down");
  moveUpButton.disabled = serverIndex === 0;
  moveDownButton.disabled = serverIndex === serverTotal - 1;
  for (const [button, offset] of [
    [moveUpButton, -1],
    [moveDownButton, 1],
  ]) {
    button.addEventListener("click", () => moveServer(groupIndex, serverIndex, offset));
  }
  return card;
}

function validateConfig() {
  const errors = [];
  const paths = [];

  function add(message, path = null) {
    errors.push(message);
    if (path) paths.push(path);
  }

  for (const [value, label, path] of [
    [state.connect_base_url, "一键连接基础 URL", "connect_base_url"],
    [state.map_name_url, "地图名称 API URL", "map_name_url"],
  ]) {
    if (!value.trim()) continue;
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error("protocol");
    } catch {
      add(`${label}必须是有效的 HTTP/HTTPS URL`, path);
    }
  }

  const groupIds = new Set();
  state.groups.forEach((group, groupIndex) => {
    const groupPath = `groups.${groupIndex}.group_openid`;
    const groupId = group.group_openid.trim();
    if (!groupId) {
      add(`第 ${groupIndex + 1} 个群组缺少群组 ID`, groupPath);
    } else if (/\s/.test(groupId)) {
      add(`群组 ID ${groupId} 不能包含空格`, groupPath);
    } else if (groupIds.has(groupId)) {
      add(`群组 ID ${groupId} 重复`, groupPath);
    }
    groupIds.add(groupId);

    const serverNames = new Set();
    group.servers.forEach((server, serverIndex) => {
      const prefix = `groups.${groupIndex}.servers.${serverIndex}`;
      const name = server.name.trim();
      const nameKey = name.replace(/\s+/g, "");
      if (!name) {
        add(`群组 ${groupId || groupIndex + 1} 的第 ${serverIndex + 1} 台服务器缺少名称`, `${prefix}.name`);
      } else if (serverNames.has(nameKey)) {
        add(`群组 ${groupId || groupIndex + 1} 中的服务器名称 ${name} 重复`, `${prefix}.name`);
      }
      serverNames.add(nameKey);

      const address = server.address.trim();
      if (!address) {
        add(`服务器 ${name || serverIndex + 1} 缺少连接地址`, `${prefix}.address`);
      } else if (!isValidServerAddress(address)) {
        add(`服务器 ${name || serverIndex + 1} 的地址格式应为 host 或 host:port`, `${prefix}.address`);
      }

      const panelUrl = server.panel_url.trim();
      if (panelUrl) {
        try {
          const url = new URL(panelUrl);
          if (!['http:', 'https:'].includes(url.protocol)) throw new Error("protocol");
        } catch {
          add(`服务器 ${name || serverIndex + 1} 的面板地址必须是有效的 HTTP/HTTPS URL`, `${prefix}.panel_url`);
        }
      }
    });
  });

  document.querySelectorAll("[data-path]").forEach((input) => {
    input.toggleAttribute("aria-invalid", paths.includes(input.dataset.path));
  });
  return errors;
}

function isValidServerAddress(address) {
  if (/\s/.test(address)) return false;
  if (address.startsWith("[")) return /^\\[[0-9a-fA-F:]+\\](?::[0-9]+)?$/.test(address);
  if ((address.match(/:/g) || []).length > 1) return false;
  if (!address.includes(":")) return address.length > 0;
  const [host, portText] = address.split(":");
  if (!host || !/^\d+$/.test(portText)) return false;
  const port = Number(portText);
  return port >= 1 && port <= 65535;
}

function requestConfirmation(message, acceptLabel = "确认") {
  if (confirmResolver) closeConfirmation(false);
  elements.confirmMessage.textContent = message;
  elements.confirmAccept.textContent = acceptLabel;
  elements.confirmLayer.hidden = false;
  elements.confirmCancel.focus();
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

function closeConfirmation(accepted) {
  if (!confirmResolver) return;
  const resolve = confirmResolver;
  confirmResolver = null;
  elements.confirmLayer.hidden = true;
  resolve(accepted);
}

async function loadConfig(initialLoad) {
  if (!initialLoad && isDirty()) {
    const confirmed = await requestConfirmation(
      "重新载入会丢弃当前未保存的修改，是否继续？",
      "放弃修改并载入",
    );
    if (!confirmed) return;
  }

  setBusy(true, "正在读取 config.json…");
  hideAlert();
  let failed = false;
  try {
    const result = await api("GET");
    state = normalizeConfig(result.config);
    revision = result.revision || "";
    baseline = snapshot(state);
    loaded = true;
    openGroups = new Set();
    openServers = new Set();
    renderGlobalControls();
    renderGroups();
    refreshStatus();
    if (!initialLoad) showToast("已从 config.json 重新载入");
  } catch (error) {
    failed = true;
    loaded = false;
    showAlert(error?.message || "读取配置失败");
    showToast("读取配置失败", "error");
  } finally {
    setBusy(false);
    refreshStatus(failed ? "error" : null);
  }
}

async function saveConfig() {
  if (!loaded) return;
  hideAlert();
  const errors = validateConfig();
  if (errors.length > 0) {
    refreshStatus("error");
    showAlert(errors);
    showToast("配置中仍有需要处理的问题", "error");
    document.querySelector('[aria-invalid="true"]')?.focus();
    return;
  }

  setBusy(true, "正在保存 config.json…");
  let failed = false;
  try {
    const result = await api("POST", {
      config: state,
      revision,
    });
    state = normalizeConfig(result.config || state);
    revision = result.revision || revision;
    baseline = snapshot(state);
    renderGlobalControls();
    renderGroups();
    refreshStatus();
    showToast("配置已保存并立即生效");
  } catch (error) {
    failed = true;
    showAlert(error?.message || "保存配置失败");
    showToast("保存失败，请检查配置", "error");
  } finally {
    setBusy(false);
    refreshStatus(failed ? "error" : null);
  }
}

async function initialize() {
  bindGlobalControls();
  setTheme({ isDark: window.matchMedia("(prefers-color-scheme: dark)").matches });
  const loginForm = document.getElementById("loginForm");
  loginForm.addEventListener("submit", async event => {
    event.preventDefault();
    password = document.getElementById("passwordInput").value;
    document.getElementById("loginButton").disabled = true;
    await loadConfig(true);
    document.getElementById("loginButton").disabled = false;
    if (loaded) {
      try { localStorage.setItem(PASSWORD_KEY, password); } catch {}
      document.getElementById("passwordInput").value = "";
      document.getElementById("loginPanel").hidden = true;
      document.querySelector(".page-shell").hidden = false;
    } else {
      document.getElementById("loginError").textContent = "登录失败，请检查密码和服务状态";
    }
  });
  document.getElementById("logoutButton").addEventListener("click", async () => {
    if (isDirty() && !await requestConfirmation("退出会丢弃未保存修改，是否继续？", "退出")) return;
    password = "";
    try { localStorage.removeItem(PASSWORD_KEY); } catch {}
    state = createEmptyConfig(); baseline = snapshot(state); loaded = false;
    renderGlobalControls(); renderGroups(); showLogin();
  });
  if (password) {
    await loadConfig(true);
    if (loaded) {
      document.getElementById("loginPanel").hidden = true;
      document.querySelector(".page-shell").hidden = false;
    }
  } else {
    setBusy(false);
    showLogin();
  }
}

window.addEventListener("beforeunload", (event) => {
  if (!isDirty()) return;
  event.preventDefault();
  event.returnValue = "";
});

initialize();
