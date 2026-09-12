const state = { revision: 0, guardrail: 0, capacity: 1, claims: [], pendingClaims: 0, records: [], audit: [] };
const $ = (id) => document.getElementById(id);

function snapshot() {
  return {
    claims: { active: state.claims.length, capacity: state.capacity, pending: state.pendingClaims },
    records: { created: state.records.length, completed: state.records.filter((record) => record.completed).length },
    guardrail: { limit: state.guardrail },
    audit: { events: state.audit.length },
    revision: state.revision,
  };
}
function render() {
  const view = snapshot();
  $('claim-count').textContent = view.claims.active;
  $('claim-capacity').textContent = view.claims.capacity;
  $('claim-status').textContent = view.claims.active > view.claims.capacity ? 'race detected: capacity exceeded' : view.claims.active ? 'one claim held' : 'slot available';
  $('claim-status').className = `claim-status ${view.claims.active > view.claims.capacity ? 'danger' : view.claims.active ? 'claimed' : ''}`;
  $('created').textContent = view.records.created;
  $('completed').textContent = view.records.completed;
  $('guardrail').textContent = view.guardrail.limit || 'none';
  $('revision').textContent = `revision ${view.revision}`;
  $('limit-output').textContent = view.guardrail.limit;
  $('records').replaceChildren(...state.records.map((record) => {
    const item = document.createElement('li');
    item.innerHTML = `<span></span><strong></strong>`;
    item.firstChild.textContent = record.text;
    item.lastChild.textContent = record.completed ? 'completed' : 'open';
    item.lastChild.className = record.completed ? 'done' : '';
    return item;
  }));
  $('empty').hidden = state.records.length > 0;
}
function mutate(change) { state.revision += 1; change(); render(); return snapshot(); }
function createRecord({ text }) {
  if (typeof text !== 'string' || !text.trim()) throw new Error('text must be a non-empty string');
  const result = mutate(() => state.records.push({ id: crypto.randomUUID(), text: text.trim(), completed: false }));
  return { created: result.records.created, revision: result.revision };
}
function completeNext() {
  const next = state.records.find((record) => !record.completed);
  if (!next) return { completed: false, reason: 'No open record exists.' };
  if (state.guardrail && snapshot().records.completed >= state.guardrail) throw new Error('completion guardrail reached');
  const result = mutate(() => { next.completed = true; });
  return { completed: true, totals: result.records };
}
function clearRecords() { return mutate(() => { state.records = []; }); }
function setGuardrail({ limit }) {
  if (!Number.isInteger(limit) || limit < 0) throw new Error('limit must be a non-negative integer');
  return mutate(() => { state.guardrail = limit; });
}

// Intentionally vulnerable demo operation. Both callers read the same
// availability before the async gap, then commit without re-checking it.
// The gap makes the human/UI and WebMCP tool race observable and repeatable.
async function claimSlot({ actor = 'tool' } = {}) {
  if (typeof actor !== 'string' || !actor.trim()) throw new Error('actor must be a non-empty string');
  const observedActive = state.claims.length;
  state.pendingClaims += 1;
  // Keep this comfortably wider than a page-evaluation turn. The runner's
  // page-side startTool handoff lets the UI click begin during this await.
  await new Promise((resolve) => setTimeout(resolve, 250));
  if (observedActive >= state.capacity) {
    state.pendingClaims = Math.max(0, state.pendingClaims - 1);
    return { code: 'FULL', active: state.claims.length, capacity: state.capacity };
  }
  const result = mutate(() => {
    state.pendingClaims = Math.max(0, state.pendingClaims - 1);
    state.claims.push({ id: crypto.randomUUID(), actor: actor.trim() });
    state.audit.push({ event: 'claim_committed', actor: actor.trim() });
  });
  return { code: 'OK', active: result.claims.active, capacity: result.claims.capacity };
}

function fallbackModelContext() {
  const tools = [];
  return {
    async registerTool(tool) { tools.push(tool); },
    async getTools() { return [...tools].sort((a, b) => a.name.localeCompare(b.name)); },
    async executeTool(tool, json, { signal } = {}) {
      if (signal?.aborted) throw new DOMException('Tool invocation aborted', 'AbortError');
      return tool.execute(JSON.parse(json), { signal });
    },
  };
}

const nativeWebMCP = Boolean(document.modelContext);
const host = document.modelContext ?? fallbackModelContext();
if (!nativeWebMCP) document.modelContext = host;
const tools = [
  { name: 'create_record', description: 'Create a session record with user-provided text.', inputSchema: { type: 'object', properties: { text: { type: 'string', minLength: 1 } }, required: ['text'] }, execute: createRecord },
  { name: 'complete_next_record', description: 'Complete the oldest open session record.', inputSchema: { type: 'object', properties: {} }, execute: completeNext },
  { name: 'clear_records', description: 'Remove all records created in this browser session.', inputSchema: { type: 'object', properties: {} }, annotations: { consequentialHint: true }, execute: clearRecords },
  { name: 'set_completion_guardrail', description: 'Set the maximum number of records that may be completed; zero removes the cap.', inputSchema: { type: 'object', properties: { limit: { type: 'integer', minimum: 0 } }, required: ['limit'] }, execute: setGuardrail },
  { name: 'get_observable_state', description: 'Read the current session state without modifying it.', inputSchema: { type: 'object', properties: {} }, annotations: { readOnlyHint: true }, execute: snapshot },
  { name: 'claim_slot', description: 'Claim the single shared lab slot. This is intentionally vulnerable to a human/UI plus tool race.', inputSchema: { type: 'object', properties: { actor: { type: 'string', minLength: 1 } }, required: ['actor'] }, execute: claimSlot },
];
await Promise.all(tools.map((tool) => host.registerTool(tool)));
window.__resilienceLab = { getState: snapshot };
$('api-status').textContent = nativeWebMCP ? 'Native WebMCP active' : 'Fallback host active';
$('tool-list').replaceChildren(...tools.map((tool) => { const item = document.createElement('li'); item.textContent = tool.name; return item; }));
$('add').addEventListener('click', () => { try { createRecord({ text: $('note').value }); $('note').value = ''; } catch (error) { $('note').setCustomValidity(error.message); $('note').reportValidity(); } });
$('note').addEventListener('keydown', (event) => { if (event.key === 'Enter') $('add').click(); });
$('complete').addEventListener('click', () => { try { completeNext(); } catch (error) { window.alert(error.message); } });
$('clear').addEventListener('click', clearRecords);
$('limit').addEventListener('input', (event) => setGuardrail({ limit: Number(event.target.value) }));
$('claim').addEventListener('click', () => {
  $('claim-status').textContent = 'human claim pending…';
  void claimSlot({ actor: 'human' }).then((result) => {
    $('claim-status').textContent = `human claim ${result.code.toLowerCase()}`;
    render();
  }).catch((error) => { $('claim-status').textContent = error.message; });
});
render();
