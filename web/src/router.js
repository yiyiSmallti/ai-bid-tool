import { createRouter, createWebHistory } from "vue-router";
import { orgSession, session } from "./api.js";
import { errorText, orgAccess, orgRequest } from "./org.js";
import Audit from "./views/Audit.vue";
import Cards from "./views/Cards.vue";
import Login from "./views/Login.vue";
import Models from "./views/Models.vue";
import OrgBilling from "./views/OrgBilling.vue";
import OrgConfidential from "./views/OrgConfidential.vue";
import OrgProfiles from "./views/OrgProfiles.vue";
import OrgLogin from "./views/OrgLogin.vue";
import OrgTasks from "./views/OrgTasks.vue";
import OrgTask from "./views/OrgTask.vue";
import OrgReview from "./views/OrgReview.vue";
import OrgDrafts from "./views/OrgDrafts.vue";
import Orgs from "./views/Orgs.vue";
import SetupPassword from "./views/SetupPassword.vue";
import Usage from "./views/Usage.vue";
export const router = createRouter({
  history: createWebHistory("/app/"),
  routes: [
    { path: "/", redirect: "/org/tasks" },
    { path: "/setup-password", component: SetupPassword, meta: { public: true, title: "设置密码" } },
    { path: "/platform/login", component: Login, meta: { public: true, title: "平台后台登录" } },
    { path: "/platform", redirect: "/platform/orgs" },
    { path: "/platform/orgs", component: Orgs, meta: { area: "platform", title: "单位", nav: "orgs" } },
    { path: "/platform/models", component: Models, meta: { area: "platform", title: "模型", nav: "models" } },
    { path: "/platform/cards", component: Cards, meta: { area: "platform", title: "卡密", nav: "cards" } },
    { path: "/platform/usage", component: Usage, meta: { area: "platform", title: "用量与账单", nav: "usage" } },
    { path: "/platform/audit", component: Audit, meta: { area: "platform", title: "审计", nav: "audit" } },
    { path: "/org/login", component: OrgLogin, meta: { public: true, title: "单位登录" } },
    { path: "/org/tasks", component: OrgTasks, meta: { area: "org", title: "招标任务", nav: "tasks" } },
    { path: "/org/tasks/:taskId", component: OrgTask, meta: { area: "org", title: "任务详情", nav: "tasks" } },
    { path: "/org/tasks/:taskId/review", component: OrgReview, meta: { area: "org", title: "要求与响应审阅", nav: "tasks" } },
    { path: "/org/tasks/:taskId/drafts", component: OrgDrafts, meta: { area: "org", title: "响应表初稿", nav: "tasks" } },
    { path: "/org/profiles", component: OrgProfiles, meta: { area: "org", title: "单位资料", nav: "profiles" } },
    { path: "/org/confidential", component: OrgConfidential, meta: { area: "org", title: "保密字段", nav: "confidential" } },
    { path: "/org/billing", component: OrgBilling, meta: { area: "org", admin: true, title: "余额与充值", nav: "billing" } },
    { path: "/:rest(.*)", redirect: "/org/tasks" },
  ],
});
router.beforeEach(async (to) => {
  if (to.meta.area === "platform" && !session.get()) return "/platform/login";
  if (to.meta.area === "org") {
    if (!orgSession.get()) return "/org/login";
    try {
      const result = await orgRequest("GET", "/org/current");
      if (result.data.org_id !== orgSession.get()?.orgId || !["admin", "bidder", "technical", "viewer"].includes(result.data.role)) throw new Error("单位身份响应不符合契约");
      orgAccess.role = result.data.role; orgAccess.error = "";
      if (to.meta.admin && result.data.role !== "admin") return "/org/tasks";
    } catch (exc) {
      orgAccess.role = null; orgAccess.error = errorText(exc);
      if (!orgSession.get()) return "/org/login";
    }
  }
});
const consoles = { org: "单位后台", platform: "平台后台" };
router.afterEach((to) => {
  document.title = [to.meta.title, consoles[to.meta.area], "AI 标书工具"].filter(Boolean).join(" · ");
});
window.addEventListener("bid:signed-out", (event) => router.push(event.detail === "org" ? "/org/login" : "/platform/login"));
