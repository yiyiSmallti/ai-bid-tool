<script setup>
import { Coin, DataAnalysis, Document, Files, Lock, Memo, Monitor, OfficeBuilding, Tickets, User, Wallet } from "@element-plus/icons-vue";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { orgSession, session } from "./api.js";
import { label, orgAccess, roles } from "./org.js";
const route = useRoute(), router = useRouter();
const area = computed(() => route.meta.area);
const org = computed(() => (area.value === "org" ? orgSession.get() : null));
const menus = computed(() => area.value === "platform"
  ? [
      { key: "orgs", to: "/platform/orgs", title: "单位", icon: OfficeBuilding },
      { key: "operators", to: "/platform/operators", title: "平台管理员", icon: User },
      { key: "models", to: "/platform/models", title: "模型", icon: Monitor },
      { key: "credentials", to: "/platform/credentials", title: "服务凭据", icon: Lock },
      { key: "cards", to: "/platform/cards", title: "卡密", icon: Tickets },
      { key: "usage", to: "/platform/usage", title: "用量与账单", icon: DataAnalysis },
      { key: "audit", to: "/platform/audit", title: "审计", icon: Files },
    ]
  : [
      { key: "tasks", to: "/org/tasks", title: "招标任务", icon: Document },
      { key: "products", to: "/org/products", title: "产品库", icon: Files },
      { key: "features", to: "/org/features", title: "功能库", icon: Files },
      { key: "templates", to: "/org/templates", title: "模板库", icon: Document },
      { key: "attachments", to: "/org/attachments", title: "附件档案", icon: Memo },
      { key: "profiles", to: "/org/profiles", title: "单位资料", icon: Memo },
      { key: "memory", to: "/org/memory", title: "单位记忆", icon: Memo },
      { key: "org-models", to: "/org/settings/models", title: "模型与服务配置", icon: Monitor },
      { key: "confidential", to: "/org/confidential", title: "保密字段", icon: Lock },
      ...(orgAccess.role === "admin" ? [{ key: "billing", to: "/org/billing", title: "余额与充值", icon: Wallet }] : []),
    ]);
async function signOut() {
  if (area.value === "org") {
    // Let the editor's leave guard run before discarding the session.
    const failure = await router.push("/org/login");
    if (!failure) orgSession.clear();
  } else { session.clear(); router.push("/platform/login"); }
}
</script>
<template>
  <el-config-provider :locale="zhCn">
    <a class="skip-link" href="#main-content">跳到主内容</a>
    <div v-if="area" class="app-shell">
      <aside class="app-aside">
        <div class="brand"><el-icon :size="22"><Coin /></el-icon><span class="brand-text">AI 标书工具<small>{{ area === "platform" ? "平台后台" : "单位后台" }}</small></span></div>
        <nav :aria-label="area === 'platform' ? '平台导航' : '单位导航'" class="app-nav">
          <RouterLink v-for="item in menus" :key="item.key" :to="item.to" class="nav-item" :class="{ active: route.meta.nav === item.key }" :title="item.title">
            <el-icon :size="18"><component :is="item.icon" /></el-icon><span>{{ item.title }}</span>
          </RouterLink>
        </nav>
      </aside>
      <div class="app-body">
        <header class="app-header">
          <div class="org-name">
            <template v-if="area === 'org'"><strong>{{ org?.orgName }}</strong><el-tag v-if="orgAccess.role" size="small" effect="plain" round>{{ label(roles, orgAccess.role) }}</el-tag></template>
            <strong v-else>平台运营</strong>
          </div>
          <div class="who">
            <span v-if="org" class="email">{{ org.email }}</span>
            <el-button size="small" @click="signOut">{{ area === "org" ? "退出 / 切换单位" : "退出登录" }}</el-button>
          </div>
        </header>
        <main id="main-content" class="app-main" tabindex="-1">
          <template v-if="area === 'org'">
            <el-alert v-if="orgAccess.error" :title="orgAccess.error" type="error" show-icon :closable="false" role="alert" />
            <RouterView v-else-if="orgAccess.role" :key="`${route.path}:${route.query.job ?? ''}:${['products', 'features', 'templates', 'profiles', 'memory', 'attachments'].includes(route.meta.nav) ? `${route.query.revision ?? ''}:${route.query.task ?? ''}` : ''}:${org?.orgId}`" />
          </template>
          <RouterView v-else />
        </main>
      </div>
    </div>
    <main v-else id="main-content" class="public-main" tabindex="-1"><RouterView /></main>
  </el-config-provider>
</template>
