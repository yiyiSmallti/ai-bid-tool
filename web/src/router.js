import { createRouter, createWebHistory } from "vue-router";
import { orgSession, session } from "./api.js";
import { errorText, orgAccess, orgRequest } from "./org.js";
import Audit from "./views/Audit.vue";
import Cards from "./views/Cards.vue";
import Credentials from "./views/Credentials.vue";
import Login from "./views/Login.vue";
import Models from "./views/Models.vue";
import OrgBilling from "./views/OrgBilling.vue";
import OrgConfidential from "./views/OrgConfidential.vue";
import OrgProfiles from "./views/OrgProfiles.vue";
import OrgLogin from "./views/OrgLogin.vue";
import OrgTasks from "./views/OrgTasks.vue";
import OrgTaskBoard from "./views/OrgTaskBoard.vue";
import OrgTaskProgress from "./views/OrgTaskProgress.vue";
import OrgTaskMembers from "./views/OrgTaskMembers.vue";
import OrgTask from "./views/OrgTask.vue";
import OrgRequirements from "./views/OrgRequirements.vue";
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
    { path: "/platform/credentials", component: Credentials, meta: { area: "platform", title: "服务凭据", nav: "credentials" } },
    { path: "/platform/cards", component: Cards, meta: { area: "platform", title: "卡密", nav: "cards" } },
    { path: "/platform/usage", component: Usage, meta: { area: "platform", title: "用量与账单", nav: "usage" } },
    { path: "/platform/audit", component: Audit, meta: { area: "platform", title: "审计", nav: "audit" } },
    { path: "/org/login", component: OrgLogin, meta: { public: true, title: "单位登录" } },
    { path: "/org/tasks", component: OrgTasks, meta: { area: "org", title: "招标任务", nav: "tasks" } },
    { path: "/org/tasks/:taskId", component: OrgTask, meta: { area: "org", title: "任务详情", nav: "tasks" } },
    { path: "/org/tasks/:taskId/board", component: OrgTaskBoard, meta: { area: "org", title: "任务看板", nav: "tasks" } },
    { path: "/org/tasks/:taskId/progress", component: OrgTaskProgress, meta: { area: "org", title: "作业进度", nav: "tasks" } },
    { path: "/org/tasks/:taskId/members", component: OrgTaskMembers, meta: { area: "org", title: "成员与归档", nav: "tasks" } },
    { path: "/org/tasks/:taskId/requirements", component: OrgRequirements, meta: { area: "org", title: "要求确认与补录", nav: "tasks" } },
    { path: "/org/tasks/:taskId/cards/:cardId/annotation", component: () => import("./views/OrgAnnotation.vue"), meta: { area: "org", title: "证据标注", nav: "tasks" } },
    { path: "/org/tasks/:taskId/review", component: OrgReview, meta: { area: "org", title: "要求与响应审阅", nav: "tasks" } },
    { path: "/org/tasks/:taskId/drafts", component: OrgDrafts, meta: { area: "org", title: "响应表初稿", nav: "tasks" } },
    { path: "/org/tasks/:taskId/checks", component: () => import("./views/OrgChecks.vue"), meta: { area: "org", title: "检查风险", nav: "tasks" } },
    { path: "/org/tasks/:taskId/checks/:reportId", component: () => import("./views/OrgCheckReport.vue"), meta: { area: "org", title: "检查报告", nav: "tasks" } },
    { path: "/org/tasks/:taskId/score-rubrics", component: () => import("./views/OrgRubrics.vue"), meta: { area: "org", title: "评分规则", nav: "tasks" } },
    { path: "/org/tasks/:taskId/score-rubrics/:rubricId", component: () => import("./views/OrgRubricReview.vue"), meta: { area: "org", title: "评分规则审阅", nav: "tasks" } },
    { path: "/org/tasks/:taskId/scores", component: () => import("./views/OrgScores.vue"), meta: { area: "org", title: "评分预估", nav: "tasks" } },
    { path: "/org/tasks/:taskId/scores/:reportId", component: () => import("./views/OrgScoreReport.vue"), meta: { area: "org", title: "评分报告", nav: "tasks" } },
    { path: "/org/products", component: () => import("./views/OrgProducts.vue"), meta: { area: "org", title: "产品库", nav: "products" } },
    { path: "/org/products/:productId", component: () => import("./views/OrgProduct.vue"), meta: { area: "org", title: "产品详情", nav: "products" } },
    { path: "/org/features", component: () => import("./views/OrgFeatures.vue"), meta: { area: "org", title: "功能库", nav: "features" } },
    { path: "/org/features/:featureId", component: () => import("./views/OrgFeature.vue"), meta: { area: "org", title: "功能详情", nav: "features" } },
    { path: "/org/templates", component: () => import("./views/OrgTemplates.vue"), meta: { area: "org", title: "模板库", nav: "templates" } },
    { path: "/org/templates/:templateId", component: () => import("./views/OrgTemplate.vue"), meta: { area: "org", title: "模板详情", nav: "templates" } },
    { path: "/org/templates/:templateId/revisions/:revisionId/bindings", component: () => import("./views/OrgTemplateBindings.vue"), meta: { area: "org", title: "模板绑定", nav: "templates" } },
    { path: "/org/profiles", component: OrgProfiles, meta: { area: "org", title: "单位资料", nav: "profiles" } },
    { path: "/org/profiles/:resourceId", component: () => import("./views/OrgQualification.vue"), meta: { area: "org", title: "资料详情", nav: "profiles", resourceKind: "profiles" } },
    { path: "/org/certificates/:resourceId", component: () => import("./views/OrgQualification.vue"), meta: { area: "org", title: "证照详情", nav: "profiles", resourceKind: "certificates" } },
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
      orgAccess.userId = result.data.user_id ?? null; orgAccess.role = result.data.role; orgAccess.error = "";
      if (to.meta.admin && result.data.role !== "admin") return "/org/tasks";
    } catch (exc) {
      orgAccess.role = null; orgAccess.userId = null; orgAccess.error = errorText(exc);
      if (!orgSession.get()) return "/org/login";
    }
  }
});
const consoles = { org: "单位后台", platform: "平台后台" };
router.afterEach((to) => {
  document.title = [to.meta.title, consoles[to.meta.area], "AI 标书工具"].filter(Boolean).join(" · ");
});
window.addEventListener("bid:signed-out", (event) => router.push(event.detail === "org" ? "/org/login" : "/platform/login"));
