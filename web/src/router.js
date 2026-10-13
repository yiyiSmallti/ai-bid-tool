import { createRouter, createWebHistory } from "vue-router";
import { exchangeAccessKey, session, takeAccessKeyFragment } from "./api.js";
import { sessionRedirect } from "./session-guard.js";
import Credentials from "./views/Credentials.vue";
import Login from "./views/Login.vue";
import OrgConfidential from "./views/OrgConfidential.vue";
import OrgProfiles from "./views/OrgProfiles.vue";
import OrgTasks from "./views/OrgTasks.vue";
import OrgTaskBoard from "./views/OrgTaskBoard.vue";
import OrgTaskProgress from "./views/OrgTaskProgress.vue";
import OrgTask from "./views/OrgTask.vue";
import OrgRequirements from "./views/OrgRequirements.vue";
import OrgReview from "./views/OrgReview.vue";
import OrgDrafts from "./views/OrgDrafts.vue";

// Read at module load, before the router's initial navigation, so the key never reaches a request.
let pendingKey = takeAccessKeyFragment();
export const router = createRouter({
  history: createWebHistory("/app/"),
  routes: [
    { path: "/", redirect: "/org/tasks" },
    { path: "/login", component: Login, meta: { public: true, title: "访问密钥" } },
    { path: "/platform/credentials", component: Credentials, meta: { title: "服务凭据", nav: "credentials" } },
    { path: "/platform/clef", component: () => import("./views/Clef.vue"), meta: { title: "Clef 初筛配置", nav: "clef" } },
    { path: "/platform/trust-anchors", component: () => import("./views/TrustAnchors.vue"), meta: { title: "信任根证书", nav: "trust-anchors" } },
    { path: "/org/tasks", component: OrgTasks, meta: { title: "招标任务", nav: "tasks" } },
    { path: "/org/tasks/:taskId", component: OrgTask, meta: { title: "任务详情", nav: "tasks" } },
    { path: "/org/tasks/:taskId/board", component: OrgTaskBoard, meta: { title: "任务看板", nav: "tasks" } },
    { path: "/org/tasks/:taskId/progress", component: OrgTaskProgress, meta: { title: "作业进度", nav: "tasks" } },
    { path: "/org/tasks/:taskId/requirements", component: OrgRequirements, meta: { title: "要求确认与补录", nav: "tasks" } },
    { path: "/org/tasks/:taskId/cards/:cardId/annotation", component: () => import("./views/OrgAnnotation.vue"), meta: { title: "证据标注", nav: "tasks" } },
    { path: "/org/tasks/:taskId/review", component: OrgReview, meta: { title: "要求与响应审阅", nav: "tasks" } },
    { path: "/org/tasks/:taskId/drafts", component: OrgDrafts, meta: { title: "响应表初稿", nav: "tasks" } },
    { path: "/org/tasks/:taskId/bid-submissions", component: () => import("./views/OrgBidSubmissions.vue"), meta: { title: "标书检验", nav: "tasks" } },
    { path: "/org/tasks/:taskId/bid-submissions/:submissionId", component: () => import("./views/OrgBidSubmission.vue"), meta: { title: "标书提交与本地准备", nav: "tasks" } },
    { path: "/org/tasks/:taskId/bid-reviews/:reviewId/report", component: () => import("./views/OrgBidReviewReport.vue"), meta: { title: "标书检验报告", nav: "tasks" } },
    { path: "/org/tasks/:taskId/checks", component: () => import("./views/OrgChecks.vue"), meta: { title: "检查风险", nav: "tasks" } },
    { path: "/org/tasks/:taskId/checks/:reportId", component: () => import("./views/OrgCheckReport.vue"), meta: { title: "检查报告", nav: "tasks" } },
    { path: "/org/tasks/:taskId/score-rubrics", component: () => import("./views/OrgRubrics.vue"), meta: { title: "评分规则", nav: "tasks" } },
    { path: "/org/tasks/:taskId/score-rubrics/:rubricId", component: () => import("./views/OrgRubricReview.vue"), meta: { title: "评分规则审阅", nav: "tasks" } },
    { path: "/org/tasks/:taskId/scores", component: () => import("./views/OrgScores.vue"), meta: { title: "评分预估", nav: "tasks" } },
    { path: "/org/tasks/:taskId/scores/:reportId", component: () => import("./views/OrgScoreReport.vue"), meta: { title: "评分报告", nav: "tasks" } },
    { path: "/org/products", component: () => import("./views/OrgProducts.vue"), meta: { title: "产品库", nav: "products" } },
    { path: "/org/products/:productId", component: () => import("./views/OrgProduct.vue"), meta: { title: "产品详情", nav: "products" } },
    { path: "/org/features", component: () => import("./views/OrgFeatures.vue"), meta: { title: "功能库", nav: "features" } },
    { path: "/org/features/:featureId", component: () => import("./views/OrgFeature.vue"), meta: { title: "功能详情", nav: "features" } },
    { path: "/org/templates", component: () => import("./views/OrgTemplates.vue"), meta: { title: "模板库", nav: "templates" } },
    { path: "/org/templates/:templateId", component: () => import("./views/OrgTemplate.vue"), meta: { title: "模板详情", nav: "templates" } },
    { path: "/org/templates/:templateId/revisions/:revisionId/bindings", component: () => import("./views/OrgTemplateBindings.vue"), meta: { title: "模板绑定", nav: "templates" } },
    { path: "/org/attachments", component: () => import("./views/OrgAttachments.vue"), meta: { title: "附件档案", nav: "attachments" } },
    { path: "/org/attachments/:attachmentId", component: () => import("./views/OrgAttachment.vue"), meta: { title: "附件精确修订", nav: "attachments" } },
    { path: "/org/tasks/:taskId/attachments", component: () => import("./views/OrgTaskAttachments.vue"), meta: { title: "任务附件", nav: "tasks" } },
    { path: "/org/profiles", component: OrgProfiles, meta: { title: "单位资料", nav: "profiles" } },
    { path: "/org/profiles/:resourceId", component: () => import("./views/OrgQualification.vue"), meta: { title: "资料详情", nav: "profiles", resourceKind: "profiles" } },
    { path: "/org/certificates/:resourceId", component: () => import("./views/OrgQualification.vue"), meta: { title: "证照详情", nav: "profiles", resourceKind: "certificates" } },
    { path: "/org/memory", component: () => import("./views/OrgMemories.vue"), meta: { title: "单位记忆", nav: "memory" } },
    { path: "/org/memory/:memoryId", component: () => import("./views/OrgMemory.vue"), meta: { title: "记忆详情", nav: "memory" } },
    { path: "/org/tasks/:taskId/memory-feedback", component: () => import("./views/OrgMemoryFeedback.vue"), meta: { title: "记忆反馈队列", nav: "tasks" } },
    { path: "/org/settings/models", component: () => import("./views/OrgModels.vue"), meta: { title: "模型与服务配置", nav: "org-models" } },
    { path: "/org/confidential", component: OrgConfidential, meta: { title: "保密字段", nav: "confidential" } },
    { path: "/:rest(.*)", redirect: "/org/tasks" },
  ],
});
router.beforeEach(async (to) => {
  if (pendingKey !== null) {
    const key = pendingKey;
    pendingKey = null;
    try { await exchangeAccessKey(key); }
    catch { session.clear(); return { path: "/login", query: { rejected: "1" } }; }
  }
  if (to.meta.public) return;
  return sessionRedirect();
});
router.afterEach((to) => {
  document.title = [to.meta.title, "AI 标书工具"].filter(Boolean).join(" · ");
});
window.addEventListener("bid:signed-out", () => router.push("/login"));
