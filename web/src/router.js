import { createRouter, createWebHistory } from "vue-router";
import { session } from "./api.js";
import Audit from "./views/Audit.vue";
import Login from "./views/Login.vue";
import Models from "./views/Models.vue";
import Orgs from "./views/Orgs.vue";
import SetupPassword from "./views/SetupPassword.vue";
import Usage from "./views/Usage.vue";

export const router = createRouter({
  history: createWebHistory("/app/"),
  routes: [
    { path: "/", redirect: "/platform/orgs" },
    { path: "/platform/login", component: Login, meta: { public: true } },
    { path: "/setup-password", component: SetupPassword, meta: { public: true } },
    { path: "/platform/orgs", component: Orgs },
    { path: "/platform/models", component: Models },
    { path: "/platform/usage", component: Usage },
    { path: "/platform/audit", component: Audit },
    { path: "/:rest(.*)", redirect: "/platform/orgs" },
  ],
});

router.beforeEach((to) => {
  if (!to.meta.public && !session.get()) return "/platform/login";
});

window.addEventListener("bid:signed-out", () => router.push("/platform/login"));
