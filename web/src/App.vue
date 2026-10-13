<script setup>
import { Box, Collection, Coin, Cpu, Document, FolderOpened, Key, Lock, Memo, Monitor, OfficeBuilding, Stamp, Tickets } from "@element-plus/icons-vue";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { session } from "./api.js";
const route = useRoute(), router = useRouter();
const shell = computed(() => !route.meta.public);
// Grouped by what the person is doing: the working task, its source libraries, then settings.
const menuGroups = [
  {
    key: "work",
    title: "核心工作",
    items: [{ key: "tasks", to: "/org/tasks", title: "招标任务", icon: Tickets }],
  },
  {
    key: "library",
    title: "资源库",
    items: [
      { key: "products", to: "/org/products", title: "产品库", icon: Box },
      { key: "features", to: "/org/features", title: "功能库", icon: Collection },
      { key: "templates", to: "/org/templates", title: "模板库", icon: Document },
      { key: "attachments", to: "/org/attachments", title: "附件档案", icon: FolderOpened },
      { key: "profiles", to: "/org/profiles", title: "单位资料", icon: OfficeBuilding },
      { key: "memory", to: "/org/memory", title: "单位记忆", icon: Memo },
    ],
  },
  {
    key: "settings",
    title: "系统设置",
    items: [
      { key: "org-models", to: "/org/settings/models", title: "模型与服务配置", icon: Cpu },
      { key: "credentials", to: "/platform/credentials", title: "服务凭据", icon: Key },
      { key: "clef", to: "/platform/clef", title: "Clef 初筛配置", icon: Monitor },
      { key: "trust-anchors", to: "/platform/trust-anchors", title: "信任根证书", icon: Stamp },
      { key: "confidential", to: "/org/confidential", title: "保密字段", icon: Lock },
    ],
  },
];
// The header names the section the current route belongs to; task routes add the task step.
const currentGroup = computed(() => menuGroups.find((group) => group.items.some((item) => item.key === route.meta.nav)));
const taskContext = computed(() => Boolean(route.params.taskId));
async function signOut() {
  // Let the editor's leave guard run before discarding the session.
  const failure = await router.push("/login");
  if (!failure) session.clear();
}
</script>
<template>
  <el-config-provider :locale="zhCn">
    <a class="skip-link" href="#main-content">跳到主内容</a>
    <div v-if="shell" class="app-shell">
      <aside class="app-aside">
        <div class="brand"><el-icon :size="22"><Coin /></el-icon><span class="brand-text">AI 标书工具</span></div>
        <nav aria-label="导航" class="app-nav">
          <div v-for="group in menuGroups" :key="group.key" class="nav-group" role="group" :aria-label="group.title">
            <p class="nav-group-title">{{ group.title }}</p>
            <RouterLink v-for="item in group.items" :key="item.key" :to="item.to" class="nav-item" :class="{ active: route.meta.nav === item.key }" :title="item.title">
              <el-icon :size="18"><component :is="item.icon" /></el-icon><span>{{ item.title }}</span>
            </RouterLink>
          </div>
        </nav>
      </aside>
      <div class="app-body">
        <header class="app-header">
          <div class="page-title">
            <span v-if="currentGroup" class="header-group">{{ currentGroup.title }}</span>
            <span v-if="currentGroup" class="header-sep" aria-hidden="true">/</span>
            <strong>{{ route.meta.title }}</strong>
            <span v-if="taskContext" class="header-context">任务工作区</span>
          </div>
          <div class="who">
            <span class="status-dot" aria-hidden="true"></span><span class="status-text">本机单用户</span>
            <el-button size="small" @click="signOut">退出</el-button>
          </div>
        </header>
        <main id="main-content" class="app-main" tabindex="-1">
          <RouterView :key="`${route.path}:${route.query.job ?? ''}:${['products', 'features', 'templates', 'profiles', 'memory', 'attachments'].includes(route.meta.nav) ? `${route.query.revision ?? ''}:${route.query.task ?? ''}` : ''}`" />
        </main>
      </div>
    </div>
    <main v-else id="main-content" class="public-main" tabindex="-1"><RouterView /></main>
  </el-config-provider>
</template>
