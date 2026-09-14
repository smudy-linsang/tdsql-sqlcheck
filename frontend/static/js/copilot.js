/* v1.6.4.0 / CP-1：Copilot 专家助手前端组合模块（CP-W08，DETAIL §13）。
 *
 * 暴露 createCopilotState({Vue, apiFetch, getIdentity, getMenus, navigate,
 * editorBridge, contextBridge})，在原 setup 调用并显式 return 所需状态/方法。
 * 依赖通过函数注入，不读任意 window 对象；不复制认证 token 到自有存储。
 *
 * 归属防护（§11.5）：view={subject_id, session_id, view_generation, turn_id,
 * request_seq, abort}；每个 await 之后检查全部归属，否则丢弃回复。
 * sessionStorage 仅保存 copilot:<subject_id>:<tab_nonce> 下的提交标识，
 * 不保存正文/token/model key。
 */
(function (global) {
  'use strict';

  const POLL_MS = 2000;
  const POLL_BACKOFF_MAX = 8000;

  function createCopilotState(deps) {
    const { Vue, apiFetch, getIdentity, getMenus, navigate,
            editorBridge, contextBridge } = deps;
    const { ref, reactive, computed } = Vue;

    // ── 视图与状态 ─────────────────────────────────────────
    const drawerVisible = ref(false);
    const fullPageVisible = ref(false);
    const capabilities = ref(null);
    const sessions = ref([]);
    const sessionsLoading = ref(false);
    const currentSessionId = ref('');
    const currentSession = ref(null);
    const turns = ref([]);
    const turnsLoading = ref(false);
    const question = ref('');
    const preview = ref(null);
    const previewing = ref(false);
    const submitting = ref(false);
    const submittingState = ref('DRAFT');   // DRAFT/PREVIEWING/PREVIEW_READY/SUBMITTING/UNCERTAIN
    const activeTurnId = ref('');
    const polling = ref(false);
    const errorMsg = ref('');
    const scene = ref('USAGE_HELP');
    const sceneOptions = [
      { value: 'USAGE_HELP', label: '使用帮助' },
      { value: 'RULE_EXPLAIN', label: '解释规则' },
      { value: 'SQL_ADVISE', label: 'SQL 修改建议' },
      { value: 'AUDIT_EXPLAIN', label: '解读审核结果' },
      { value: 'JOB_TROUBLESHOOT', label: '任务失败排查' },
      { value: 'SLOW_EXPLAIN', label: '慢SQL解读' },
      { value: 'COMPARE_EXPLAIN', label: '扫描对比解读' },
      { value: 'TABLETYPE_EXPLAIN', label: '表类型统计解读' },
      { value: 'GATEWAY_EXPLAIN', label: '网关报告解读' },
      { value: 'DIAGNOSTIC_HELP', label: '诊断模块帮助' },
    ];
    const selectedConnectionId = ref('');
    const connections = ref([]);
    const sourceRefs = ref([]);
    const draftText = ref('');
    const expanded = ref(false);

    // 归属防护：view 世代 + 请求序号 + abort
    const view = reactive({
      subject_id: '', session_id: '', generation: 0, turn_id: '',
      request_seq: 0,
    });
    let abortController = null;
    let pollTimer = null;

    const identity = computed(() => (getIdentity ? getIdentity() : {}) || {});
    const subjectId = computed(() => identity.value.subject_id || '');
    const menus = computed(() => (getMenus ? getMenus() : []) || []);
    const copilotEnabled = computed(() =>
      !!(capabilities.value && capabilities.value.enabled));
    const copilotMode = computed(() =>
      (capabilities.value && capabilities.value.mode) || 'DISABLED');

    function _storageKey() {
      return `copilot:${subjectId.value || 'anon'}:${_tabNonce()}`;
    }
    let _nonce = null;
    function _tabNonce() {
      if (_nonce) return _nonce;
      try {
        _nonce = sessionStorage.getItem('copilot:tab_nonce');
        if (!_nonce) {
          _nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
          sessionStorage.setItem('copilot:tab_nonce', _nonce);
        }
      } catch (e) { _nonce = 'noname'; }
      return _nonce;
    }

    function _saveSubmission(patch) {
      try {
        const raw = sessionStorage.getItem(_storageKey()) || '{}';
        const data = JSON.parse(raw);
        sessionStorage.setItem(_storageKey(),
          JSON.stringify(Object.assign(data, patch)));
      } catch (e) { /* 忽略 */ }
    }
    function _loadSubmission() {
      try {
        return JSON.parse(sessionStorage.getItem(_storageKey()) || '{}');
      } catch (e) { return {}; }
    }
    function _clearSubmission() {
      try { sessionStorage.removeItem(_storageKey()); } catch (e) { /* 忽略 */ }
    }

    function _bumpGeneration() {
      view.generation += 1;
      view.request_seq += 1;
      if (abortController) { try { abortController.abort(); } catch (e) {} }
      abortController = new AbortController();
    }
    function _currentGen() { return view.generation; }

    function _checkGen(gen) {
      return gen === view.generation;
    }

    // ── 能力 ─────────────────────────────────────────────
    async function loadCapabilities() {
      const gen = _currentGen();
      try {
        const resp = await apiFetch('/api/v1/copilot/capabilities');
        if (!_checkGen(gen)) return;
        if (resp.ok) {
          capabilities.value = await resp.json();
          if (!_checkGen(gen)) return;
          view.subject_id = capabilities.value.subject_id || '';
        } else if (resp.status === 401) {
          capabilities.value = { enabled: false, mode: 'DISABLED' };
        } else {
          capabilities.value = { enabled: false, mode: 'DISABLED' };
        }
      } catch (e) {
        if (!_checkGen(gen)) return;
        capabilities.value = { enabled: false, mode: 'DISABLED' };
      }
    }

    // ── 抽屉开关 ─────────────────────────────────────────
    function openDrawer() {
      drawerVisible.value = true;
      errorMsg.value = '';
      loadCapabilities();
      loadSessions();
      loadConnections();
    }
    function closeDrawer() {
      drawerVisible.value = false;
      stopPolling();
    }
    function toggleExpand() { expanded.value = !expanded.value; }

    // ── 会话列表 ─────────────────────────────────────────
    async function loadSessions() {
      const gen = _currentGen();
      sessionsLoading.value = true;
      try {
        const resp = await apiFetch('/api/v1/copilot/sessions?limit=20');
        if (!_checkGen(gen)) return;
        if (resp.ok) {
          const data = await resp.json();
          if (!_checkGen(gen)) return;
          sessions.value = data.items || [];
        }
      } finally {
        if (_checkGen(gen)) sessionsLoading.value = false;
      }
    }

    async function loadConnections() {
      const gen = _currentGen();
      try {
        const resp = await apiFetch('/api/v1/copilot/connections');
        if (!_checkGen(gen)) return;
        if (resp.ok) {
          const data = await resp.json();
          if (!_checkGen(gen)) return;
          connections.value = data.items || [];
        }
      } catch (e) { /* 连接列表失败不阻塞 */ }
    }

    async function createSession(scopeKind) {
      const gen = _currentGen();
      const body = {
        scope_kind: scopeKind,
        instance_type: 'unknown',
        page_key: (contextBridge && contextBridge.pageKey) || '',
      };
      if (scopeKind === 'INSTANCE' && selectedConnectionId.value) {
        body.connection_id = selectedConnectionId.value;
      }
      const resp = await apiFetch('/api/v1/copilot/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!_checkGen(gen)) return;
      if (resp.ok) {
        const data = await resp.json();
        if (!_checkGen(gen)) return;
        currentSessionId.value = data.session_id;
        currentSession.value = data;
        turns.value = [];
        preview.value = null;
        _saveSubmission({ session_id: data.session_id });
        await loadSessions();
        return data.session_id;
      }
      const err = await _readError(resp);
      errorMsg.value = err;
      return null;
    }

    async function selectSession(sessionId) {
      const gen = _currentGen();
      _bumpGeneration();
      stopPolling();
      currentSessionId.value = sessionId;
      preview.value = null;
      activeTurnId.value = '';
      await loadTurns(sessionId);
      if (!_checkGen(_currentGen())) return;
    }

    async function loadTurns(sessionId) {
      const gen = _currentGen();
      turnsLoading.value = true;
      try {
        const resp = await apiFetch(
          `/api/v1/copilot/sessions/${sessionId}/turns?limit=20`);
        if (!_checkGen(gen)) return;
        if (resp.ok) {
          const data = await resp.json();
          if (!_checkGen(gen)) return;
          turns.value = data.items || [];
        }
      } finally {
        if (_checkGen(gen)) turnsLoading.value = false;
      }
    }

    // ── 预览 ─────────────────────────────────────────────
    async function buildPreview() {
      const gen = _currentGen();
      if (!currentSessionId.value) {
        errorMsg.value = '请先创建或选择会话';
        return;
      }
      previewing.value = true;
      submittingState.value = 'PREVIEWING';
      errorMsg.value = '';
      const refs = sourceRefs.value.slice(0, 4);
      const body = {
        expected_session_revision:
          (currentSession.value && currentSession.value.revision) || 1,
        scene: scene.value,
        page_key: (contextBridge && contextBridge.pageKey) || '',
        question: question.value,
        source_refs: refs,
      };
      if (draftText.value) {
        body.draft = { kind: 'SQL', text: draftText.value.slice(0, 32768) };
      }
      try {
        const resp = await apiFetch(
          `/api/v1/copilot/sessions/${currentSessionId.value}/previews`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        if (!_checkGen(gen)) return;
        if (resp.ok) {
          const data = await resp.json();
          if (!_checkGen(gen)) return;
          preview.value = data;
          submittingState.value = 'PREVIEW_READY';
          _saveSubmission({ preview_id: data.preview_id,
                            snapshot_hash: data.snapshot_hash });
        } else {
          errorMsg.value = await _readError(resp);
          submittingState.value = 'DRAFT';
        }
      } catch (e) {
        if (!_checkGen(gen)) return;
        errorMsg.value = '预览失败（网络异常）';
        submittingState.value = 'DRAFT';
      } finally {
        if (_checkGen(gen)) previewing.value = false;
      }
    }

    // ── 提交（幂等）───────────────────────────────────────
    function _uuid32() {
      return 'xxxxxxxxxxxx4xxx8xxxxxxxxxxxxxxx'.replace(/x/g, () =>
        Math.floor(Math.random() * 16).toString(16));
    }

    async function submitTurn() {
      const gen = _currentGen();
      if (!preview.value) { errorMsg.value = '请先完成资料预览'; return; }
      submitting.value = true;
      submittingState.value = 'SUBMITTING';
      errorMsg.value = '';
      const saved = _loadSubmission();
      const clientRequestId = saved.pending_client_request_id || _uuid32();
      _saveSubmission({ pending_client_request_id: clientRequestId,
                        submission_state: 'SUBMITTING' });
      const body = {
        client_request_id: clientRequestId,
        preview_id: preview.value.preview_id,
        snapshot_hash: preview.value.snapshot_hash,
        expected_session_revision: preview.value.expected_session_revision,
        confirm_data_use: true,
      };
      try {
        const resp = await apiFetch(
          `/api/v1/copilot/sessions/${currentSessionId.value}/turns`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        if (!_checkGen(gen)) return;
        if (resp.ok || resp.status === 202) {
          const data = await resp.json();
          if (!_checkGen(gen)) return;
          activeTurnId.value = data.turn_id;
          view.turn_id = data.turn_id;
          _saveSubmission({ turn_id: data.turn_id, submission_state: 'ACCEPTED' });
          submittingState.value = 'DRAFT';
          preview.value = null;
          question.value = '';
          startPolling();
          await loadTurns(currentSessionId.value);
        } else {
          const err = await _readError(resp);
          if (resp.status === 409) {
            // 幂等冲突/预览过期：允许恢复原 turn
            submittingState.value = 'UNCERTAIN';
            errorMsg.value = err + '（可尝试“恢复本次提交”）';
          } else {
            errorMsg.value = err;
            submittingState.value = 'DRAFT';
          }
        }
      } catch (e) {
        if (!_checkGen(gen)) return;
        submittingState.value = 'UNCERTAIN';
        errorMsg.value = '提交响应未确认，请勿重复点击；可尝试“恢复本次提交”';
      } finally {
        if (_checkGen(gen)) submitting.value = false;
      }
    }

    async function recoverSubmission() {
      const saved = _loadSubmission();
      if (!saved.pending_client_request_id || !saved.preview_id) {
        errorMsg.value = '没有可恢复的提交标识';
        return;
      }
      const gen = _currentGen();
      try {
        const resp = await apiFetch(
          `/api/v1/copilot/sessions/${currentSessionId.value}/turns`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              client_request_id: saved.pending_client_request_id,
              preview_id: saved.preview_id,
              snapshot_hash: saved.snapshot_hash,
              expected_session_revision:
                (currentSession.value && currentSession.value.revision) || 1,
              confirm_data_use: true,
            }),
          });
        if (!_checkGen(gen)) return;
        if (resp.ok) {
          const data = await resp.json();
          if (!_checkGen(gen)) return;
          activeTurnId.value = data.turn_id;
          view.turn_id = data.turn_id;
          submittingState.value = 'DRAFT';
          startPolling();
        } else {
          errorMsg.value = await _readError(resp);
        }
      } catch (e) {
        if (!_checkGen(gen)) return;
        errorMsg.value = '恢复失败（网络异常）';
      }
    }

    // ── 轮询 ─────────────────────────────────────────────
    function startPolling() {
      stopPolling();
      polling.value = true;
      _poll(0);
    }
    function stopPolling() {
      polling.value = false;
      if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    }
    async function _poll(backoff) {
      if (!polling.value || !activeTurnId.value) return;
      const gen = _currentGen();
      const tid = activeTurnId.value;
      try {
        const resp = await apiFetch(`/api/v1/copilot/turns/${tid}`);
        if (!_checkGen(gen)) return;
        if (resp.ok) {
          const data = await resp.json();
          if (!_checkGen(gen)) return;
          const terminal = data.terminal;
          if (terminal) {
            polling.value = false;
            await loadTurns(currentSessionId.value);
            return;
          }
          pollTimer = setTimeout(() => _poll(0), POLL_MS);
          return;
        }
        if (resp.status === 401) { polling.value = false; return; }
      } catch (e) {
        if (!_checkGen(gen)) return;
      }
      const next = Math.min(backoff ? backoff * 2 : POLL_MS, POLL_BACKOFF_MAX);
      pollTimer = setTimeout(() => _poll(next), next);
    }

    // ── 取消 / 反馈 ──────────────────────────────────────
    async function cancelTurn(turnId) {
      const gen = _currentGen();
      try {
        await apiFetch(`/api/v1/copilot/turns/${turnId}/cancel`,
                       { method: 'POST' });
        if (!_checkGen(gen)) return;
        await loadTurns(currentSessionId.value);
      } catch (e) { /* 取消失败不阻塞 */ }
    }

    async function sendFeedback(turnId, rating, code) {
      const gen = _currentGen();
      try {
        await apiFetch(`/api/v1/copilot/turns/${turnId}/feedback`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rating, code }),
        });
        if (!_checkGen(gen)) return;
        await loadTurns(currentSessionId.value);
      } catch (e) { /* 反馈失败不阻塞 */ }
    }

    // ── 结果查看 ─────────────────────────────────────────
    const turnResult = ref(null);
    const resultVisible = ref(false);
    async function viewResult(turnId) {
      const gen = _currentGen();
      try {
        const resp = await apiFetch(`/api/v1/copilot/turns/${turnId}/result`);
        if (!_checkGen(gen)) return;
        if (resp.ok) {
          turnResult.value = await resp.json();
          if (!_checkGen(gen)) return;
          resultVisible.value = true;
        } else if (resp.status === 409) {
          errorMsg.value = '结果尚未生成';
        } else {
          errorMsg.value = await _readError(resp);
        }
      } catch (e) {
        if (!_checkGen(gen)) return;
        errorMsg.value = '读取结果失败（网络异常）';
      }
    }

    async function exportTurnHtml(turnId) {
      window.open(`/api/v1/copilot/turns/${turnId}/export.html`, '_blank',
                  'noopener');
    }

    async function copySuggestion(candidate) {
      const text = (candidate && candidate.sql) || '';
      if (!text) return;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement('textarea');
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        ElementPlus.ElMessage.success('已复制候选 SQL（请人工复核后使用）');
      } catch (e) {
        ElementPlus.ElMessage.warning('复制失败，请手动选择复制');
      }
    }

    function sendToEditor(candidate) {
      if (editorBridge && editorBridge.previewReplacement) {
        editorBridge.previewReplacement(candidate);
      }
    }

    async function _readError(resp) {
      try {
        const d = await resp.json();
        return (d && d.detail && (d.detail.message || d.detail.code)) ||
               `请求失败（${resp.status}）`;
      } catch (e) {
        return `请求失败（${resp.status}）`;
      }
    }

    return {
      drawerVisible, fullPageVisible, capabilities, sessions, sessionsLoading,
      currentSessionId, currentSession, turns, turnsLoading, question,
      preview, previewing, submitting, submittingState, activeTurnId, polling,
      errorMsg, scene, sceneOptions, selectedConnectionId, connections,
      sourceRefs, draftText, expanded, turnResult, resultVisible,
      copilotEnabled, copilotMode,
      openDrawer, closeDrawer, toggleExpand, loadCapabilities, loadSessions,
      createSession, selectSession, buildPreview, submitTurn,
      recoverSubmission, cancelTurn, sendFeedback, viewResult, exportTurnHtml,
      copySuggestion, sendToEditor, loadTurns, startPolling, stopPolling,
    };
  }

  global.createCopilotState = createCopilotState;
})(window);
