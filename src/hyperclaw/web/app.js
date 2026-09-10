'use strict';

(() => {
  const DEFAULT_TOOLS = [
    'workspace_read', 'workspace_list', 'workspace_search', 'workspace_write', 'command',
    'memory_remember', 'memory_search', 'memory_correct', 'memory_forget',
  ];
  const MCP_TOOLS = ['mcp_docs_search', 'mcp_docs_read'];
  const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted', 'uncertain']);
  const byId = (id) => document.getElementById(id);
  const state = {
    phase: 'disconnected', token: '', sessions: [], session: null, runs: [], run: null,
    stream: null, lastSeq: 0, reconnects: 0, pendingSubmission: null,
  };

  function node(tag, text, className) {
    const value = document.createElement(tag);
    if (text !== undefined) value.textContent = text;
    if (className) value.className = className;
    return value;
  }

  function clear(element) {
    element.replaceChildren();
  }

  function describe(error) {
    if (!error) return 'Unknown client error.';
    if (error.error) return `${error.error.code}: ${error.error.message}`;
    return error.message || String(error);
  }

  function showError(error) {
    byId('errors').textContent = describe(error);
  }

  function clearError() {
    byId('errors').textContent = '';
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options, credentials: 'omit', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...options.headers,
        Authorization: `Bearer ${state.token}` },
    });
    if (!response.ok) {
      let error;
      try { error = await response.json(); }
      catch (_) { error = {error: {code: 'http_error', message: `HTTP ${response.status}`}}; }
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  function setPhase(phase) {
    state.phase = phase;
    document.body.dataset.phase = phase;
    updateControls();
  }

  function updateControls() {
    const connected = state.phase !== 'disconnected' && state.phase !== 'connecting';
    byId('send').disabled = !connected || !state.session || Boolean(state.pendingSubmission);
    byId('reset-session').disabled = !connected || !state.session;
    byId('cancel-run').disabled = !connected || !state.run || TERMINAL.has(state.run.status);
    byId('reconnect-stream').disabled = !connected || !state.run || TERMINAL.has(state.run.status);
    byId('retry-send').hidden = !state.pendingSubmission;
  }

  function hashState() {
    const values = new URLSearchParams(location.hash.slice(1));
    return {session: values.get('session'), run: values.get('run')};
  }

  function saveHash() {
    const values = new URLSearchParams();
    if (state.session) values.set('session', state.session.id);
    if (state.run) values.set('run', state.run.id);
    history.replaceState(null, '', values.size ? `#${values}` : location.pathname);
  }

  function renderSessions() {
    const target = byId('sessions');
    clear(target);
    for (const session of state.sessions) {
      const button = node('button', `${session.id} · gen ${session.generation}`);
      button.type = 'button';
      button.dataset.sessionId = session.id;
      if (state.session && session.id === state.session.id) button.classList.add('selected');
      button.addEventListener('click', () => selectSession(session.id));
      target.append(button);
    }
    byId('load-older-sessions').hidden = state.sessions.length < 50;
  }

  function renderChoices(skills, mcp) {
    const tools = byId('tools');
    clear(tools);
    for (const name of [...DEFAULT_TOOLS, ...MCP_TOOLS]) {
      const label = node('label');
      const input = document.createElement('input');
      input.type = 'checkbox'; input.name = 'tools'; input.value = name;
      input.checked = DEFAULT_TOOLS.includes(name);
      if (MCP_TOOLS.includes(name)) {
        const admission = mcp && mcp.admission;
        input.disabled = !(mcp && mcp.enabled !== false && admission && admission.sha256 === mcp.sha256);
      }
      label.append(input, document.createTextNode(` ${name}`));
      tools.append(label);
    }
    const skillTarget = byId('skills');
    clear(skillTarget);
    const admitted = skills.filter((skill) => skill.admitted);
    if (!admitted.length) skillTarget.append(node('p', 'No reviewed skills are currently admitted.', 'muted'));
    for (const skill of admitted) {
      const label = node('label');
      const input = document.createElement('input');
      input.type = 'checkbox'; input.name = 'skills'; input.value = skill.name;
      label.append(input, document.createTextNode(` ${skill.name} — ${skill.description}`));
      skillTarget.append(label);
    }
  }

  function renderRunHistory() {
    const target = byId('run-history');
    clear(target);
    for (const run of state.runs) {
      const button = node('button', `${run.status} · gen ${run.request.generation} · ${run.id}`);
      button.type = 'button'; button.dataset.runId = run.id;
      if (state.run && run.id === state.run.id) button.classList.add('selected');
      button.addEventListener('click', () => selectRun(run.id));
      target.append(button);
    }
  }

  function appendActivity(label, value) {
    const article = node('article', undefined, 'activity-item');
    article.append(node('strong', label), node('pre', typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
    byId('activity').append(article);
  }

  function renderRun(run, preserveActivity = false) {
    state.run = run;
    if (run) {
      const index = state.runs.findIndex((item) => item.id === run.id);
      if (index >= 0) state.runs[index] = run;
    }
    byId('run-id').textContent = run ? run.id : 'None';
    const status = run ? run.status : 'No run selected';
    const verification = run && ({
      not_requested: 'No verification requested', passed: 'Verification passed',
      failed: 'Verification failed',
    })[run.verification];
    byId('run-status').textContent = run ? `Run: ${status.replace('_', ' ')} · ${verification}` : status;
    byId('run-status').dataset.status = run ? run.status : '';
    const transcript = byId('transcript');
    clear(transcript);
    if (!preserveActivity) clear(byId('activity'));
    clear(byId('receipts'));
    if (run) {
      const user = node('article', undefined, 'message user-message');
      user.append(node('h3', 'You'), node('pre', run.request.text));
      const assistant = node('article', undefined, 'message assistant-message');
      assistant.append(node('h3', 'Assistant'), node('pre', run.output || '', 'assistant-output'));
      transcript.append(user, assistant);
      if (run.error) appendActivity('Error', run.error);
    }
    renderRunHistory();
    saveHash();
    updateControls();
  }

  async function loadReceipts() {
    if (!state.run) return;
    const runId = state.run.id;
    const receipts = await request(`/v1/runs/${encodeURIComponent(runId)}/receipts`);
    if (!state.run || state.run.id !== runId) return;
    const target = byId('receipts'); clear(target);
    for (const receipt of receipts) target.append(node('pre', JSON.stringify(receipt, null, 2)));
    if (!receipts.length) target.append(node('p', 'No receipts.', 'muted'));
  }

  async function refreshApprovals() {
    if (!state.token) return;
    try {
      const approvals = await request('/v1/approvals');
      const target = byId('approvals'); clear(target);
      if (!approvals.length) target.append(node('p', 'No waiting approvals.', 'muted'));
      for (const approval of approvals) {
        const article = node('article', undefined, 'approval');
        const title = node('h3', approval.call.name);
        const details = node('pre', JSON.stringify({
          arguments: approval.call.arguments,
          workspace_id: approval.workspace_id,
          arguments_sha256: approval.arguments_sha256,
          policy_sha256: approval.policy_sha256,
        }, null, 2));
        const controls = node('div', undefined, 'row');
        for (const [label, approved] of [['Approve', true], ['Deny', false]]) {
          const button = node('button', label); button.type = 'button';
          button.dataset.decision = approved ? 'approve' : 'deny';
          button.addEventListener('click', () => decideApproval(article, approval, approved));
          controls.append(button);
        }
        article.append(title, details, controls); target.append(article);
      }
    } catch (error) { showError(error); }
  }

  async function decideApproval(article, approval, approved) {
    for (const button of article.querySelectorAll('button')) button.disabled = true;
    try {
      clearError();
      const run = await request(`/v1/approvals/${encodeURIComponent(approval.id)}/decision`, {
        method: 'POST', body: JSON.stringify({
          approved,
          arguments_sha256: approval.arguments_sha256,
          policy_sha256: approval.policy_sha256,
        }),
      });
      if (state.run && state.run.id === run.id) renderRun(run, true);
      await refreshApprovals();
    } catch (error) {
      showError(error);
      article.append(node('p', 'Decision rejected. Refresh and review the current action before deciding again.', 'warning'));
    }
  }

  async function connect() {
    const candidate = byId('token').value;
    byId('token').value = '';
    if (!candidate) { showError({message: 'Enter the operator token.'}); return; }
    abortStream(); clearError(); state.token = candidate; setPhase('connecting');
    try {
      const [sessions, skills, mcp] = await Promise.all([
        request('/v1/sessions'), request('/v1/skills'), request('/v1/mcp'),
      ]);
      state.sessions = sessions;
      renderChoices(skills, mcp);
      byId('workspace').hidden = false; byId('logout').hidden = false; byId('connect').hidden = true;
      byId('connection-panel').classList.add('connected');
      byId('connection-status').textContent = 'Connected';
      setPhase('selecting'); renderSessions(); await refreshApprovals();
      const remembered = hashState();
      if (remembered.session && sessions.some((item) => item.id === remembered.session)) {
        await selectSession(remembered.session, remembered.run);
      }
    } catch (error) {
      state.token = ''; setPhase('disconnected'); showError(error);
      byId('connection-status').textContent = 'Disconnected';
    }
  }

  function abortStream() {
    if (state.stream) state.stream.abort();
    state.stream = null;
  }

  function logout() {
    abortStream();
    state.phase = 'disconnected'; state.token = ''; state.sessions = []; state.session = null;
    state.runs = []; state.run = null; state.pendingSubmission = null; state.lastSeq = 0;
    byId('token').value = ''; byId('workspace').hidden = true; byId('logout').hidden = true;
    byId('connection-panel').classList.remove('connected');
    byId('connect').hidden = false; byId('connection-status').textContent = 'Disconnected';
    clear(byId('transcript')); clear(byId('approvals')); clearError();
    history.replaceState(null, '', location.pathname); setPhase('disconnected');
  }

  async function createSession() {
    try {
      clearError();
      const session = await request('/v1/sessions', {method: 'POST', body: '{}'});
      state.sessions.unshift(session); renderSessions(); await selectSession(session.id);
    } catch (error) { showError(error); }
  }

  async function loadRuns(sessionId) {
    const runs = await request(`/v1/sessions/${encodeURIComponent(sessionId)}/runs`);
    if (state.session && state.session.id === sessionId) {
      state.runs = runs; renderRunHistory();
    }
    return runs;
  }

  async function selectSession(sessionId, preferredRun) {
    abortStream(); clearError(); setPhase('selecting');
    try {
      const session = await request(`/v1/sessions/${encodeURIComponent(sessionId)}`);
      state.session = session;
      byId('session-id').textContent = session.id; byId('generation').textContent = String(session.generation);
      renderSessions();
      const runs = await loadRuns(session.id);
      const chosen = runs.find((run) => run.id === preferredRun) || runs[0];
      if (chosen) await selectRun(chosen.id);
      else { renderRun(null); setPhase('selecting'); }
    } catch (error) { showError(error); setPhase('selecting'); }
  }

  async function selectRun(runId) {
    abortStream(); clearError(); state.lastSeq = 0; state.reconnects = 0; setPhase('observing');
    try {
      const run = await request(`/v1/runs/${encodeURIComponent(runId)}`);
      renderRun(run);
      observe(run.id);
    } catch (error) { showError(error); setPhase('selecting'); }
  }

  function selected(name) {
    return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
  }

  async function startSubmission(event) {
    event.preventDefault();
    const text = byId('message').value;
    if (!state.session || !text.trim() || state.pendingSubmission) return;
    state.pendingSubmission = {
      session_id: state.session.id, generation: state.session.generation,
      request_id: crypto.randomUUID(), text, tools: selected('tools'), skills: selected('skills'),
    };
    await sendPending();
  }

  async function sendPending() {
    if (!state.pendingSubmission) return;
    const payload = state.pendingSubmission;
    setPhase('submitting'); clearError();
    try {
      const run = await request('/v1/runs', {method: 'POST', body: JSON.stringify(payload)});
      if (state.pendingSubmission !== payload) return;
      state.pendingSubmission = null; byId('message').value = '';
      state.runs.unshift(run); state.lastSeq = 0; state.reconnects = 0;
      renderRun(run); setPhase('observing'); observe(run.id);
    } catch (error) {
      showError(error);
      if (error.status) state.pendingSubmission = null;
      setPhase('selecting'); updateControls();
    }
  }

  function parseFrame(frame) {
    let id = null; const data = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith('id:')) id = Number(line.slice(3).trim());
      if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (!Number.isSafeInteger(id) || !data.length) return null;
    return {id, event: JSON.parse(data.join('\n'))};
  }

  async function applyEvent(item) {
    if (item.id <= state.lastSeq) return;
    state.lastSeq = item.id;
    const event = item.event;
    if (event.kind === 'run.queued' || event.kind === 'run.started') {
      state.run = {...state.run, status: event.kind === 'run.started' ? 'running' : 'queued'};
      const verification = state.run.verification === 'not_requested' ? 'No verification requested' :
        state.run.verification === 'passed' ? 'Verification passed' : 'Verification failed';
      byId('run-status').textContent = `Run: ${state.run.status} · ${verification}`;
      byId('run-status').dataset.status = state.run.status;
      updateControls();
    } else if (event.kind === 'model.text') {
      const output = document.querySelector('.assistant-output');
      if (output) output.textContent += event.data.text;
    } else if (event.kind === 'model.thinking') {
      appendActivity('Model thinking', event.data.text);
    } else if (event.kind === 'tool.requested') {
      appendActivity(`Tool request: ${event.data.call.name}`, event.data);
    } else if (event.kind === 'tool.finished') {
      appendActivity('Tool finished', event.data);
      await loadReceipts();
    } else if (event.kind === 'approval.required') {
      state.run = {...state.run, status: 'waiting_approval'};
      byId('run-status').textContent = 'Run: waiting approval · No verification requested';
      byId('run-status').dataset.status = 'waiting_approval';
      updateControls();
      appendActivity(`Approval required: ${event.data.call.name}`, event.data);
      await refreshApprovals();
    }
  }

  async function durableRun(runId) {
    const run = await request(`/v1/runs/${encodeURIComponent(runId)}`);
    if (!state.run || state.run.id !== runId) return null;
    const partial = document.querySelector('.assistant-output')?.textContent || '';
    renderRun(run, true);
    if (!run.output && partial) document.querySelector('.assistant-output').textContent = partial;
    await loadReceipts();
    return run;
  }

  async function observe(runId) {
    if (!state.run || state.run.id !== runId) return;
    abortStream();
    const controller = new AbortController(); state.stream = controller;
    try {
      const response = await fetch(`/v1/runs/${encodeURIComponent(runId)}/events?after=${state.lastSeq}`, {
        credentials: 'omit', cache: 'no-store', signal: controller.signal,
        headers: {Accept: 'text/event-stream', Authorization: `Bearer ${state.token}`},
      });
      if (!response.ok) {
        const error = await response.json(); error.status = response.status; throw error;
      }
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const {value, done} = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
        let match;
        while ((match = /\r?\n\r?\n/.exec(buffer)) !== null) {
          const frame = buffer.slice(0, match.index);
          buffer = buffer.slice(match.index + match[0].length);
          const item = parseFrame(frame);
          if (item) await applyEvent(item);
        }
        if (done) break;
      }
      if (buffer.trim()) throw {error: {code: 'stream_frame', message: 'Event stream ended inside a frame.'}};
      const run = await durableRun(runId);
      if (!run || TERMINAL.has(run.status)) { state.reconnects = 0; setPhase('selecting'); return; }
      throw {error: {code: 'stream_ended', message: 'Event stream ended before durable completion.'}};
    } catch (error) {
      if (controller.signal.aborted || !state.run || state.run.id !== runId) return;
      showError(error);
      if (TERMINAL.has(state.run.status)) { setPhase('selecting'); return; }
      const status = error && error.status;
      if ([401, 403, 409, 422].includes(status) || state.reconnects >= 3) {
        setPhase('selecting'); return;
      }
      const delay = 250 * (2 ** state.reconnects++);
      await new Promise((resolve) => setTimeout(resolve, delay));
      if (state.run && state.run.id === runId && state.token) observe(runId);
    } finally {
      if (state.stream === controller) state.stream = null;
    }
  }

  async function cancelRun() {
    if (!state.run) return;
    try {
      clearError();
      const partial = document.querySelector('.assistant-output')?.textContent || '';
      const run = await request(`/v1/runs/${encodeURIComponent(state.run.id)}/cancel`, {method: 'POST', body: '{}'});
      renderRun(run, true);
      if (!run.output && partial) document.querySelector('.assistant-output').textContent = partial;
      await loadRuns(state.session.id);
    } catch (error) { showError(error); }
  }

  async function resetSession() {
    if (!state.session) return;
    try {
      clearError();
      const session = await request(`/v1/sessions/${encodeURIComponent(state.session.id)}/reset`, {
        method: 'POST', body: JSON.stringify({generation: state.session.generation}),
      });
      state.session = session; byId('generation').textContent = String(session.generation);
      const listed = state.sessions.find((item) => item.id === session.id);
      if (listed) Object.assign(listed, session);
      renderSessions(); await loadRuns(session.id); saveHash(); updateControls();
    } catch (error) { showError(error); }
  }

  async function loadOlderSessions() {
    if (!state.sessions.length) return;
    try {
      const older = await request(`/v1/sessions?limit=50&before=${encodeURIComponent(state.sessions.at(-1).id)}`);
      state.sessions.push(...older); renderSessions();
    } catch (error) { showError(error); }
  }

  byId('connect').addEventListener('click', connect);
  byId('logout').addEventListener('click', logout);
  byId('new-session').addEventListener('click', createSession);
  byId('load-older-sessions').addEventListener('click', loadOlderSessions);
  byId('composer').addEventListener('submit', startSubmission);
  byId('retry-send').addEventListener('click', sendPending);
  byId('cancel-run').addEventListener('click', cancelRun);
  byId('reset-session').addEventListener('click', resetSession);
  byId('refresh-approvals').addEventListener('click', refreshApprovals);
  byId('reconnect-stream').addEventListener('click', () => {
    if (state.run) { state.reconnects = 0; clearError(); observe(state.run.id); }
  });
  setPhase('disconnected');
})();
