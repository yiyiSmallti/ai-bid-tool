import { createRouter, createWebHistory } from "vue-router";
import { orgSession, session } from "./api.js";
import { errorText, orgAccess, orgRequest } from "./org.js";
import Audit from "./views/Audit.vue";
import Cards from "./views/Cards.vue";
import Login from "./views/Login.vue";
import Models from "./views/Models.vue";
import OrgBilling from "./views/OrgBilling.vue";
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
    { path: "/setup-password", component: SetupPassword, meta: { public: true } },
    { path: "/platform/login", component: Login, meta: { public: true } },
    { path: "/platform", redirect: "/platform/orgs" },
    { path: "/platform/orgs", component: Orgs, meta: { area: "platform" } },
    { path: "/platform/models", component: Models, meta: { area: "platform" } },
    { path: "/platform/cards", component: Cards, meta: { area: "platform" } },
    { path: "/platform/usage", component: Usage, meta: { area: "platform" } },
    { path: "/platform/audit", component: Audit, meta: { area: "platform" } },
    { path: "/org/login", component: OrgLogin, meta: { public: true } },
    { path: "/org/tasks", component: OrgTasks, meta: { area: "org" } },
    { path: "/org/tasks/:taskId", component: OrgTask, meta: { area: "org" } },
    { path: "/org/tasks/:taskId/review", component: OrgReview, meta: { area: "org" } },
    { path: "/org/tasks/:taskId/drafts", component: OrgDrafts, meta: { area: "org" } },
    { path: "/org/billing", component: OrgBilling, meta: { area: "org", admin: true } },
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
window.addEventListener("bid:signed-out", (event) => router.push(event.detail === "org" ? "/org/login" : "/platform/login"));
