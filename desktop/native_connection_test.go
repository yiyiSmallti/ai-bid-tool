package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Failure matrix before implementation: an unreachable gateway shows a raw
// socket error, opens the local stack, or forgets the stored session; retrying
// while it is still down clears the banner; a page that loses the connection
// is torn down as inaccessible; retrying after it returns leaves the banner up
// or the page empty; an incompatible gateway fails page by page instead of at
// sign-in with what to upgrade.
func TestGatewayConnectionBanner(t *testing.T) {
	g := newFakeGateway(t, "enterprise", clientContract)
	g.valid = "synthetic-session.1"
	root := t.TempDir()
	secrets := &memorySecrets{}
	const id = "6f1c2d3e-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
	runner := &WorkspaceRunner{
		Store:      WorkspaceStore{Path: filepath.Join(root, "workspaces.json")},
		Secrets:    secrets,
		NewBackend: func() (*Backend, error) { return nil, errors.New("the local stack must not start") },
	}
	gateway := Workspace{ID: id, Kind: "enterprise", Name: "合成测试公司", URL: g.URL, PinSHA256: g.pin()}
	if err := runner.Store.Save(Workspaces{Current: personalWorkspace, Workspaces: []Workspace{{ID: personalWorkspace, Kind: "personal", Name: "本机"}, gateway}}); err != nil {
		t.Fatal(err)
	}
	if err := secrets.Set(id, g.valid); err != nil {
		t.Fatal(err)
	}
	app := NewNativeApp()
	app.Runner = runner
	updates := make(chan func(), 64)
	app.Dispatch = func(apply func()) { updates <- apply }
	pump := func() {
		for idle := 0; idle < 8; {
			select {
			case apply := <-updates:
				apply()
				idle = 0
			case <-time.After(40 * time.Millisecond):
				idle++
			}
		}
	}
	down := func(v bool) { g.mu.Lock(); g.down = v; g.mu.Unlock() }
	steps := []string{}
	step := func(name string, ok bool) {
		steps = append(steps, name)
		if !ok {
			t.Fatalf("%s (route %s, offline %v, error %q, notice %q)", name, app.Route.Path, app.Offline, app.Error, app.Notice)
		}
	}
	stored := func() string { token, _ := secrets.Get(id); return token }

	down(true)
	app.switchWorkspace(id)
	pump()
	step("unreachable gateway shows the banner at sign-in", app.Offline && app.Error == "" && app.Route.Path == "/login" && app.enterprise() && stored() == g.valid)
	app.reconnect()
	pump()
	step("retry while down keeps the banner", app.Offline && app.Route.Path == "/login")
	down(false)
	app.reconnect()
	pump()
	step("retry after it returns resumes the session", !app.Offline && app.Route.Path == "/org/tasks" && app.Workspace.Account != nil)

	app.Navigate("/account/sessions")
	pump()
	down(true)
	app.Reload()
	pump()
	_, kept := app.page.(*sessionsPage)
	step("lost connection keeps the page with the banner", app.Offline && kept && app.Error == "" && app.Route.Path == "/account/sessions")
	down(false)
	app.reconnect()
	pump()
	sessions, kept := app.page.(*sessionsPage)
	step("retry reloads the page", !app.Offline && kept && len(sessions.items) == 1)

	for contract, message := range map[string]string{"6.0": "请升级客户端", "4.0": "请联系管理员升级企业服务"} {
		g.mu.Lock()
		g.contract = contract
		g.mu.Unlock()
		app.switchWorkspace(personalWorkspace)
		pump()
		app.switchWorkspace(id)
		pump()
		step("contract "+contract+" asks to "+message, !app.Offline && app.Error == message && app.Route.Path == "/login")
	}

	dir := filepath.Join("..", "data", "work", "gateway-connection")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	report, _ := json.MarshalIndent(Object{"rerun": "go test -run TestGatewayConnectionBanner .", "passed": steps}, "", "  ")
	if err := os.WriteFile(filepath.Join(dir, "report.json"), append(report, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
}
