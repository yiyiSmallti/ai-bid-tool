package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Failure matrix before implementation: an added gateway skips the
// certificate confirmation or keeps the address after a refused probe; a wrong
// password stores a session; a new member reaches the console before changing
// the password; a wrong current password signs the member out; the password
// change leaves the forced flag set; signing out leaves the session in the
// Keychain or valid on the gateway; an expired session is kept, or leaves the
// console open; a password lands in workspaces.json.
func TestEnterpriseSignInFlow(t *testing.T) {
	g := newFakeGateway(t, "enterprise", clientContract)
	g.mustChange = true
	root := t.TempDir()
	secrets := &memorySecrets{}
	runner := &WorkspaceRunner{
		Store:      WorkspaceStore{Path: filepath.Join(root, "workspaces.json")},
		Secrets:    secrets,
		NewBackend: func() (*Backend, error) { return nil, errors.New("the local stack must not start") },
	}
	app := NewNativeApp()
	app.Runner = runner
	updates := make(chan func(), 64)
	app.Dispatch = func(apply func()) { updates <- apply }
	app.ConfirmDialog = func(string, string) (bool, error) { return true, nil }
	pump := func() {
		for idle := 0; idle < 6; {
			select {
			case apply := <-updates:
				apply()
				idle = 0
			case <-time.After(40 * time.Millisecond):
				idle++
			}
		}
	}
	steps := []string{}
	step := func(name string, ok bool, detail string) {
		steps = append(steps, name)
		if !ok {
			t.Fatalf("%s: %s (route %s, error %q, notice %q)", name, detail, app.Route.Path, app.Error, app.Notice)
		}
	}
	stored := func() string {
		token, _ := secrets.Get(app.Workspace.Workspace.ID)
		return token
	}

	// The personal workspace is open; its client is never called here.
	app.Client = NewAPIClient("http://127.0.0.1:9")
	app.Workspace = &OpenWorkspace{Workspace: Workspace{ID: personalWorkspace, Kind: "personal", Name: "本机"}, Client: app.Client}
	app.navigate("/workspaces/add")
	add, ok := app.page.(*addGatewayPage)
	step("add page opens", ok, "not the add page")
	add.address = g.URL
	add.probe(app, nil)
	pump()
	step("untrusted certificate asks for its fingerprint", add.untrusted == *g.pin() && add.info == nil, "fingerprint "+add.untrusted)
	fingerprint := add.untrusted
	add.probe(app, &fingerprint)
	pump()
	step("confirmed fingerprint reads the gateway", add.info != nil && add.info.CompanyName == "合成测试公司", "no gateway info")
	add.add(app)
	pump()
	step("added gateway opens at sign-in", app.enterprise() && app.Route.Path == "/login" && app.Workspace.Account == nil, "not at enterprise sign-in")

	login := app.page.(*loginPage)
	login.login, login.password = "synthetic.member", "wrong-password"
	login.signIn(app)
	pump()
	step("wrong password is refused without a session", app.Error == "登录名或密码不正确" && stored() == "" && login.password == "", "session stored or message wrong")

	login.login, login.password = "synthetic.member", "synthetic-password"
	login.signIn(app)
	pump()
	_, forced := app.page.(*passwordPage)
	full := forced && app.page.(nativeFullscreen).Fullscreen(app)
	step("new member is held at the password page", forced && full && app.mustChangePassword() && stored() == g.valid, "not held at a fullscreen password page")

	password := app.page.(*passwordPage)
	password.current, password.next, password.confirm = "not-the-password", "synthetic-new-password", "synthetic-new-password"
	password.change(app)
	pump()
	step("wrong current password keeps the session", app.Error == "当前密码不正确" && app.Workspace.Account != nil && app.Client.session == g.valid && stored() == g.valid, "session lost")

	password.current = "synthetic-password"
	password.change(app)
	pump()
	step("changed password opens the console", app.Route.Path == "/org/tasks" && !app.mustChangePassword() && app.Notice == "密码已更新，其他 1 处登录已退出" && g.password == "synthetic-new-password", "console not opened")

	app.Navigate("/account/sessions")
	pump()
	sessions, ok := app.page.(*sessionsPage)
	step("own sessions list", ok && len(sessions.items) == 1 && sessions.items[0]["current"] == true, "sessions not listed")

	app.signOut()
	pump()
	step("sign-out revokes and forgets the session", app.Route.Path == "/login" && stored() == "" && g.valid == "" && app.Workspace.Account == nil, "session kept")

	login = app.page.(*loginPage)
	login.login, login.password = "synthetic.member", "synthetic-new-password"
	login.signIn(app)
	pump()
	step("new password signs in to the console", app.Route.Path == "/org/tasks" && stored() != "", "not signed in")
	g.mu.Lock()
	g.valid = "revoked-by-admin"
	g.mu.Unlock()
	app.Navigate("/account/sessions")
	pump()
	step("expired session returns to sign-in", app.Route.Path == "/login" && app.Error == "登录已过期，请重新登录" && stored() == "" && app.Client.session == "", "expired session kept")

	data, err := os.ReadFile(runner.Store.Path)
	if err != nil {
		t.Fatal(err)
	}
	step("workspaces.json holds no secret", !strings.Contains(string(data), "password") && !strings.Contains(string(data), "session") && strings.Contains(string(data), fingerprint), string(data))
	g.mu.Lock()
	for _, line := range g.seen {
		if strings.HasPrefix(line, "POST /auth/session ") && strings.Contains(line, "Bearer") {
			t.Fatalf("sign-in carried a session: %s", line)
		}
	}
	g.mu.Unlock()
	steps = append(steps, "sign-in never carries a session")

	dir := filepath.Join("..", "data", "work", "enterprise-signin")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	report, _ := json.MarshalIndent(Object{"rerun": "go test -run TestEnterpriseSignInFlow .", "passed": steps}, "", "  ")
	if err := os.WriteFile(filepath.Join(dir, "report.json"), append(report, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
}
