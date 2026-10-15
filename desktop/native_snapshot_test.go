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
	config := Object{"id": "11111111-2222-4333-8444-555555555555", "revision": 1, "provider": "openai", "model": "local-fixture-model", "credential_state": "configured", "updated_at": "2026-10-14T09:12:00Z", "revised_at": "2026-10-14T09:12:00Z", "reasoning": choices, "default_reasoning": "low",
		"configuration": Object{"capability": "llm_extract", "provider": "openai", "model": "local-fixture-model", "base_url": "https://example.invalid/long-model-service-endpoint/v1", "json_mode": "json_object", "reasoning": levels, "default_reasoning": "low", "expected_revision": nil}}
	return map[string]snapFixture{
		"/management/providers":               {data: Object{"capability": "llm_extract", "configured": true, "current": config, "reasoning": choices, "connection_status": "not_checked", "actions": []Object{{"action": "configure", "allowed": true, "reason": nil}, {"action": "test", "allowed": true, "reason": nil}}}, items: []Object{}},
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
	}
}

func snapServer(t *testing.T) *httptest.Server {
	fixtures := snapFixtures()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fixture, ok := fixtures[r.URL.Path]
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
			app.Navigate(route)
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
