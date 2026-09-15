/* v1.6.4.0 / CP-1：AI 配置管理端组合模块（DETAIL §12.5）。
   依赖注入：Vue / apiFetch / getIdentity。
   管理 /api/v1/copilot-admin/* 与 /api/v1/copilot-audit/events。 */
(function (global) {
  'use strict';

  function createCopilotAdminState(deps) {
    const { ref, reactive, computed } = deps.Vue;
    const apiFetch = deps.apiFetch;

    const tab = ref('providers');
    const providers = ref([]);
    const routes = ref([]);
    const grants = ref([]);
    const health = ref(null);
    const loading = ref(false);
    const errorMsg = ref('');

    async function loadHealth() {
      try {
        const resp = await apiFetch('/api/v1/copilot-admin/health');
        if (resp.ok) health.value = await resp.json();
      } catch (e) { /* 静默 */ }
    }

    async function loadProviders() {
      loading.value = true;
      errorMsg.value = '';
      try {
        const resp = await apiFetch('/api/v1/copilot-admin/providers');
        if (resp.ok) {
          const data = await resp.json();
          providers.value = data.items || [];
        } else {
          const d = await resp.json();
          errorMsg.value = d.message || d.detail || `加载失败（${resp.status}）`;
        }
      } catch (e) {
        errorMsg.value = `请求失败（${e.message}）`;
      } finally {
        loading.value = false;
      }
    }

    async function loadRoutes() {
      try {
        const resp = await apiFetch('/api/v1/copilot-admin/routes');
        if (resp.ok) {
          const data = await resp.json();
          routes.value = data.items || [];
        }
      } catch (e) { /* 静默 */ }
    }

    async function loadGrants() {
      try {
        const resp = await apiFetch('/api/v1/copilot-admin/grants?limit=50');
        if (resp.ok) {
          const data = await resp.json();
          grants.value = data.items || [];
        }
      } catch (e) { /* 静默 */ }
    }

    async function runSelfTest(row) {
      try {
        const clientId = Array.from(crypto.getRandomValues(new Uint8Array(16)))
          .map(b => b.toString(16).padStart(2, '0')).join('');
        const resp = await apiFetch(
          `/api/v1/copilot-admin/providers/${row.id}/self-tests`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              client_request_id: clientId,
              expected_provider_revision: row.revision,
            }),
          });
        if (resp.ok || resp.status === 202) {
          ElementPlus.ElMessage.success(`自检已受理（turn_id: ${(await resp.json()).turn_id}）`);
        } else {
          const d = await resp.json();
          ElementPlus.ElMessage.error(d.message || d.detail || `自检请求失败（${resp.status}）`);
        }
      } catch (e) {
        ElementPlus.ElMessage.error(`自检请求失败：${e.message}`);
      }
    }

    async function enableProvider(row, enabled) {
      try {
        const resp = await apiFetch(
          `/api/v1/copilot-admin/providers/${row.id}/enabled`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              enabled: enabled,
              expected_revision: row.revision,
            }),
          });
        if (resp.ok) {
          ElementPlus.ElMessage.success(enabled ? '已启用' : '已停用');
          loadProviders();
        } else {
          const d = await resp.json();
          ElementPlus.ElMessage.error(d.message || d.detail || `操作失败（${resp.status}）`);
        }
      } catch (e) {
        ElementPlus.ElMessage.error(`操作失败：${e.message}`);
      }
    }

    async function initPage() {
      await Promise.all([loadHealth(), loadProviders(), loadRoutes(), loadGrants()]);
    }

    return {
      tab, providers, routes, grants, health, loading, errorMsg,
      loadHealth, loadProviders, loadRoutes, loadGrants,
      runSelfTest, enableProvider, initPage,
    };
  }

  global.createCopilotAdminState = createCopilotAdminState;
})(window);
