//go:build uisnap

package main

import (
	"encoding/json"
	"fmt"
	"image/png"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"
)

// Renders console pages against a synthetic API into PNG files for visual
// review. Run with: go test -tags uisnap -run TestNativeSnapshots .
// BID_UI_SNAP_DIR selects the output directory.

type snapFixture struct {
	data  Object
	items []Object
}

func snapTask(id, name, number, deadline string, redaction bool) Object {
	return Object{"id": id, "name": name, "tender_number": number, "deadline": deadline, "model_redaction_enabled": redaction, "created_at": "2026-10-10T08:00:00Z"}
}

func snapFixtures() map[string]snapFixture {
	tasks := []Object{
		snapTask("t-1", "市人民医院智慧病房信息化建设项目", "ZB-2026-1108", "2026-10-21T01:30:00Z", true),
		snapTask("t-2", "区政务云平台扩容及安全加固采购", "GZY-2026-0342", "2026-11-04T02:00:00Z", true),
		snapTask("t-3", "高新区智慧园区综合管理平台", "", "2026-10-12T06:00:00Z", false),
		snapTask("t-4", "教育局校园网络设备集中采购（第二批）", "JY-2026-077", "", true),
	}
	docs := []Object{
		{"id": "d-1", "name": "招标文件-智慧病房.pdf", "status": "parsed", "page_count": 186, "citation_mode": "page"},
		{"id": "d-2", "name": "技术需求补充说明.docx", "status": "uploaded", "page_count": nil, "citation_mode": nil},
	}
	extractions := []Object{
		{"job_id": "j-ext-2", "status": "succeeded", "reasoning": "high", "model": "claude-opus-5-5", "saved": 214, "rejected": 3, "document_id": "d-1", "created_at": "2026-10-14T09:12:00Z"},
		{"job_id": "j-ext-1", "status": "failed", "reasoning": "medium", "model": nil, "saved": nil, "rejected": nil, "document_id": "d-1", "created_at": "2026-10-13T16:40:00Z", "error": Object{"code": "provider_unavailable", "message": "LLM request failed with HTTP 429 (1308)"}},
	}
	product := func(id, name string, revision int, provenance string) Object {
		return Object{"ref": Object{"kind": "products", "resource_id": id}, "name": name, "revision": revision, "lifecycle": Object{"state": "active"}, "provenance": provenance, "revised_at": "2026-10-12T03:20:00Z"}
	}
	products := []Object{
		product("p-1", "移动护理终端 MT-800", 3, "declared"),
		product("p-2", "护理信息系统 V6", 7, "declared"),
		product("p-3", "床旁交互一体机 BX-21", 1, "simulated"),
	}
	requirement := func(id, text, category, state, origin string, starred bool) Object {
		return Object{"requirement_id": id, "state": state, "origin": origin, "content": Object{"text": text, "category": category, "starred": starred}}
	}
	requirements := []Object{
		requirement("r-1", "投标人须具有有效的营业执照及医疗器械经营许可证。", "qualification", "confirmed", "model", true),
		requirement("r-2", "移动护理终端须支持 Wi-Fi 6 与 5G 双模接入，电池续航不少于 10 小时。", "technical", "unconfirmed", "model", true),
		requirement("r-3", "系统须与院内 HIS、LIS、PACS 完成接口对接并提供接口文档。", "technical", "unconfirmed", "model", false),
		requirement("r-4", "技术方案评分 30 分，按方案完整性与可实施性分档评分。", "scoring", "confirmed", "model", false),
		requirement("r-5", "质保期不少于 3 年，提供 7×24 小时响应服务。", "substantive", "unconfirmed", "manual", false),
	}
	levels := []Object{
		{"name": "low", "label": "轻量推理，适合初步整理", "request_options": Object{"reasoning_effort": "low", "thinking": Object{"type": "enabled"}}, "effort": nil, "batch_chars": 8000},
		{"name": "high", "label": "深度推理，适合复杂条款", "request_options": Object{"reasoning_effort": "high", "thinking": Object{"type": "enabled"}}, "effort": "high", "batch_chars": 4000},
	}
	choices := []Object{{"name": "low", "label": "轻量推理"}, {"name": "high", "label": "深度推理"}}
	config := Object{"id": "11111111-2222-4333-8444-555555555555", "slot": "aaaaaaaa-2222-4333-8444-555555555555", "name": "智谱 GLM", "revision": 3, "provider": "openai", "model": "glm-5.3-flash", "credential_state": "configured", "updated_at": "2026-10-14T09:12:00Z", "revised_at": "2026-10-14T09:12:00Z", "reasoning": choices, "default_reasoning": "low",
		"configuration": Object{"capability": "llm_extract", "owner": "company", "slot": "aaaaaaaa-2222-4333-8444-555555555555", "new": false, "name": "智谱 GLM", "provider": "openai", "model": "glm-5.3-flash", "base_url": "https://open.bigmodel.cn/api/paas/v4", "json_mode": "json_object", "reasoning": levels, "default_reasoning": "low", "expected_revision": nil}}
	other := func(id, slot, name, provider, model, endpoint string, revision int, owner string) Object {
		return Object{"id": id, "slot": slot, "name": name, "revision": revision, "provider": provider, "model": model, "credential_state": "configured", "updated_at": "2026-10-15T02:00:00Z", "revised_at": "2026-10-15T02:00:00Z", "reasoning": []Object{}, "default_reasoning": nil,
			"configuration": Object{"capability": "llm_extract", "owner": owner, "slot": slot, "new": false, "name": name, "provider": provider, "model": model, "base_url": mgNull(endpoint), "json_mode": "json_object", "reasoning": []Object{}, "default_reasoning": nil, "expected_revision": nil}}
	}
	models := []Object{config,
		other("22222222-2222-4333-8444-555555555555", "bbbbbbbb-2222-4333-8444-555555555555", "DeepSeek", "openai", "deepseek-v4-flash", "https://api.deepseek.com", 2, "company"),
		other("33333333-2222-4333-8444-555555555555", "cccccccc-2222-4333-8444-555555555555", "Claude", "anthropic", "claude-sonnet-5-5", "", 1, "company")}
	personal := other("44444444-2222-4333-8444-555555555555", "dddddddd-2222-4333-8444-555555555555", "我的 Kimi", "openai", "kimi-k3", "https://api.moonshot.cn/v1", 5, "personal")
	actions := []Object{{"action": "configure", "allowed": true, "reason": nil}, {"action": "test", "allowed": false, "reason": "scope_required"}}
	return map[string]snapFixture{
		"/management/providers": {data: Object{"capability": "llm_extract", "configured": true, "current": config, "models": models, "default_slot": "aaaaaaaa-2222-4333-8444-555555555555", "reasoning": choices, "connection_status": "not_checked", "actions": []Object{{"action": "configure", "allowed": true, "reason": nil}, {"action": "test", "allowed": true, "reason": nil}}}, items: []Object{}},
		// The enterprise prefix stands for a gateway member's view.
		"/ent/management/providers":           {data: Object{"capability": "llm_extract", "configured": true, "current": config, "models": append(append([]Object{}, models...), personal), "default_slot": "aaaaaaaa-2222-4333-8444-555555555555", "reasoning": choices, "connection_status": "not_checked", "actions": actions}, items: []Object{}},
		"/ent/providers":                      {data: Object{}, items: []Object{{"slot": "aaaaaaaa-2222-4333-8444-555555555555", "is_member_default": false}, {"slot": "dddddddd-2222-4333-8444-555555555555", "is_member_default": true}}},
		"/management/memories/query":          {data: Object{"limit": 25, "next_cursor": ""}, items: []Object{}},
		"/management/providers/history/query": {data: Object{"as_of": "2026-10-14T09:12:00Z", "returned": 1, "next_cursor": nil, "has_more": false}, items: []Object{config}},

		"/tasks":                               {data: Object{}, items: tasks},
		"/tasks/t-1":                           {data: tasks[0], items: []Object{}},
		"/tasks/t-1/documents":                 {data: Object{}, items: docs},
		"/tasks/t-1/extractions":               {data: Object{}, items: extractions},
		"/tasks/t-1/exports":                   {data: Object{}, items: []Object{}},
		"/tasks/t-1/jobs":                      {data: Object{}, items: []Object{{"id": "j-parse-1", "status": "succeeded", "kind": "parse"}}},
		"/management/resources/products/query": {data: Object{"limit": 25, "next_cursor": ""}, items: products},
		"/tasks/t-1/extractions/j-ext-2/requirement-reviews": {data: Object{"scope": Object{"task_id": "t-1", "extraction_job_id": "j-ext-2", "revision": 4, "confirmation_sha256": "c0ffee"}, "next_cursor": ""}, items: requirements},
		"/confidential-fields":                               {data: Object{}, items: []Object{}},
		"/account/sessions": {data: Object{}, items: []Object{
			{"id": "s-1", "created_at": "2026-10-15T01:00:00Z", "expires_at": "2026-10-15T13:00:00Z", "last_seen_at": "2026-10-15T06:40:00Z", "client_label": "AI Bid 桌面客户端", "current": true},
			{"id": "s-2", "created_at": "2026-10-14T09:00:00Z", "expires_at": "2026-10-14T21:00:00Z", "last_seen_at": "2026-10-14T11:20:00Z", "client_label": "AI Bid 桌面客户端", "current": false},
		}},
		"/members": {data: Object{}, items: []Object{
			{"id": "m-1", "login": "zhangwei", "display_name": "张伟", "role": "admin", "grants": []any{}, "status": "active"},
			{"id": "m-2", "login": "lina", "display_name": "李娜", "role": "member", "grants": []any{"library:edit", "memory:edit"}, "status": "active"},
			{"id": "m-3", "login": "wangqiang", "display_name": "王强", "role": "member", "grants": []any{}, "status": "active"},
			{"id": "m-4", "login": "zhaomin", "display_name": "赵敏", "role": "member", "grants": []any{"confidential:reveal"}, "status": "disabled"},
		}},
		"/members/m-2/usage": {data: Object{"member_id": "m-2", "month": "2026-10", "timezone": "UTC", "monthly_company_tokens": 2000000, "company_tokens": 486320}, items: []Object{}},
		"/members/m-1/usage": {data: Object{"member_id": "m-1", "month": "2026-10", "timezone": "UTC", "monthly_company_tokens": nil, "company_tokens": 486320}, items: []Object{
			{"owner": "company", "provider": "openai", "model": "glm-5.3-flash", "calls": 42, "input_tokens": 401200, "output_tokens": 85120, "tokens": 486320},
			{"owner": "personal", "provider": "openai", "model": "kimi-k3", "calls": 7, "input_tokens": 61000, "output_tokens": 9800, "tokens": 70800},
		}},
		"/tasks/t-1/members": {data: Object{}, items: []Object{
			{"member_id": "m-2", "login": "lina", "display_name": "李娜", "role": "owner", "status": "active", "added_at": "2026-10-10T08:00:00Z"},
			{"member_id": "m-1", "login": "zhangwei", "display_name": "张伟", "role": "member", "status": "active", "added_at": "2026-10-11T02:30:00Z"},
			{"member_id": "m-3", "login": "wangqiang", "display_name": "王强", "role": "member", "status": "active", "added_at": "2026-10-12T06:10:00Z"},
		}},
	}
}

