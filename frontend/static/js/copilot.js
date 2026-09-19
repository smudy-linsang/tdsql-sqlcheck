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

    // ── 类 DB_Monitor 对话流状态 ───────────────────────────
    const chatActiveTab = ref('chat'); // 'chat' | 'turns'
    const chatInput = ref('');
    const chatLoading = ref(false);
    const messages = ref([
      {
        role: 'assistant',
        content: '👋 您好！我是 **TDSQL 数据库专属 Copilot 专家助手**。请直接在下方输入您的问题，我将为您提供只读安全的专业分析与诊断。',
        time: new Date().toLocaleTimeString(),
        model: '只读安全',
        provider_name: 'Copilot'
      }
    ]);

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

    // QC1-B01：身份变更时重置全部私有状态
    function resetForIdentityChange(oldSubject, newSubject, reason) {
      _bumpGeneration();
      stopPolling();
      // 清除旧 subject 的 sessionStorage
      if (oldSubject) {
        try { sessionStorage.removeItem('copilot:' + oldSubject + ':' + _tabNonce()); } catch (e) {}
      }
      // 清空全部私有状态
      question.value = '';
      preview.value = null;
      previewing.value = false;
      submitting.value = false;
      submittingState.value = 'DRAFT';
      activeTurnId.value = '';
      polling.value = false;
      errorMsg.value = '';
      sourceRefs.value = [];
      draftText.value = '';
      turnResult.value = null;
      resultVisible.value = false;
      currentSessionId.value = '';
      currentSession.value = null;
      turns.value = [];
      sessions.value = [];
      selectedConnectionId.value = '';
      capabilities.value = null;
      view.subject_id = newSubject || '';
      view.session_id = '';
      view.turn_id = '';
      drawerVisible.value = false;
      fullPageVisible.value = false;
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
      _clearSubmission();  // QC1-B05：切会话清除旧提交标识
      // QC1-B05：选择会话时同时加载 session 详情（刷新 revision）
      try {
        const sResp = await apiFetch(`/api/v1/copilot/sessions/${sessionId}`);
        if (_checkGen(_currentGen()) && sResp.ok) {
          currentSession.value = await sResp.json();
        }
      } catch (e) { /* 不阻塞 */ }
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
        body.draft = { kind: 'SQL', text: draftText.value.slice(0, 32768),
                       revision: String((contextBridge && contextBridge.getCurrentSelection
                         ? contextBridge.getCurrentSelection().draft_revision : null) || '0') };
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
      // QC1-B05：每个新意图生成新 client_request_id（只有 UNCERTAIN 恢复才复用）
      const clientRequestId = _uuid32();
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
            _clearSubmission();  // QC1-B05：终态后清除提交标识
            // QC1-B05：终态后刷新 session revision
            try {
              const sResp = await apiFetch(`/api/v1/copilot/sessions/${currentSessionId.value}`);
              if (_checkGen(gen) && sResp.ok) currentSession.value = await sResp.json();
            } catch (e) { /* 不阻塞 */ }
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
      // QC1-M01：用 apiFetch 带 Bearer 获取 blob，不再 window.open 裸链接
      const gen = _currentGen();
      try {
        const resp = await apiFetch(`/api/v1/copilot/turns/${turnId}/export.html`);
        if (!_checkGen(gen)) return;
        if (!resp.ok) {
          errorMsg.value = await _readError(resp);
          return;
        }
        const blob = await resp.blob();
        if (!_checkGen(gen)) return;
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `copilot-advice-${turnId.slice(0, 8)}.html`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 5000);
      } catch (e) {
        if (!_checkGen(gen)) return;
        errorMsg.value = '导出失败（网络异常）';
      }
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
      // QC1-B06：候选 SQL 不直接覆盖草稿，先确认 + revision 检查
      if (!editorBridge) return;
      const text = (candidate && candidate.sql) || '';
      if (!text) return;
      const currentRev = editorBridge.readRevision ? editorBridge.readRevision() : '0';
      const validation = (candidate && candidate.validation) || {};
      const stateLabel = validation.validation === 'TEXT_PASSED' ? '文本复核通过'
        : validation.validation === 'BLOCKED' ? '存在阻断违规'
        : validation.validation === 'PARSE_FAILED' ? '解析失败'
        : '未完成复核';
      ElementPlus.ElMessageBox.confirm(
        `候选 SQL 状态：${stateLabel}\n` +
        (validation.executable === 'NO' ? '存在阻断级违规，不建议直接执行。\n' : '') +
        `将替换审核编辑器中当前草稿（revision ${currentRev}）。确认继续？`,
        '送入审核编辑器',
        { confirmButtonText: '确认替换', cancelButtonText: '取消', type: 'warning' }
      ).then(() => {
        if (editorBridge.applyDraftIfRevision) {
          const ok = editorBridge.applyDraftIfRevision(currentRev, text);
          if (ok) {
            ElementPlus.ElMessage.success('已送入编辑器（请人工复核后使用）');
          }
        } else if (editorBridge.previewReplacement) {
          editorBridge.previewReplacement(candidate);
        }
      }).catch(() => { /* 用户取消，草稿零改动 */ });
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

    // ── 页面初始化（侧栏进入时调用）──────────────────────────
    async function initPage() {
      await Promise.all([loadCapabilities(), loadConnections(), loadSessions()]);
    }

    // QC1-B04：业务页上下文桥原子入口——打开抽屉前导入业务选择
    function applyBusinessContext(selection) {
      if (!selection) return;
      // 映射目标 scene
      if (selection.scene) scene.value = selection.scene;
      // 复制来源 IDs
      if (selection.source_refs && selection.source_refs.length) {
        sourceRefs.value = selection.source_refs.slice(0, 4);
      }
      // 复制草稿和 revision
      if (selection.draft) {
        draftText.value = selection.draft;
      }
      // 同步 pageKey
      if (contextBridge && selection.page_key) {
        contextBridge.pageKey = selection.page_key;
      }
      // 清旧 preview/submit journal
      preview.value = null;
      _clearSubmission();
      errorMsg.value = '';
    }

    async function sendChat(customText) {
      const q = (customText || chatInput.value || question.value || '').trim();
      if (!q || chatLoading.value) return;

      const userMsg = {
        role: 'user',
        content: q,
        time: new Date().toLocaleTimeString()
      };
      messages.value.push(userMsg);
      if (!customText) {
        chatInput.value = '';
        question.value = '';
      }
      chatLoading.value = true;
      errorMsg.value = '';

      Vue.nextTick(() => {
        try {
          const listEls = document.querySelectorAll('.copilot-chat-feed');
          listEls.forEach(el => { el.scrollTop = el.scrollHeight; });
        } catch (e) {}
      });

      try {
        const history = messages.value
          .filter(m => m.role === 'user' || m.role === 'assistant')
          .slice(-6)
          .map(m => ({ role: m.role, content: m.content }));

        const resp = await apiFetch('/api/v1/copilot/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query: q,
            connection_id: selectedConnectionId.value || undefined,
            history: history
          })
        });

        if (resp && resp.ok) {
          const res = await resp.json();
          const aiMsg = {
            role: 'assistant',
            content: res.answer || '抱歉，暂时未能生成有效回复。',
            time: new Date().toLocaleTimeString(),
            model: res.model,
            provider_name: res.provider_name,
            latency_ms: res.latency_ms,
            action_cards: res.action_cards || [],
            safety_status: res.safety_status
          };
          messages.value.push(aiMsg);
        } else {
          let errDetail = '';
          try {
            const errJson = await resp.json();
            errDetail = (errJson && (errJson.detail || errJson.message || errJson.answer)) || '';
          } catch (e) {}
          messages.value.push({
            role: 'assistant',
            content: errDetail ? `⚠️ 请求未能成功返回: ${errDetail}` : '⚠️ 请求未能成功返回，请稍后再试。',
            time: new Date().toLocaleTimeString(),
            model: 'error'
          });
        }
      } catch (err) {
        messages.value.push({
          role: 'assistant',
          content: '⚠️ 对话请求发生异常: ' + (err.message || '网络连接或服务端超时'),
          time: new Date().toLocaleTimeString(),
          model: 'error'
        });
      } finally {
        chatLoading.value = false;
        Vue.nextTick(() => {
          try {
            const listEls = document.querySelectorAll('.copilot-chat-feed');
            listEls.forEach(el => { el.scrollTop = el.scrollHeight; });
          } catch (e) {}
        });
      }
    }

    function clearChat() {
      messages.value = [
        {
          role: 'assistant',
          content: '👋 对话已清空。请直接在下方输入您的问题，我将为您提供只读安全的专业分析与诊断。',
          time: new Date().toLocaleTimeString(),
          model: '只读安全',
          provider_name: 'Copilot'
        }
      ];
    }

    function executeActionCard(card) {
      if (!card) return;
      if (card.card_type === 'SQL_SUGGESTION' && card.sql) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(card.sql).then(() => {
            if (window.ElementPlus && window.ElementPlus.ElMessage) {
              window.ElementPlus.ElMessage.success('只读 SQL 已成功复制到剪贴板！');
            }
          }).catch(() => {});
        } else {
          const ta = document.createElement('textarea');
          ta.value = card.sql;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          if (window.ElementPlus && window.ElementPlus.ElMessage) {
            window.ElementPlus.ElMessage.success('只读 SQL 已成功复制到剪贴板！');
          }
        }
        return;
      }
      if (card.card_type === 'NAVIGATE_EDITOR' && card.sql) {
        if (editorBridge && editorBridge.previewReplacement) {
          editorBridge.previewReplacement({ sql: card.sql });
        }
        if (navigate) {
          navigate('audit-sql');
        }
        if (drawerVisible.value) {
          closeDrawer();
        }
        if (window.ElementPlus && window.ElementPlus.ElMessage) {
          window.ElementPlus.ElMessage.success('已将诊断 SQL 送入 SQL 审核编辑器');
        }
        return;
      }
      if (card.card_type === 'NAVIGATE' && card.target_page) {
        if (navigate) {
          navigate(card.target_page);
        }
        if (drawerVisible.value) {
          closeDrawer();
        }
      }
    }

    return {
      drawerVisible, fullPageVisible, capabilities, sessions, sessionsLoading,
      currentSessionId, currentSession, turns, turnsLoading, question,
      preview, previewing, submitting, submittingState, activeTurnId, polling,
      errorMsg, scene, sceneOptions, selectedConnectionId, connections,
      sourceRefs, draftText, expanded, turnResult, resultVisible,
      copilotEnabled, copilotMode,
      // 对话流专用导出
      chatActiveTab, chatInput, chatLoading, messages, sendChat, clearChat, executeActionCard,
      openDrawer, closeDrawer, toggleExpand, loadCapabilities, loadSessions,
      createSession, selectSession, buildPreview, submitTurn,
      recoverSubmission, cancelTurn, sendFeedback, viewResult, exportTurnHtml,
      copySuggestion, sendToEditor, loadTurns, startPolling, stopPolling,
      initPage, resetForIdentityChange, applyBusinessContext,
    };
  }

  global.createCopilotState = createCopilotState;
})(window);
