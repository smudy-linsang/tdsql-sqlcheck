<template>
  <el-drawer
    v-model="visible"
    title="ToolBridge 运维控制台"
    size="50%"
    direction="rtl"
    :before-close="handleClose"
  >
    <div class="console-header">
      <div class="status-box" style="display: flex; align-items: center; gap: 12px;">
        <el-tag :type="statusTagType" effect="dark" size="large">{{ taskStatus }}</el-tag>
        <span class="task-id" style="font-family: var(--font-mono); color: #64748b;">Run ID: {{ runId }}</span>
      </div>
      <el-progress :percentage="progress" :status="progressStatus" style="margin-top: 12px;" />
    </div>

    <!-- 终端日志窗口 -->
    <div ref="terminalRef" class="terminal-window" style="background: #0f172a; color: #38bdf8; font-family: monospace; padding: 16px; border-radius: 6px; height: 420px; overflow-y: auto; margin-top: 16px;">
      <pre v-for="(line, idx) in logLines" :key="idx" class="log-line" style="margin: 0; line-height: 1.5; white-space: pre-wrap;">{{ line }}</pre>
    </div>

    <template #footer>
      <div class="drawer-footer" style="display: flex; justify-content: flex-end; gap: 12px;">
        <el-button v-if="taskStatus === 'RUNNING'" type="danger" :loading="killing" @click="killTask">
          终止任务
        </el-button>
        <el-button v-if="taskStatus === 'SUCCESS'" type="success" @click="downloadReport">
          下载诊断报告
        </el-button>
        <el-button @click="visible = false">关闭</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue';
import { ElMessage } from 'element-plus';

const visible = ref(false);
const runId = ref('');
const taskStatus = ref('RUNNING');
const progress = ref(30);
const logLines = ref([]);
const terminalRef = ref(null);
const killing = ref(false);

const statusTagType = computed(() => {
  if (taskStatus.value === 'RUNNING') return 'warning';
  if (taskStatus.value === 'SUCCESS') return 'success';
  return 'danger';
});

const progressStatus = computed(() => {
  if (taskStatus.value === 'SUCCESS') return 'success';
  if (taskStatus.value === 'FAILED') return 'exception';
  return '';
});

function appendLog(text) {
  logLines.value.push(text);
  nextTick(() => {
    if (terminalRef.value) {
      terminalRef.value.scrollTop = terminalRef.value.scrollHeight;
    }
  });
}

function killTask() {
  killing.value = true;
  setTimeout(() => {
    taskStatus.value = 'FAILED';
    appendLog('[SYSTEM] 任务已被用户强制终止。');
    killing.value = false;
    ElMessage.warning('任务已终止');
  }, 1000);
}

function downloadReport() {
  ElMessage.success('诊断报告已成功导出下载');
}

function handleClose(done) {
  done();
}

defineExpose({ visible, runId, taskStatus, progress, appendLog });
</script>