func snapServer(t *testing.T) *httptest.Server {
	fixtures := snapFixtures()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fixture, ok := fixtures[r.URL.Path]
		if !ok {
			fixture, ok = fixtures[strings.TrimPrefix(r.URL.Path, "/ent")]
		}
		if !ok {
			fixture = snapFixture{data: Object{}, items: []Object{}}
		}
		_ = json.NewEncoder(w).Encode(Object{
			"ok": true, "command": "snapshot", "data": fixture.data, "items": fixture.items,
			"warnings": []string{}, "duration_ms": 0,
			"cost": Object{"calls": 0, "llm_tokens": 0, "ocr_pages": 0, "search_requests": 0, "browser_units": 0, "unresolved_calls": 0},
		})
	}))
}

func TestNativeSnapshots(t *testing.T) {
	dir := os.Getenv("BID_UI_SNAP_DIR")
	if dir == "" {
		dir = filepath.Join("..", "data", "work", "ui-snap")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	server := snapServer(t)
	defer server.Close()
	routes := map[string]string{
		"login":        "/login",
		"tasks":        "/org/tasks",
		"task-detail":  "/org/tasks/t-1",
		"requirements": "/org/tasks/t-1/requirements?job=j-ext-2",
		"products":     "/org/products",
		"models":       "/org/settings/models",
		"models-add":   "/org/settings/models#add",
		"add-gateway":  "/workspaces/add",
		// Enterprise workspace pages; the ent- prefix opens the gateway workspace.
		"ent-login":           "/login",
		"ent-password-forced": "/account/password",
		"ent-password":        "/account/password",
		"ent-sessions":        "/account/sessions",
		"ent-tasks":           "/org/tasks",
		"ent-members":         "/org/members",
		"ent-project-members": "/org/tasks/t-1/members",
		"ent-models":          "/org/settings/models",
		"ent-memory":          "/org/memory",
		"ent-memory-private":  "/org/memory?scope=private",
		"ent-usage":           "/org/usage",
		"ent-my-usage":        "/account/usage",
		"ent-offline":         "/org/tasks",
		"ent-login-offline":   "/login",
	}
	store := WorkspaceStore{Path: filepath.Join(t.TempDir(), "workspaces.json")}
	gateway := Workspace{ID: "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b", Kind: "enterprise", Name: "华东建设集团", URL: "https://bid.example.com"}
	if err := store.Save(Workspaces{Current: personalWorkspace, Workspaces: []Workspace{{ID: personalWorkspace, Kind: "personal", Name: "本机"}, gateway}}); err != nil {
		t.Fatal(err)
	}
	if only := os.Getenv("BID_UI_SNAP_ONLY"); only != "" {
		for name := range routes {
			if !strings.Contains(","+only+",", ","+name+",") {
				delete(routes, name)
			}
		}
	}
	for _, dark := range []bool{false, true} {
		for name, route := range routes {
			app := NewNativeApp()
			app.Client = NewAPIClient(server.URL)
			app.Runner = &WorkspaceRunner{Store: store, Secrets: &memorySecrets{items: map[string]string{}}}
			app.Workspace = &OpenWorkspace{Workspace: Workspace{ID: personalWorkspace, Kind: "personal", Name: "本机"}, Client: app.Client}
			if strings.HasPrefix(name, "ent-") {
				app.Client = NewAPIClient(server.URL + "/ent")
				app.Workspace = &OpenWorkspace{Workspace: gateway, Client: app.Client}
				if !strings.HasPrefix(name, "ent-login") {
					role := "admin"
					if name == "ent-sessions" || name == "ent-password" || name == "ent-models" || name == "ent-memory" || name == "ent-memory-private" || name == "ent-my-usage" {
						role = "member"
					}
					app.Workspace.Account = Object{"mode": "enterprise", "actor_kind": "user", "must_change_password": name == "ent-password-forced",
						"member": Object{"id": "m-1", "login": "zhangwei", "display_name": "张伟", "role": role, "grants": []any{}, "status": "active"}}
				}
			}
			updates := make(chan func(), 64)
			app.Dispatch = func(apply func()) { updates <- apply }
			width, height := 1360, 880
			if size := os.Getenv("BID_UI_SNAP_SIZE"); size != "" {
				fmt.Sscanf(size, "%dx%d", &width, &height)
			}
			tt := ui.NewTester(app.View, width, height)
			tt.SetScale(2)
			tt.SetDark(dark)
			// As the macOS window with a hidden title bar.
			tt.SetTitleBar(ui.TitleBar{Height: 28, Left: 78})
			app.Navigate(strings.TrimSuffix(route, "#add"))
			if route == "/login" {
				app.Client = NewAPIClient(server.URL)
			}
			for idle := 0; idle < 6; {
				select {
				case apply := <-updates:
					apply()
					idle = 0
				case <-time.After(60 * time.Millisecond):
					idle++
				}
				tt.Frame()
			}
			// As a gateway that stopped answering.
			if strings.HasSuffix(name, "-offline") {
				app.Offline = true
				tt.Frame()
			}
			// Opens the add-model form with a vendor template, as a click on 添加模型 would.
			if page, ok := app.page.(*modelSettingsPage); ok && strings.HasSuffix(route, "#add") {
				page.startEdit(app, nil, "company")
				page.template = "zhipu"
				page.applyTemplate("zhipu")
				tt.Frame()
			}
			// As a gateway whose self-signed certificate awaits confirmation.
			if page, ok := app.page.(*addGatewayPage); ok {
				page.address = "https://10.8.0.12:8443"
				page.untrusted = strings.Repeat("3fa9", 16)
				tt.Frame()
			}
			// As an administrator who just created a member and opened another.
			if page, ok := app.page.(*membersPage); ok {
				page.oneTime = &oneTimePassword{login: "wangqiang", name: "王强", password: "Tq7-vR2k-Lm9x-Pe4w"}
				page.choose(app, "m-2")
				for idle := 0; idle < 4; idle++ {
					select {
					case apply := <-updates:
						apply()
					case <-time.After(60 * time.Millisecond):
					}
					tt.Frame()
				}
			}
			tt.Frame()
			suffix := ""
			if dark {
				suffix = "-dark"
			}
			file, err := os.Create(filepath.Join(dir, name+suffix+".png"))
			if err != nil {
				t.Fatal(err)
			}
			if err := png.Encode(file, tt.Image()); err != nil {
				t.Fatal(err)
			}
			file.Close()
			app.cancel()
		}
	}
}
