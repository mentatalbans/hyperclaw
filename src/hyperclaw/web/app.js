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
    connectionEpoch: 0, selectionEpoch: 0, observationEpoch: 0, approvalsEpoch: 0,
    sessionCursor: null, hasOlderSessions: false, runCursor: null, hasOlderRuns: false,
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
    byId('retry-send').hidden = !state.pendingSubmission || !state.session ||
      state.pendingSubmission.payload.session_id !== state.session.id || state.pendingSubmission.inFlight;
    byId('load-older-runs').hidden = !state.session || !state.hasOlderRuns;
  }

  function ownsConnection(epoch, token) {
    return state.connectionEpoch === epoch && state.token === token && Boolean(token);
  }

  function ownsSelection(connectionEpoch, token, selectionEpoch, sessionId) {
    return ownsConnection(connectionEpoch, token) && state.selectionEpoch === selectionEpoch &&
      state.session && state.session.id === sessionId;
  }

  function invalidateObservation() {
    state.observationEpoch += 1;
    if (state.stream) state.stream.abort();
    state.stream = null;
  }

  function beginSelection() {
    state.selectionEpoch += 1;
    invalidateObservation();
    return state.selectionEpoch;
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
    byId('load-older-sessions').hidden = !state.hasOlderSessions;
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
    byId('load-older-runs').hidden = !state.session || !state.hasOlderRuns;
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

  async function loadReceipts(expectedOwner) {
    if (!state.run) return;
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const selectionEpoch = state.selectionEpoch; const sessionId = state.session && state.session.id;
    const runId = state.run.id;
    const receipts = await request(`/v1/runs/${encodeURIComponent(runId)}/receipts`);
    const owns = expectedOwner || (() => ownsSelection(
      connectionEpoch, token, selectionEpoch, sessionId,
    ) && state.run && state.run.id === runId);
    if (!owns()) return;
    const target = byId('receipts'); clear(target);
    for (const receipt of receipts) target.append(node('pre', JSON.stringify(receipt, null, 2)));
    if (!receipts.length) target.append(node('p', 'No receipts.', 'muted'));
  }

  async function refreshApprovals(expectedOwner) {
    if (!state.token) return;
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const approvalsEpoch = ++state.approvalsEpoch;
    const owns = expectedOwner || (() => ownsConnection(connectionEpoch, token));
    try {
      const approvals = await request('/v1/approvals');
      if (!owns() || state.approvalsEpoch !== approvalsEpoch) return;
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
    } catch (error) {
      if (owns() && state.approvalsEpoch === approvalsEpoch) showError(error);
    }
  }

  async function decideApproval(article, approval, approved) {
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const selectionEpoch = state.selectionEpoch;
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
      if (!ownsConnection(connectionEpoch, token)) return;
      if (state.selectionEpoch === selectionEpoch && state.run && state.run.id === run.id) {
        renderRun(run, true);
      }
      await refreshApprovals(() => ownsConnection(connectionEpoch, token));
    } catch (error) {
      if (!ownsConnection(connectionEpoch, token)) return;
      showError(error);
      article.append(node('p', 'Decision rejected. Refresh and review the current action before deciding again.', 'warning'));
    }
  }

  async function connect() {
    const candidate = byId('token').value;
    byId('token').value = '';
    if (!candidate) { showError({message: 'Enter the operator token.'}); return; }
    state.connectionEpoch += 1; beginSelection(); state.approvalsEpoch += 1;
    const connectionEpoch = state.connectionEpoch;
    clearError(); state.token = candidate; setPhase('connecting');
    try {
      const [sessions, skills, mcp] = await Promise.all([
        request('/v1/sessions'), request('/v1/skills'), request('/v1/mcp'),
      ]);
      if (!ownsConnection(connectionEpoch, candidate)) return;
      state.sessions = sessions;
      state.sessionCursor = sessions.length ? sessions.at(-1).id : null;
      state.hasOlderSessions = sessions.length === 50;
      renderChoices(skills, mcp);
      byId('workspace').hidden = false; byId('logout').hidden = false; byId('connect').hidden = true;
      byId('connection-panel').classList.add('connected');
      byId('connection-status').textContent = 'Connected';
      setPhase('selecting'); renderSessions(); refreshApprovals();
      const remembered = hashState();
      if (remembered.session) {
        await selectSession(remembered.session, remembered.run);
      }
    } catch (error) {
      if (!ownsConnection(connectionEpoch, candidate)) return;
      state.token = ''; setPhase('disconnected'); showError(error);
      byId('connection-status').textContent = 'Disconnected';
    }
  }

  function logout() {
    state.connectionEpoch += 1; state.selectionEpoch += 1; state.approvalsEpoch += 1;
    invalidateObservation();
    state.phase = 'disconnected'; state.token = ''; state.sessions = []; state.session = null;
    state.runs = []; state.run = null; state.pendingSubmission = null; state.lastSeq = 0;
    state.sessionCursor = null; state.hasOlderSessions = false;
    state.runCursor = null; state.hasOlderRuns = false;
    byId('token').value = ''; byId('workspace').hidden = true; byId('logout').hidden = true;
    byId('connection-panel').classList.remove('connected');
    byId('connect').hidden = false; byId('connection-status').textContent = 'Disconnected';
    byId('session-id').textContent = 'None'; byId('generation').textContent = '—';
    byId('run-id').textContent = 'None'; byId('run-status').textContent = 'No run selected';
    clear(byId('sessions')); clear(byId('run-history')); clear(byId('transcript'));
    clear(byId('activity')); clear(byId('receipts')); clear(byId('approvals')); clearError();
    history.replaceState(null, '', location.pathname); setPhase('disconnected');
  }

  async function createSession() {
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const selectionEpoch = beginSelection();
    try {
      clearError();
      const session = await request('/v1/sessions', {method: 'POST', body: '{}'});
      if (!ownsConnection(connectionEpoch, token) || state.selectionEpoch !== selectionEpoch) return;
      state.sessions.unshift(session); renderSessions(); await selectSession(session.id);
    } catch (error) {
      if (ownsConnection(connectionEpoch, token) && state.selectionEpoch === selectionEpoch) showError(error);
    }
  }

  async function selectSession(sessionId, preferredRun) {
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const selectionEpoch = beginSelection();
    clearError(); setPhase('selecting');
    try {
      const [session, runs] = await Promise.all([
        request(`/v1/sessions/${encodeURIComponent(sessionId)}`),
        request(`/v1/sessions/${encodeURIComponent(sessionId)}/runs`),
      ]);
      if (!ownsConnection(connectionEpoch, token) || state.selectionEpoch !== selectionEpoch) return;
      state.session = session;
      if (!state.sessions.some((item) => item.id === session.id)) state.sessions.push(session);
      state.runs = runs;
      state.runCursor = runs.length ? runs.at(-1).id : null;
      state.hasOlderRuns = runs.length === 50;
      byId('session-id').textContent = session.id; byId('generation').textContent = String(session.generation);
      state.run = null; state.lastSeq = 0;
      renderSessions(); renderRun(null);
      let chosen = runs[0];
      if (preferredRun) {
        chosen = await request(`/v1/runs/${encodeURIComponent(preferredRun)}`);
        if (!ownsSelection(connectionEpoch, token, selectionEpoch, session.id)) return;
        if (chosen.request.session_id !== session.id) {
          throw {error: {code: 'run_session_mismatch', message: 'The requested run does not belong to this session.'}};
        }
        const listed = state.runs.findIndex((run) => run.id === chosen.id);
        if (listed >= 0) state.runs[listed] = chosen;
        else state.runs.push(chosen);
        renderRunHistory();
      }
      if (chosen) activateRun(chosen, connectionEpoch, token, selectionEpoch, session.id);
      else { renderRun(null); setPhase('selecting'); }
    } catch (error) {
      if (ownsConnection(connectionEpoch, token) && state.selectionEpoch === selectionEpoch) {
        showError(error); renderRun(null); setPhase('selecting');
      }
    }
  }

  async function selectRun(runId) {
    if (!state.session) return;
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const sessionId = state.session.id; const selectionEpoch = beginSelection();
    clearError(); state.lastSeq = 0; state.reconnects = 0; renderRun(null); setPhase('observing');
    try {
      const run = await request(`/v1/runs/${encodeURIComponent(runId)}`);
      if (!ownsSelection(connectionEpoch, token, selectionEpoch, sessionId)) return;
      if (run.request.session_id !== sessionId) {
        throw {error: {code: 'run_session_mismatch', message: 'The requested run does not belong to this session.'}};
      }
      activateRun(run, connectionEpoch, token, selectionEpoch, sessionId);
    } catch (error) {
      if (ownsSelection(connectionEpoch, token, selectionEpoch, sessionId)) {
        showError(error); setPhase('selecting');
      }
    }
  }

  function activateRun(run, connectionEpoch, token, selectionEpoch, sessionId) {
    if (!ownsSelection(connectionEpoch, token, selectionEpoch, sessionId)) return;
    state.lastSeq = 0; state.reconnects = 0;
    renderRun(run); setPhase('observing');
    observe(run.id, connectionEpoch, token, selectionEpoch, sessionId);
  }

  function selected(name) {
    return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
  }

  async function startSubmission(event) {
    event.preventDefault();
    const text = byId('message').value;
    if (!state.session || !text.trim() || state.pendingSubmission) return;
    state.pendingSubmission = {
      payload: {
        session_id: state.session.id, generation: state.session.generation,
        request_id: crypto.randomUUID(), text, tools: selected('tools'), skills: selected('skills'),
      },
      connectionEpoch: state.connectionEpoch, selectionEpoch: state.selectionEpoch, inFlight: false,
    };
    await sendPending();
  }

  async function sendPending() {
    const pending = state.pendingSubmission;
    if (!pending || pending.inFlight) return;
    const payload = pending.payload;
    pending.inFlight = true;
    setPhase('submitting'); clearError();
    try {
      const run = await request('/v1/runs', {method: 'POST', body: JSON.stringify(payload)});
      if (state.pendingSubmission !== pending) return;
      state.pendingSubmission = null;
      if (!ownsConnection(pending.connectionEpoch, state.token)) return;
      updateControls();
      if (!ownsSelection(
        pending.connectionEpoch, state.token, pending.selectionEpoch, payload.session_id,
      )) return;
      byId('message').value = '';
      if (!state.runs.some((item) => item.id === run.id)) state.runs.unshift(run);
      activateRun(run, pending.connectionEpoch, state.token, pending.selectionEpoch, payload.session_id);
    } catch (error) {
      if (state.pendingSubmission !== pending) return;
      pending.inFlight = false;
      if (error.status) state.pendingSubmission = null;
      if (!ownsConnection(pending.connectionEpoch, state.token)) return;
      if (state.session && state.session.id === payload.session_id) showError(error);
      if (state.selectionEpoch === pending.selectionEpoch) setPhase('selecting');
      updateControls();
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

  async function applyEvent(item, owns) {
    if (!owns()) return;
    if (item.id <= state.lastSeq) return;
    state.lastSeq = item.id;
    const event = item.event;
    if (event.kind === 'run.queued' || event.kind === 'run.started') {
      state.run = {...state.run, status: event.kind === 'run.started' ? 'running' : 'queued'};
      const verification = state.run.verification === 'not_requested' ? 'No verification requested' :
        state.run.verification === 'passed' ? 'Verification passed' : 'Verification failed';
      byId('run-status').textContent = `Run: ${state.run.status} · ${verification}`;
      byId('run-status').dataset.status = state.run.status;
      renderRunHistory();
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
      await loadReceipts(owns);
    } else if (event.kind === 'approval.required') {
      state.run = {...state.run, status: 'waiting_approval'};
      byId('run-status').textContent = 'Run: waiting approval · No verification requested';
      byId('run-status').dataset.status = 'waiting_approval';
      updateControls();
      appendActivity(`Approval required: ${event.data.call.name}`, event.data);
      await refreshApprovals(owns);
    }
  }

  async function durableRun(runId, owns) {
    const run = await request(`/v1/runs/${encodeURIComponent(runId)}`);
    if (!owns()) return null;
    const partial = document.querySelector('.assistant-output')?.textContent || '';
    renderRun(run, true);
    if (!run.output && partial) document.querySelector('.assistant-output').textContent = partial;
    await loadReceipts(owns);
    return run;
  }

  async function observe(runId, connectionEpoch, token, selectionEpoch, sessionId) {
    if (!ownsSelection(connectionEpoch, token, selectionEpoch, sessionId) ||
        !state.run || state.run.id !== runId) return;
    invalidateObservation();
    const observationEpoch = state.observationEpoch;
    const controller = new AbortController(); state.stream = controller;
    const owns = () => ownsSelection(connectionEpoch, token, selectionEpoch, sessionId) &&
      state.observationEpoch === observationEpoch && state.stream === controller &&
      state.run && state.run.id === runId;
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
          if (item) await applyEvent(item, owns);
          if (!owns()) return;
        }
        if (done) break;
      }
      if (buffer.trim()) throw {error: {code: 'stream_frame', message: 'Event stream ended inside a frame.'}};
      const run = await durableRun(runId, owns);
      if (!owns()) return;
      if (!run || TERMINAL.has(run.status)) { state.reconnects = 0; setPhase('selecting'); return; }
      throw {error: {code: 'stream_ended', message: 'Event stream ended before durable completion.'}};
    } catch (error) {
      if (controller.signal.aborted || !owns()) return;
      showError(error);
      if (TERMINAL.has(state.run.status)) { setPhase('selecting'); return; }
      const status = error && error.status;
      if ([401, 403, 409, 422].includes(status) || state.reconnects >= 3) {
        setPhase('selecting'); return;
      }
      const delay = 250 * (2 ** state.reconnects++);
      await new Promise((resolve) => setTimeout(resolve, delay));
      if (owns()) observe(runId, connectionEpoch, token, selectionEpoch, sessionId);
    } finally {
      if (state.stream === controller) state.stream = null;
    }
  }

  async function cancelRun() {
    if (!state.run) return;
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const selectionEpoch = state.selectionEpoch; const sessionId = state.session.id; const runId = state.run.id;
    try {
      clearError();
      const partial = document.querySelector('.assistant-output')?.textContent || '';
      const run = await request(`/v1/runs/${encodeURIComponent(runId)}/cancel`, {method: 'POST', body: '{}'});
      if (!ownsSelection(connectionEpoch, token, selectionEpoch, sessionId) ||
          !state.run || state.run.id !== runId) return;
      renderRun(run, true);
      if (!run.output && partial) document.querySelector('.assistant-output').textContent = partial;
      const runs = await request(`/v1/sessions/${encodeURIComponent(sessionId)}/runs`);
      if (!ownsSelection(connectionEpoch, token, selectionEpoch, sessionId)) return;
      state.runs = runs; state.runCursor = runs.length ? runs.at(-1).id : null;
      state.hasOlderRuns = runs.length === 50; renderRunHistory();
    } catch (error) {
      if (ownsSelection(connectionEpoch, token, selectionEpoch, sessionId)) showError(error);
    }
  }

  async function resetSession() {
    if (!state.session) return;
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const selectionEpoch = state.selectionEpoch; const sessionId = state.session.id;
    const generation = state.session.generation;
    try {
      clearError();
      const session = await request(`/v1/sessions/${encodeURIComponent(sessionId)}/reset`, {
        method: 'POST', body: JSON.stringify({generation}),
      });
      if (!ownsSelection(connectionEpoch, token, selectionEpoch, sessionId)) return;
      state.session = session; byId('generation').textContent = String(session.generation);
      const listed = state.sessions.find((item) => item.id === session.id);
      if (listed) Object.assign(listed, session);
      renderSessions();
      const runs = await request(`/v1/sessions/${encodeURIComponent(sessionId)}/runs`);
      if (!ownsSelection(connectionEpoch, token, selectionEpoch, sessionId)) return;
      state.runs = runs; state.runCursor = runs.length ? runs.at(-1).id : null;
      state.hasOlderRuns = runs.length === 50; renderRunHistory(); saveHash(); updateControls();
    } catch (error) {
      if (ownsSelection(connectionEpoch, token, selectionEpoch, sessionId)) showError(error);
    }
  }

  async function loadOlderSessions() {
    if (!state.sessionCursor || !state.hasOlderSessions) return;
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const cursor = state.sessionCursor;
    try {
      const older = await request(`/v1/sessions?limit=50&before=${encodeURIComponent(cursor)}`);
      if (!ownsConnection(connectionEpoch, token) || state.sessionCursor !== cursor) return;
      for (const session of older) {
        if (!state.sessions.some((item) => item.id === session.id)) state.sessions.push(session);
      }
      if (older.length) state.sessionCursor = older.at(-1).id;
      state.hasOlderSessions = older.length === 50;
      renderSessions();
    } catch (error) {
      if (ownsConnection(connectionEpoch, token)) showError(error);
    }
  }

  async function loadOlderRuns() {
    if (!state.session || !state.runCursor || !state.hasOlderRuns) return;
    const connectionEpoch = state.connectionEpoch; const token = state.token;
    const selectionEpoch = state.selectionEpoch; const sessionId = state.session.id;
    const cursor = state.runCursor;
    try {
      const older = await request(
        `/v1/sessions/${encodeURIComponent(sessionId)}/runs?limit=50&before=${encodeURIComponent(cursor)}`,
      );
      if (!ownsSelection(connectionEpoch, token, selectionEpoch, sessionId) || state.runCursor !== cursor) return;
      for (const run of older) {
        if (!state.runs.some((item) => item.id === run.id)) state.runs.push(run);
      }
      if (older.length) state.runCursor = older.at(-1).id;
      state.hasOlderRuns = older.length === 50;
      renderRunHistory();
    } catch (error) {
      if (ownsSelection(connectionEpoch, token, selectionEpoch, sessionId)) showError(error);
    }
  }

  byId('connect').addEventListener('click', connect);
  byId('logout').addEventListener('click', logout);
  byId('new-session').addEventListener('click', createSession);
  byId('load-older-sessions').addEventListener('click', loadOlderSessions);
  byId('load-older-runs').addEventListener('click', loadOlderRuns);
  byId('composer').addEventListener('submit', startSubmission);
  byId('retry-send').addEventListener('click', sendPending);
  byId('cancel-run').addEventListener('click', cancelRun);
  byId('reset-session').addEventListener('click', resetSession);
  byId('refresh-approvals').addEventListener('click', () => refreshApprovals());
  byId('reconnect-stream').addEventListener('click', () => {
    if (state.run && state.session) {
      state.reconnects = 0; clearError();
      observe(
        state.run.id, state.connectionEpoch, state.token, state.selectionEpoch, state.session.id,
      );
    }
  });
  setPhase('disconnected');
})();
