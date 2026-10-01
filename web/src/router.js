import { createRouter, createWebHistory } from "vue-router";
import { orgSession, session } from "./api.js";
import Audit from "./views/Audit.vue";
import Cards from "./views/Cards.vue";
import Login from "./views/Login.vue";
import Models from "./views/Models.vue";
import OrgBilling from "./views/OrgBilling.vue";
import OrgLogin from "./views/OrgLogin.vue";
import Orgs from "./views/Orgs.vue";
import SetupPassword from "./views/SetupPassword.vue";
import Usage from "./views/Usage.vue";

export const router = createRouter({
  history: createWebHistory("/app/"),
  routes: [
    { path: "/", redirect: "/org/billing" },
    { path: "/setup-password", component: SetupPassword, meta: { public: true } },
    { path: "/platform/login", component: Login, meta: { public: true } },
    { path: "/platform", redirect: "/platform/orgs" },
    { path: "/platform/orgs", component: Orgs, meta: { area: "platform" } },
    { path: "/platform/models", component: Models, meta: { area: "platform" } },
    { path: "/platform/cards", component: Cards, meta: { area: "platform" } },
    { path: "/platform/usage", component: Usage, meta: { area: "platform" } },
    { path: "/platform/audit", component: Audit, meta: { area: "platform" } },
    { path: "/org/login", component: OrgLogin, meta: { public: true } },
    { path: "/org/billing", component: OrgBilling, meta: { area: "org" } },
    { path: "/:rest(.*)", redirect: "/org/billing" },
  ],
});

router.beforeEach((to) => {
  if (to.meta.area === "platform" && !session.get()) return "/platform/login";
  if (to.meta.area === "org" && !orgSession.get()) return "/org/login";
});

window.addEventListener("bid:signed-out", (event) =>
  router.push(event.detail === "org" ? "/org/login" : "/platform/login"),
);
