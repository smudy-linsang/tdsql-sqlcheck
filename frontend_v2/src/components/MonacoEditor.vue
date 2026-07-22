<template>
  <div ref="editorContainer" class="monaco-editor-box" style="height: 350px; width: 100%; border: 1px solid #dcdfe6; border-radius: 4px;"></div>
</template>

<script setup>
import { ref, onMounted, watch, onBeforeUnmount } from 'vue';

const props = defineProps({
  modelValue: { type: String, default: '' },
  violations: { type: Array, default: () => [] }
});

const emit = defineEmits(['update:modelValue']);
const editorContainer = ref(null);
let editorInstance = null;

onMounted(() => {
  if (window.monaco) {
    initMonaco();
  }
});

function initMonaco() {
  if (!editorContainer.value) return;
  editorInstance = window.monaco.editor.create(editorContainer.value, {
    value: props.modelValue,
    language: 'sql',
    theme: 'vs-dark',
    automaticLayout: true,
    minimap: { enabled: false }
  });

  editorInstance.onDidChangeModelContent(() => {
    emit('update:modelValue', editorInstance.getValue());
  });
}

onBeforeUnmount(() => {
  if (editorInstance) {
    editorInstance.dispose();
  }
});
</script>

<style scoped>
.monaco-editor-box {
  position: relative;
}
</style>
