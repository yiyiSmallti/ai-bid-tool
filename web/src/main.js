import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router.js";
// Services called from scripts (message boxes) need their styles loaded explicitly.
import "element-plus/es/components/message-box/style/css";
import "element-plus/es/components/message/style/css";
import "./style.css";

createApp(App).use(router).mount("#app");
