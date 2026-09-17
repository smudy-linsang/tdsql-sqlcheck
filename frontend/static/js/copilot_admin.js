/* v1.6.4.0 / CP-1：AI 配置管理端组合模块（DETAIL §12.5，QC1-B03 完整重写）。
   依赖注入：Vue / apiFetch / getIdentity。
   管理 /api/v1/copilot-admin/* 与 /api/v1/copilot-audit/events。
   QC1-B03：新增 provider 增改、路由配置、授权申请/审批/撤销、运行设置表单。 */
(function (global) {
  'use strict';

  var SCENES = [
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
  var PRIVACY_PROFILES = [
    { value: 'INTERNAL_REDACTED', label: '内部脱敏' },
    { value: 'INTERNAL_FULL', label: '内部完整' },
    { value: 'PUBLIC_HELP', label: '公共帮助' },
  ];
  var AUTH_MODES = [
    { value: 'NETWORK_IDENTITY', label: '网络身份（内网免密钥）' },
    { value: 'BEARER_KEY', label: 'Bearer 密钥' },
  ];

  function createCopilotAdminState(deps) {
    var Vue = deps.Vue;
    var ref = Vue.ref, reactive = Vue.reactive, computed = Vue.computed;
    var apiFetch = deps.apiFetch;

    var tab = ref('providers');
    var providers = ref([]);
    var routes = ref([]);
    var grants = ref([]);
    var endpoints = ref([]);
    var health = ref(null);
    var settings = ref(null);
    var loading = ref(false);
    var errorMsg = ref('');
    var saving = ref(false);

    // ── Provider 表单 ──
    var providerDrawer = ref(false);
    var providerForm = reactive({
      id: '', name: '', endpoint_id: '', model_id: '',
      protocol: 'OPENAI_COMPAT_CHAT', auth_mode: 'NETWORK_IDENTITY',
      context_tokens: 8192, secret_action: 'KEEP', secret: '',
      isEdit: false, expected_revision: 1,
    });
    var providerFormError = ref('');

    // ── 路由表单 ──
    var routeDrawer = ref(false);
    var routeForm = reactive({
      scene_code: '', primary_provider_id: '', fallback_provider_id: '',
      privacy_profile: 'INTERNAL_REDACTED', expected_revision: 1,
    });
    var routeFormError = ref('');

    // ── 授权表单 ──
    var grantDrawer = ref(false);
    var grantForm = reactive({
      username: '', connection_id: '', intent: 'REQUEST',
      approval_ref: '', allow_schema_identifiers: false,
      identifier_approval_ref: '',
    });
    var grantFormError = ref('');

    // ── 自检轮询 ──
    var selfTestPolling = ref(false);
    var _selfTestTimer = null;

    function _rid() {
      return Array.from(crypto.getRandomValues(new Uint8Array(16)))
        .map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    }

    async function _readErr(resp) {
      try {
        var d = await resp.json();
        return (d && d.detail && (d.detail.message || d.detail.code)) ||
          (d && d.message) || ('请求失败（' + resp.status + '）');
      } catch (e) { return '请求失败（' + resp.status + '）'; }
    }

    // ═══ 数据加载 ═══
    async function loadHealth() {
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/health');
        if (resp.ok) health.value = await resp.json();
        else if (resp.status === 503) health.value = await resp.json();
      } catch (e) { /* 静默 */ }
    }

    async function loadEndpoints() {
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/endpoints');
        if (resp.ok) {
          var d = await resp.json();
          endpoints.value = d.items || [];
        }
      } catch (e) { /* 静默 */ }
    }

    async function loadProviders() {
      loading.value = true;
      errorMsg.value = '';
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/providers');
        if (resp.ok) {
          var d = await resp.json();
          providers.value = d.items || [];
        } else {
          errorMsg.value = await _readErr(resp);
        }
      } catch (e) {
        errorMsg.value = '请求失败（' + e.message + '）';
      } finally { loading.value = false; }
    }

    async function loadRoutes() {
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/routes');
        if (resp.ok) {
          var d = await resp.json();
          routes.value = d.items || [];
        }
      } catch (e) { /* 静默 */ }
    }

    async function loadGrants() {
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants?limit=50');
        if (resp.ok) {
          var d = await resp.json();
          grants.value = d.items || [];
        }
      } catch (e) { /* 静默 */ }
    }

    async function loadSettings() {
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/settings');
        if (resp.ok) settings.value = await resp.json();
      } catch (e) { /* 静默 */ }
    }

    // ═══ Provider CRUD ═══
    function openProviderCreate() {
      providerForm.id = ''; providerForm.name = '';
      providerForm.endpoint_id = endpoints.value.length ? endpoints.value[0].endpoint_id : '';
      providerForm.model_id = ''; providerForm.auth_mode = 'NETWORK_IDENTITY';
      providerForm.context_tokens = 8192;
      providerForm.secret_action = 'KEEP'; providerForm.secret = '';
      providerForm.isEdit = false; providerForm.expected_revision = 1;
      providerFormError.value = '';
      providerDrawer.value = true;
    }

    function openProviderEdit(row) {
      providerForm.id = row.id; providerForm.name = row.name;
      providerForm.endpoint_id = row.endpoint_id;
      providerForm.model_id = row.model_id; providerForm.auth_mode = row.auth_mode;
      providerForm.context_tokens = (row.capabilities && row.capabilities.context_tokens) || 8192;
      providerForm.secret_action = 'KEEP'; providerForm.secret = '';
      providerForm.isEdit = true; providerForm.expected_revision = row.revision;
      providerFormError.value = '';
      providerDrawer.value = true;
    }

    async function saveProvider() {
      providerFormError.value = '';
      if (!providerForm.name.trim()) { providerFormError.value = '名称不能为空'; return; }
      if (!providerForm.endpoint_id) { providerFormError.value = '请选择端点'; return; }
      if (!providerForm.model_id.trim()) { providerFormError.value = '模型 ID 不能为空'; return; }
      saving.value = true;
      try {
        var body = {
          name: providerForm.name.trim(),
          endpoint_id: providerForm.endpoint_id,
          protocol: 'OPENAI_COMPAT_CHAT',
          model_id: providerForm.model_id.trim(),
          auth_mode: providerForm.auth_mode,
          capabilities: { context_tokens: providerForm.context_tokens },
          secret_action: providerForm.secret_action,
        };
        if (providerForm.secret_action === 'REPLACE' && providerForm.secret) {
          body.secret = providerForm.secret;
        }
        var resp;
        if (providerForm.isEdit) {
          body.expected_revision = providerForm.expected_revision;
          resp = await apiFetch('/api/v1/copilot-admin/providers/' + providerForm.id, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        } else {
          resp = await apiFetch('/api/v1/copilot-admin/providers', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        }
        if (resp.ok || resp.status === 201) {
          ElementPlus.ElMessage.success(providerForm.isEdit ? '已更新' : '已创建');
          providerDrawer.value = false;
          await loadProviders();
        } else {
          providerFormError.value = await _readErr(resp);
        }
      } catch (e) {
        providerFormError.value = '请求失败（' + e.message + '）';
      } finally { saving.value = false; }
    }

    // ═══ 自检 + 轮询 ═══
    async function runSelfTest(row) {
      try {
        var resp = await apiFetch(
          '/api/v1/copilot-admin/providers/' + row.id + '/self-tests', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              client_request_id: _rid(),
              expected_provider_revision: row.revision,
            }),
          });
        if (resp.ok || resp.status === 202) {
          var d = await resp.json();
          ElementPlus.ElMessage.success('自检已受理（turn_id: ' + d.turn_id + '）');
          _startSelfTestPoll(row.id, d.turn_id);
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('自检请求失败：' + e.message);
      }
    }

    function _startSelfTestPoll(providerId, turnId) {
      _stopSelfTestPoll();
      selfTestPolling.value = true;
      var attempts = 0;
      function _check() {
        if (!selfTestPolling.value || attempts > 30) {
          selfTestPolling.value = false;
          loadProviders();
          return;
        }
        attempts++;
        apiFetch('/api/v1/copilot/turns/' + turnId).then(function (resp) {
          if (!resp.ok) { _selfTestTimer = setTimeout(_check, 2000); return; }
          return resp.json();
        }).then(function (data) {
          if (!data) { _selfTestTimer = setTimeout(_check, 2000); return; }
          if (data.terminal) {
            selfTestPolling.value = false;
            if (data.state === 'SUCCEEDED') {
              ElementPlus.ElMessage.success('自检通过');
            } else {
              ElementPlus.ElMessage.warning('自检未通过（' + (data.state || '') + '）');
            }
            loadProviders();
            return;
          }
          _selfTestTimer = setTimeout(_check, 2000);
        }).catch(function () {
          _selfTestTimer = setTimeout(_check, 3000);
        });
      }
      _selfTestTimer = setTimeout(_check, 1500);
    }
    function _stopSelfTestPoll() {
      selfTestPolling.value = false;
      if (_selfTestTimer) { clearTimeout(_selfTestTimer); _selfTestTimer = null; }
    }

    async function enableProvider(row, enabled) {
      try {
        var resp = await apiFetch(
          '/api/v1/copilot-admin/providers/' + row.id + '/enabled', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabled, expected_revision: row.revision }),
          });
        if (resp.ok) {
          ElementPlus.ElMessage.success(enabled ? '已启用' : '已停用');
          loadProviders();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('操作失败：' + e.message);
      }
    }

    // ═══ 路由配置 ═══
    function openRouteEdit(row) {
      routeForm.scene_code = row.scene_code;
      routeForm.primary_provider_id = row.primary_provider_id || '';
      routeForm.fallback_provider_id = row.fallback_provider_id || '';
      routeForm.privacy_profile = row.privacy_profile || 'INTERNAL_REDACTED';
      routeForm.expected_revision = row.revision || 1;
      routeFormError.value = '';
      routeDrawer.value = true;
    }

    function openRouteCreate(sceneCode) {
      routeForm.scene_code = sceneCode;
      routeForm.primary_provider_id = '';
      routeForm.fallback_provider_id = '';
      routeForm.privacy_profile = 'INTERNAL_REDACTED';
      routeForm.expected_revision = 0;
      routeFormError.value = '';
      routeDrawer.value = true;
    }

    async function saveRoute() {
      routeFormError.value = '';
      if (!routeForm.primary_provider_id) { routeFormError.value = '主模型不能为空'; return; }
      saving.value = true;
      try {
        var resp = await apiFetch(
          '/api/v1/copilot-admin/routes/' + routeForm.scene_code, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              primary_provider_id: routeForm.primary_provider_id || null,
              fallback_provider_id: routeForm.fallback_provider_id || null,
              privacy_profile: routeForm.privacy_profile,
              expected_revision: routeForm.expected_revision,
            }),
          });
        if (resp.ok) {
          ElementPlus.ElMessage.success('路由已保存');
          routeDrawer.value = false;
          await loadRoutes();
        } else {
          routeFormError.value = await _readErr(resp);
        }
      } catch (e) {
        routeFormError.value = '请求失败（' + e.message + '）';
      } finally { saving.value = false; }
    }

    // ═══ 授权管理 ═══
    function openGrantRequest() {
      grantForm.username = ''; grantForm.connection_id = '';
      grantForm.intent = 'REQUEST'; grantForm.approval_ref = '';
      grantForm.allow_schema_identifiers = false;
      grantForm.identifier_approval_ref = '';
      grantFormError.value = '';
      grantDrawer.value = true;
    }

    async function saveGrant() {
      grantFormError.value = '';
      if (!grantForm.username.trim()) { grantFormError.value = '用户不能为空'; return; }
      if (!grantForm.connection_id.trim()) { grantFormError.value = '实例连接 ID 不能为空'; return; }
      saving.value = true;
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: grantForm.username.trim(),
            connection_id: grantForm.connection_id.trim(),
            intent: grantForm.intent,
            approval_ref: grantForm.approval_ref || '',
            allow_schema_identifiers: grantForm.allow_schema_identifiers,
            identifier_approval_ref: grantForm.identifier_approval_ref || '',
          }),
        });
        if (resp.ok) {
          ElementPlus.ElMessage.success(grantForm.intent === 'REVOKE' ? '已撤销' : '申请已提交');
          grantDrawer.value = false;
          await loadGrants();
        } else {
          grantFormError.value = await _readErr(resp);
        }
      } catch (e) {
        grantFormError.value = '请求失败（' + e.message + '）';
      } finally { saving.value = false; }
    }

    async function approveGrant(row) {
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants/approve', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            subject_id: row.subject_id,
            connection_id: row.connection_id,
            expected_revision: row.revision,
          }),
        });
        if (resp.ok) {
          ElementPlus.ElMessage.success('已批准');
          loadGrants();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('操作失败：' + e.message);
      }
    }

    async function revokeGrant(row) {
      grantForm.username = row.username; grantForm.connection_id = row.connection_id;
      grantForm.intent = 'REVOKE';
      await saveGrant();
    }

    // ═══ 设置 ═══
    async function saveSettings() {
      if (!settings.value) return;
      saving.value = true;
      try {
        var s = settings.value.settings || {};
        var resp = await apiFetch('/api/v1/copilot-admin/settings', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            enabled: s.enabled, allow_schema_identifiers: s.allow_schema_identifiers,
            user_daily_tokens: s.user_daily_tokens, global_daily_tokens: s.global_daily_tokens,
            session_retention_days: s.session_retention_days,
            audit_retention_days: s.audit_retention_days,
            expected_revision: settings.value.config_revision,
          }),
        });
        if (resp.ok) {
          ElementPlus.ElMessage.success('设置已保存');
          await loadSettings();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('保存失败：' + e.message);
      } finally { saving.value = false; }
    }

    // ═══ 计算属性 ═══
    var providerMap = computed(function () {
      var m = {};
      (providers.value || []).forEach(function (p) { m[p.id] = p.name; });
      return m;
    });

    var configuredScenes = computed(function () {
      return (routes.value || []).map(function (r) { return r.scene_code; });
    });

    var unconfiguredScenes = computed(function () {
      var configured = configuredScenes.value;
      return SCENES.filter(function (s) { return configured.indexOf(s.value) < 0; });
    });

    // ═══ 初始化 ═══
    async function initPage() {
      await Promise.all([loadHealth(), loadEndpoints(), loadProviders(),
                         loadRoutes(), loadGrants(), loadSettings()]);
    }

    return {
      tab: tab, providers: providers, routes: routes, grants: grants,
      endpoints: endpoints, health: health, settings: settings,
      loading: loading, errorMsg: errorMsg, saving: saving,
      providerDrawer: providerDrawer, providerForm: providerForm,
      providerFormError: providerFormError,
      routeDrawer: routeDrawer, routeForm: routeForm, routeFormError: routeFormError,
      grantDrawer: grantDrawer, grantForm: grantForm, grantFormError: grantFormError,
      selfTestPolling: selfTestPolling,
      providerMap: providerMap, unconfiguredScenes: unconfiguredScenes,
      SCENES: SCENES, PRIVACY_PROFILES: PRIVACY_PROFILES, AUTH_MODES: AUTH_MODES,
      loadHealth: loadHealth, loadProviders: loadProviders, loadRoutes: loadRoutes,
      loadGrants: loadGrants, loadSettings: loadSettings, loadEndpoints: loadEndpoints,
      openProviderCreate: openProviderCreate, openProviderEdit: openProviderEdit,
      saveProvider: saveProvider,
      runSelfTest: runSelfTest, enableProvider: enableProvider,
      openRouteEdit: openRouteEdit, openRouteCreate: openRouteCreate, saveRoute: saveRoute,
      openGrantRequest: openGrantRequest, saveGrant: saveGrant,
      approveGrant: approveGrant, revokeGrant: revokeGrant,
      saveSettings: saveSettings,
      initPage: initPage,
    };
  }

  global.createCopilotAdminState = createCopilotAdminState;
})(window);
