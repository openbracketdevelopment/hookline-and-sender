(() => {
  const byId = (id) => document.getElementById(id);
  const pageSize = 25;
  let apiKey = "";
  let offset = 0;
  let selected = null;
  let generation = 0;
  let timer;
  let busy = false;
  let hasNext = false;
  const requests = new Set();
  const labels = {
    pending: "Pending", in_progress: "In progress", retry_scheduled: "Retry scheduled",
    succeeded: "Succeeded", failed: "Failed",
  };
  const date = (value) => value ? new Date(value).toLocaleString() : "—";
  function message(text, error = false) {
    byId("activity-message").textContent = text;
    byId("activity-message").setAttribute("role", error ? "alert" : "status");
  }
  function cancel() {
    generation += 1;
    clearTimeout(timer);
    for (const controller of requests) controller.abort();
    requests.clear();
  }
  function controls() {
    byId("refresh").disabled = busy;
    byId("previous").disabled = busy || offset === 0;
    byId("next").disabled = busy || !hasNext;
  }
  function disconnect() {
    cancel();
    apiKey = "";
    selected = null;
    busy = false;
    byId("api-key").value = "";
    byId("disconnect").hidden = true;
    byId("activity-content").hidden = true;
    byId("delivery-details").hidden = true;
    byId("delivery-rows").replaceChildren();
    byId("attempt-rows").replaceChildren();
    byId("detail-summary").replaceChildren();
    message("Disconnected. Enter your API key to view activity.");
  }
  async function get(path) {
    const controller = new AbortController();
    requests.add(controller);
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    try {
      const response = await fetch(path, {
        headers: { Authorization: `Bearer ${apiKey}` },
        cache: "no-store", credentials: "omit", redirect: "error", signal: controller.signal,
      });
      if (response.status === 401) {
        disconnect();
        message("Access denied. Check your API key and connect again.", true);
        return null;
      }
      if (!response.ok) throw new Error(`Request failed (HTTP ${response.status}). Check the relay logs and try again.`);
      return await response.json();
    } catch (error) {
      if (timedOut) throw new Error("The relay took too long to respond. Check its services and try again.");
      throw error;
    } finally {
      clearTimeout(timeout);
      requests.delete(controller);
    }
  }
  function cell(row, text) {
    const element = document.createElement("td");
    element.textContent = text;
    row.append(element);
    return element;
  }
  function badge(status) {
    const element = document.createElement("span");
    element.className = `status-badge ${Object.hasOwn(labels, status) ? `status-${status}` : ""}`;
    element.textContent = labels[status] || status;
    return element;
  }
  function renderList(items) {
    const rows = byId("delivery-rows");
    rows.replaceChildren();
    for (const item of items) {
      const row = document.createElement("tr");
      const identity = cell(row, "");
      for (const [text, style] of [[item.id, "identifier"], [`Event: ${item.event_id}`, "identifier event-identifier"]]) {
        const line = document.createElement("span");
        line.className = style;
        line.textContent = text;
        identity.append(line);
      }
      cell(row, item.destination_id);
      cell(row, "").append(badge(item.status));
      cell(row, String(item.attempt_count));
      cell(row, date(item.created_at));
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "View";
      button.setAttribute("aria-label", `View delivery ${item.id}`);
      button.addEventListener("click", () => { selected = item.id; load(true); });
      cell(row, "").append(button);
      rows.append(row);
    }
    byId("empty-state").hidden = items.length > 0;
    byId("page-number").textContent = `Page ${offset / pageSize + 1}`;
    byId("page-summary").textContent = items.length
      ? `Showing ${offset + 1}–${offset + items.length}, newest first. Times are local to your browser.`
      : "0 deliveries on this page.";
  }
  function renderDetail(item) {
    const summary = byId("detail-summary");
    summary.replaceChildren();
    for (const [label, value] of [
      ["Delivery ID", item.id], ["Event ID", item.event_id],
      ["Destination", item.destination_id], ["Status", labels[item.status] || item.status],
      ["Created", date(item.created_at)], ["Updated", date(item.updated_at)],
      ["Next attempt", date(item.next_attempt_at)], ["Last error", item.last_error || "None"],
    ]) {
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = label;
      description.textContent = value;
      summary.append(term, description);
    }
    const rows = byId("attempt-rows");
    rows.replaceChildren();
    for (const attempt of item.attempts) {
      const row = document.createElement("tr");
      for (const value of [
        attempt.attempt_number, date(attempt.started_at), attempt.outcome,
        attempt.http_status ?? "—", attempt.duration_ms === null ? "—" : `${attempt.duration_ms} ms`,
        attempt.error_detail || "None",
      ]) cell(row, String(value));
      rows.append(row);
    }
    byId("no-attempts").hidden = item.attempts.length > 0;
    byId("delivery-details").hidden = false;
  }
  function schedule() {
    clearTimeout(timer);
    if (apiKey && byId("auto-refresh").checked && !document.hidden) timer = setTimeout(load, 10000);
  }
  async function load(focusDetails = false) {
    cancel();
    const current = generation;
    busy = true;
    controls();
    message("Loading activity…");
    const query = new URLSearchParams({ limit: String(pageSize + 1), offset: String(offset) });
    const status = byId("status-filter").value;
    if (status) query.set("status", status);
    try {
      const result = await get(`/api/v1/deliveries?${query}`);
      if (current !== generation || !result) return;
      hasNext = result.items.length > pageSize;
      renderList(result.items.slice(0, pageSize));
      byId("activity-content").hidden = false;
      if (selected) {
        const detail = await get(`/api/v1/deliveries/${encodeURIComponent(selected)}`);
        if (current !== generation || !detail) return;
        renderDetail(detail);
        if (focusDetails) byId("details-title").focus();
      }
      message(`Updated ${new Date().toLocaleTimeString()}.`);
    } catch (error) {
      if (current === generation && error.name !== "AbortError") {
        message(`${error.message} Displayed activity may be out of date.`, true);
      }
    } finally {
      if (current === generation) {
        busy = false;
        controls();
        schedule();
      }
    }
  }
  byId("access-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const key = byId("api-key").value.trim();
    disconnect();
    apiKey = key;
    offset = 0;
    byId("disconnect").hidden = false;
    load();
  });
  byId("disconnect").addEventListener("click", disconnect);
  byId("refresh").addEventListener("click", () => load());
  byId("status-filter").addEventListener("change", () => {
    offset = 0;
    selected = null;
    byId("delivery-details").hidden = true;
    load();
  });
  byId("previous").addEventListener("click", () => { offset = Math.max(0, offset - pageSize); load(); });
  byId("next").addEventListener("click", () => { offset += pageSize; load(); });
  byId("close-details").addEventListener("click", () => {
    selected = null;
    byId("delivery-details").hidden = true;
    load();
  });
  byId("auto-refresh").addEventListener("change", schedule);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearTimeout(timer);
    else if (apiKey && byId("auto-refresh").checked) load();
  });
  window.addEventListener("pagehide", disconnect);
})();
