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

    // ── 端点表单 ──
    var endpointDrawer = ref(false);
    var endpointSaving = ref(false);
    var endpointFormError = ref('');
    var endpointForm = reactive({
      endpoint_id: '',
      scheme: 'https',
      canonical_host: '',
      port: 443,
      base_path: '/v1',
      data_zone: 'INTERNAL',
      privacy_profile: 'INTERNAL_REDACTED',
      allows_schema_identifiers: true,
      allowed_resolved_cidrs_str: '',
      tls_ca_ref: 'internal',
      description: '',
      isEdit: false,
    });

    // ── 路由表单 ──
    var routeDrawer = ref(false);
    var routeForm = reactive({
      scene_code: '', primary_provider_id: '', fallback_provider_id: '',
      privacy_profile: 'INTERNAL_REDACTED', expected_revision: 1,
    });
    var routeFormError = ref('');

    // ── 授权表单 ──
    var grantDrawer = ref(false);
    var allUsers = ref([]);
    var allConnections = ref([]);
    var selectedGrants = ref([]);
    var grantForm = reactive({
      usernames: [], connection_ids: [], intent: 'REQUEST',
      approval_ref: '', allow_schema_identifiers: false,
      identifier_approval_ref: '',
    });
    var grantFormError = ref('');

    var grantFilterState = ref('ALL');

    var grantEstimateCount = computed(function () {
      return ((grantForm.usernames && grantForm.usernames.length) || 0) *
             ((grantForm.connection_ids && grantForm.connection_ids.length) || 0);
    });
    var pendingSelectedCount = computed(function () {
      return (selectedGrants.value || []).filter(function (r) {
        return r.approval_state === 'PENDING';
      }).length;
    });
    var revokedSelectedCount = computed(function () {
      return (selectedGrants.value || []).filter(function (r) {
        return r.approval_state === 'REVOKED' || !r.enabled;
      }).length;
    });
    var approvedSelectedCount = computed(function () {
      return (selectedGrants.value || []).filter(function (r) {
        return r.approval_state === 'APPROVED' && r.enabled;
      }).length;
    });
    var totalRevokedCount = computed(function () {
      return (grants.value || []).filter(function (r) {
        return r.approval_state === 'REVOKED' || !r.enabled;
      }).length;
    });
    var totalApprovedCount = computed(function () {
      return (grants.value || []).filter(function (r) {
        return r.approval_state === 'APPROVED' && r.enabled;
      }).length;
    });
    var filteredGrants = computed(function () {
      var state = grantFilterState.value;
      if (!state || state === 'ALL') return grants.value || [];
      if (state === 'APPROVED') {
        return (grants.value || []).filter(function (r) {
          return r.approval_state === 'APPROVED' && r.enabled;
        });
      }
      if (state === 'REVOKED') {
        return (grants.value || []).filter(function (r) {
          return r.approval_state === 'REVOKED' || !r.enabled;
        });
      }
      if (state === 'PENDING') {
        return (grants.value || []).filter(function (r) {
          return r.approval_state === 'PENDING';
        });
      }
      return grants.value || [];
    });
    var canBatchApprove = computed(function () {
      return pendingSelectedCount.value > 0;
    });

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
        if (d && Array.isArray(d.detail)) {
          var msgs = d.detail.map(function(item) {
            return (item.loc ? item.loc[item.loc.length - 1] + ': ' : '') + (item.msg || '');
          });
          return msgs.join('; ') || ('请求参数不合法（' + resp.status + '）');
        }
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

    // ═══ Endpoint CRUD ═══
    function openEndpointCreate(suggestedId) {
      endpointForm.endpoint_id = suggestedId || '';
      endpointForm.scheme = 'https';
      endpointForm.canonical_host = '';
      endpointForm.port = 443;
      endpointForm.base_path = '/v1';
      endpointForm.data_zone = 'INTERNAL';
      endpointForm.privacy_profile = 'INTERNAL_REDACTED';
      endpointForm.allows_schema_identifiers = true;
      endpointForm.allowed_resolved_cidrs_str = '';
      endpointForm.tls_ca_ref = 'internal';
      endpointForm.description = '';
      endpointForm.isEdit = false;
      endpointFormError.value = '';
      endpointDrawer.value = true;
    }

    function openEndpointEdit(row) {
      endpointForm.endpoint_id = row.endpoint_id;
      endpointForm.scheme = row.scheme || 'https';
      endpointForm.canonical_host = row.canonical_host || '';
      endpointForm.port = row.port || (row.scheme === 'http' ? 80 : 443);
      endpointForm.base_path = row.base_path || '/v1';
      endpointForm.data_zone = row.data_zone || 'INTERNAL';
      endpointForm.privacy_profile = row.privacy_profile || 'INTERNAL_REDACTED';
      endpointForm.allows_schema_identifiers = !!row.allows_schema_identifiers;
      endpointForm.allowed_resolved_cidrs_str = (row.allowed_resolved_cidrs || []).join(', ');
      endpointForm.tls_ca_ref = row.tls_ca_ref || 'internal';
      endpointForm.description = row.description || '';
      endpointForm.isEdit = true;
      endpointFormError.value = '';
      endpointDrawer.value = true;
    }

    function onEndpointSchemeChange() {
      if (!endpointForm.isEdit) {
        if (endpointForm.scheme === 'http' && endpointForm.port === 443) {
          endpointForm.port = 8000;
        } else if (endpointForm.scheme === 'https' && (endpointForm.port === 80 || endpointForm.port === 8000)) {
          endpointForm.port = 443;
        }
      }
    }

    async function saveEndpoint() {
      endpointFormError.value = '';
      var eid = (endpointForm.endpoint_id || '').trim();
      var host = (endpointForm.canonical_host || '').trim();
      var basePath = (endpointForm.base_path || '').trim();

      if (!eid) { endpointFormError.value = '端点标识不能为空'; return; }
      if (!/^[A-Za-z0-9_.-]{1,64}$/.test(eid)) {
        endpointFormError.value = '端点标识只能包含英文字母、数字、短横线、下划线与点';
        return;
      }
      if (!host) { endpointFormError.value = '主机 IP 或域名不能为空'; return; }
      if (host.includes('@') || host.includes('?') || host.includes('#') || host.includes('*')) {
        endpointFormError.value = '主机不能包含 @、?、#、* 等特殊字符';
        return;
      }
      if (!endpointForm.port || endpointForm.port < 1 || endpointForm.port > 65535) {
        endpointFormError.value = '端口范围需在 1 到 65535 之间';
        return;
      }
      if (!basePath) {
        basePath = '/';
      } else if (!basePath.startsWith('/')) {
        basePath = '/' + basePath;
      }

      var cidrs = [];
      if (endpointForm.allowed_resolved_cidrs_str && endpointForm.allowed_resolved_cidrs_str.trim()) {
        cidrs = endpointForm.allowed_resolved_cidrs_str.split(',')
          .map(function(s) { return s.trim(); })
          .filter(function(s) { return !!s; });
      }

      endpointSaving.value = true;
      try {
        var body = {
          scheme: endpointForm.scheme,
          canonical_host: host,
          port: endpointForm.port,
          base_path: basePath,
          data_zone: endpointForm.data_zone,
          privacy_profile: endpointForm.privacy_profile,
          allows_schema_identifiers: endpointForm.allows_schema_identifiers,
          allowed_resolved_cidrs: cidrs,
          tls_ca_ref: endpointForm.tls_ca_ref || 'internal',
          description: (endpointForm.description || '').trim(),
        };

        var resp;
        if (endpointForm.isEdit) {
          resp = await apiFetch('/api/v1/copilot-admin/endpoints/' + encodeURIComponent(eid), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        } else {
          body.endpoint_id = eid;
          resp = await apiFetch('/api/v1/copilot-admin/endpoints', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        }

        if (resp.ok || resp.status === 201) {
          ElementPlus.ElMessage.success(endpointForm.isEdit ? '端点已更新' : '端点已创建');
          endpointDrawer.value = false;
          await loadEndpoints();
          // 如果当前处于新增/编辑模型抽屉中，自动将新建的端点选中
          if (providerDrawer.value) {
            providerForm.endpoint_id = eid;
          }
        } else {
          endpointFormError.value = await _readErr(resp);
        }
      } catch (e) {
        endpointFormError.value = '请求失败（' + e.message + '）';
      } finally {
        endpointSaving.value = false;
      }
    }

    async function deleteEndpoint(row) {
      if (row.used_by_providers && row.used_by_providers.length > 0) {
        ElementPlus.ElMessageBox.alert(
          '端点【' + row.endpoint_id + '】当前正被模型【' + row.used_by_providers.join(', ') + '】使用，无法直接删除。请先在模型标签页解除关联或更换模型端点后再操作。',
          '禁止删除',
          { confirmButtonText: '知道了', type: 'warning' }
        );
        return;
      }

      try {
        await ElementPlus.ElMessageBox.confirm(
          '确定要彻底删除批准端点【' + row.endpoint_id + '】(' + row.canonical_host + ') 吗？',
          '删除确认',
          { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' }
        );
      } catch (e) {
        return; // 用户取消
      }

      try {
        var resp = await apiFetch('/api/v1/copilot-admin/endpoints/' + encodeURIComponent(row.endpoint_id), {
          method: 'DELETE',
        });
        if (resp.ok) {
          ElementPlus.ElMessage.success('端点已删除');
          await loadEndpoints();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('删除失败（' + e.message + '）');
      }
    }

    // ═══ Provider CRUD ═══
    function openProviderCreate() {
      providerForm.id = ''; providerForm.name = '';
      providerForm.endpoint_id = endpoints.value.length ? endpoints.value[0].endpoint_id : '';
      providerForm.model_id = ''; providerForm.auth_mode = 'BEARER_KEY';
      providerForm.context_tokens = 65536;
      providerForm.secret_action = 'REPLACE'; providerForm.secret = '';
      providerForm.isEdit = false; providerForm.expected_revision = 1;
      providerFormError.value = '';
      providerDrawer.value = true;
    }

    function openProviderEdit(row) {
      providerForm.id = row.id; providerForm.name = row.name;
      providerForm.endpoint_id = row.endpoint_id;
      providerForm.model_id = row.model_id;
      providerForm.auth_mode = (row.auth_mode === 'BEARER' || row.auth_mode === 'BEARER_KEY') ? 'BEARER_KEY' : row.auth_mode;
      providerForm.context_tokens = (row.capabilities && row.capabilities.context_tokens) || 65536;
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
          capabilities: {
            context_tokens: providerForm.context_tokens || 65536,
            max_output_field: 'max_tokens',
          },
          secret_action: providerForm.secret_action,
        };
        if (providerForm.secret_action === 'REPLACE' && providerForm.secret) {
          body.secret = providerForm.secret;
        }
        var resp;
        if (providerForm.isEdit) {
          delete body.protocol;
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
    async function loadGrantCandidates() {
      try {
        var pUsers = apiFetch('/api/v1/auth/users?limit=500');
        var pConns = apiFetch('/api/v1/tdsql/connections');
        var resps = await Promise.allSettled([pUsers, pConns]);
        if (resps[0].status === 'fulfilled' && resps[0].value.ok) {
          var uData = await resps[0].value.json();
          allUsers.value = (uData.users || []).filter(function (u) {
            return u.status === 'active';
          });
        }
        if (resps[1].status === 'fulfilled' && resps[1].value.ok) {
          var cData = await resps[1].value.json();
          allConnections.value = cData.connections || [];
        }
      } catch (e) { /* 静默 */ }
    }

    function selectAllDevUsers() {
      var devs = allUsers.value.filter(function (u) { return u.role === 'developer'; });
      grantForm.usernames = devs.map(function (u) { return u.username; });
    }

    function selectAllUsers() {
      grantForm.usernames = allUsers.value.map(function (u) { return u.username; });
    }

    function clearUsers() {
      grantForm.usernames = [];
    }

    function selectAllConnections() {
      grantForm.connection_ids = allConnections.value.map(function (c) { return c.id; });
    }

    function clearConnections() {
      grantForm.connection_ids = [];
    }

    function openGrantRequest() {
      grantForm.usernames = []; grantForm.connection_ids = [];
      grantForm.intent = 'GRANT'; grantForm.approval_ref = '';
      grantForm.allow_schema_identifiers = false;
      grantForm.identifier_approval_ref = '';
      grantFormError.value = '';
      grantDrawer.value = true;
      loadGrantCandidates();
    }

    async function saveGrant() {
      grantFormError.value = '';
      if (!grantForm.usernames || !grantForm.usernames.length) {
        grantFormError.value = '请至少选择一个目标用户';
        return;
      }
      if (!grantForm.connection_ids || !grantForm.connection_ids.length) {
        grantFormError.value = '请至少选择一个实例连接';
        return;
      }
      saving.value = true;
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            usernames: grantForm.usernames,
            connection_ids: grantForm.connection_ids,
            intent: grantForm.intent,
            approval_ref: grantForm.approval_ref || '',
            allow_schema_identifiers: grantForm.allow_schema_identifiers,
            identifier_approval_ref: grantForm.identifier_approval_ref || '',
          }),
        });
        if (resp.ok) {
          var resData = await resp.json().catch(function () { return {}; });
          var count = resData.count || (grantForm.usernames.length * grantForm.connection_ids.length);
          var msg = '已成功分配 ' + count + ' 条实例授权（已即时生效）';
          if (grantForm.intent === 'REVOKE') msg = '已成功撤销 ' + count + ' 条实例授权';
          else if (grantForm.intent === 'DELETE') msg = '已成功彻底删除 ' + count + ' 条实例授权记录';
          else if (grantForm.intent === 'RESTORE') msg = '已成功恢复 ' + count + ' 条实例授权';
          ElementPlus.ElMessage.success(msg);
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
      grantForm.usernames = [row.username];
      grantForm.connection_ids = [row.connection_id];
      grantForm.intent = 'REVOKE';
      await saveGrant();
    }

    function handleGrantSelectionChange(selection) {
      selectedGrants.value = selection || [];
    }

    async function batchApproveGrants() {
      var pendingList = (selectedGrants.value || []).filter(function (r) {
        return r.approval_state === 'PENDING';
      });
      if (!pendingList.length) {
        ElementPlus.ElMessage.warning('请勾选处于 PENDING 状态的待批准授权');
        return;
      }
      try {
        await ElementPlus.ElMessageBox.confirm(
          '确认批量批准选中的 ' + pendingList.length + ' 条实例授权？',
          '批量批准确认',
          { confirmButtonText: '确认批准', cancelButtonText: '取消', type: 'info' }
        );
      } catch (e) { return; }

      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants/batch-approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            grants: pendingList.map(function (r) {
              return {
                subject_id: r.subject_id,
                connection_id: r.connection_id,
                expected_revision: r.revision,
              };
            }),
          }),
        });
        if (resp.ok) {
          var res = await resp.json();
          ElementPlus.ElMessage.success('批量批准完成，成功：' + res.approved_count + ' 条');
          await loadGrants();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('批量批准失败：' + e.message);
      }
    }

    async function batchRevokeGrants() {
      if (!selectedGrants.value || !selectedGrants.value.length) {
        ElementPlus.ElMessage.warning('请先勾选需要撤销的授权项');
        return;
      }
      var targetUsers = Array.from(new Set(selectedGrants.value.map(function (r) { return r.username; })));
      var targetConns = Array.from(new Set(selectedGrants.value.map(function (r) { return r.connection_id; })));
      try {
        await ElementPlus.ElMessageBox.confirm(
          '确认批量撤销选中的 ' + selectedGrants.value.length + ' 条实例授权？',
          '批量撤销确认',
          { confirmButtonText: '确认撤销', cancelButtonText: '取消', type: 'warning' }
        );
      } catch (e) { return; }

      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            usernames: targetUsers,
            connection_ids: targetConns,
            intent: 'REVOKE',
          }),
        });
        if (resp.ok) {
          ElementPlus.ElMessage.success('批量撤销已生效');
          await loadGrants();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('批量撤销失败：' + e.message);
      }
    }

    async function restoreGrant(row) {
      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants/restore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            grants: [{ subject_id: row.subject_id, connection_id: row.connection_id, username: row.username }]
          }),
        });
        if (resp.ok) {
          var connDisplay = row.connection_name || row.instance_name || row.connection_id;
          ElementPlus.ElMessage.success('已恢复用户 ' + row.username + ' 对实例 ' + connDisplay + ' 的授权');
          await loadGrants();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('恢复授权失败：' + e.message);
      }
    }

    async function deleteGrant(row) {
      var connDisplay = row.connection_name || row.instance_name || row.connection_id;
      try {
        await ElementPlus.ElMessageBox.confirm(
          '确认彻底删除用户 ' + row.username + ' 对实例 ' + connDisplay + ' 的授权记录？删除后将永久移除该条记录。',
          '彻底删除授权',
          { confirmButtonText: '彻底删除', cancelButtonText: '取消', type: 'warning' }
        );
      } catch (e) { return; }

      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants?subject_id=' + encodeURIComponent(row.subject_id || '') + '&connection_id=' + encodeURIComponent(row.connection_id) + '&username=' + encodeURIComponent(row.username || ''), {
          method: 'DELETE',
        });
        if (resp.ok) {
          var res = await resp.json();
          if (res.deleted || (res.deleted_count && res.deleted_count > 0)) {
            ElementPlus.ElMessage.success('已彻底删除该授权记录');
          } else {
            ElementPlus.ElMessage.info('记录已不存在或已被清理');
          }
          await loadGrants();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('删除授权失败：' + e.message);
      }
    }

    async function batchRestoreGrants() {
      var revokedList = (selectedGrants.value || []).filter(function (r) {
        return r.approval_state === 'REVOKED' || !r.enabled;
      });
      if (!revokedList.length) {
        ElementPlus.ElMessage.warning('请勾选处于已撤销状态的授权项');
        return;
      }
      try {
        await ElementPlus.ElMessageBox.confirm(
          '确认批量恢复选中的 ' + revokedList.length + ' 条实例授权并重新生效？',
          '批量恢复确认',
          { confirmButtonText: '确认恢复', cancelButtonText: '取消', type: 'success' }
        );
      } catch (e) { return; }

      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants/restore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            grants: revokedList.map(function (r) {
              return { subject_id: r.subject_id, connection_id: r.connection_id, username: r.username };
            })
          }),
        });
        if (resp.ok) {
          var res = await resp.json();
          var count = res.restored_count != null ? res.restored_count : (res.count || 0);
          if (count > 0) {
            ElementPlus.ElMessage.success('批量恢复成功，已重新生效 ' + count + ' 条授权');
          } else {
            ElementPlus.ElMessage.info('未找到可恢复的授权记录');
          }
          await loadGrants();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('批量恢复失败：' + e.message);
      }
    }

    async function batchDeleteGrants() {
      if (!selectedGrants.value || !selectedGrants.value.length) {
        ElementPlus.ElMessage.warning('请先勾选需要彻底删除的授权项');
        return;
      }
      try {
        await ElementPlus.ElMessageBox.confirm(
          '确认彻底删除选中的 ' + selectedGrants.value.length + ' 条实例授权记录？删除后将从列表中彻底移除。',
          '批量删除确认',
          { confirmButtonText: '彻底删除', cancelButtonText: '取消', type: 'danger' }
        );
      } catch (e) { return; }

      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants/batch-delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            grants: selectedGrants.value.map(function (r) {
              return { subject_id: r.subject_id, connection_id: r.connection_id, username: r.username };
            })
          }),
        });
        if (resp.ok) {
          var res = await resp.json();
          var count = res.deleted_count != null ? res.deleted_count : (res.count || 0);
          if (count > 0) {
            ElementPlus.ElMessage.success('批量删除成功，已移除 ' + count + ' 条记录');
          } else {
            ElementPlus.ElMessage.info('未找到需删除的记录或已删除');
          }
          await loadGrants();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('批量删除失败：' + e.message);
      }
    }

    async function clearAllRevokedGrants() {
      try {
        await ElementPlus.ElMessageBox.confirm(
          '确认一键清空所有历史已撤销/已停用的授权记录？（共 ' + totalRevokedCount.value + ' 条）清空后列表仅保留有效授权。',
          '清空已撤销记录',
          { confirmButtonText: '清空已撤销', cancelButtonText: '取消', type: 'warning' }
        );
      } catch (e) { return; }

      try {
        var resp = await apiFetch('/api/v1/copilot-admin/grants?clear_revoked=true', {
          method: 'DELETE',
        });
        if (resp.ok) {
          var res = await resp.json();
          ElementPlus.ElMessage.success('已清空 ' + (res.deleted_count || 0) + ' 条已撤销记录');
          await loadGrants();
        } else {
          ElementPlus.ElMessage.error(await _readErr(resp));
        }
      } catch (e) {
        ElementPlus.ElMessage.error('清空失败：' + e.message);
      }
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
      allUsers: allUsers, allConnections: allConnections, selectedGrants: selectedGrants,
      grantEstimateCount: grantEstimateCount, pendingSelectedCount: pendingSelectedCount,
      canBatchApprove: canBatchApprove,
      grantFilterState: grantFilterState, filteredGrants: filteredGrants,
      revokedSelectedCount: revokedSelectedCount, approvedSelectedCount: approvedSelectedCount,
      totalRevokedCount: totalRevokedCount, totalApprovedCount: totalApprovedCount,
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
      selectAllDevUsers: selectAllDevUsers, selectAllUsers: selectAllUsers, clearUsers: clearUsers,
      selectAllConnections: selectAllConnections, clearConnections: clearConnections,
      handleGrantSelectionChange: handleGrantSelectionChange,
      batchApproveGrants: batchApproveGrants, batchRevokeGrants: batchRevokeGrants,
      approveGrant: approveGrant, revokeGrant: revokeGrant,
      restoreGrant: restoreGrant, deleteGrant: deleteGrant,
      batchRestoreGrants: batchRestoreGrants, batchDeleteGrants: batchDeleteGrants,
      endpointDrawer: endpointDrawer, endpointForm: endpointForm,
      endpointFormError: endpointFormError, endpointSaving: endpointSaving,
      openEndpointCreate: openEndpointCreate, openEndpointEdit: openEndpointEdit,
      onEndpointSchemeChange: onEndpointSchemeChange,
      saveEndpoint: saveEndpoint, deleteEndpoint: deleteEndpoint,
      saveSettings: saveSettings,
      initPage: initPage,
    };
  }

  global.createCopilotAdminState = createCopilotAdminState;
})(window);
