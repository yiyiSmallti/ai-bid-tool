package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// realGateway runs the Python server in enterprise mode on a disposable
// cluster, behind a TLS proxy the client pins, as a company would behind its
// reverse proxy. It returns the gateway origin and the bootstrap
// administrator's one-time password.
func realGateway(t *testing.T, root string) (*httptest.Server, string) {
	server, pgBin := os.Getenv("BID_DESKTOP_TEST_SERVER"), os.Getenv("BID_DESKTOP_TEST_PG_BIN")
	if server == "" || pgBin == "" {
		t.Skip("set BID_DESKTOP_TEST_SERVER and BID_DESKTOP_TEST_PG_BIN for isolated runtime acceptance")
	}
	b := &Backend{DataDir: filepath.Join(root, "data"), LogDir: filepath.Join(root, "logs"), SocketDir: filepath.Join(root, "socket"), ServerDir: server, Python: filepath.Join(server, ".venv", "bin", "python"), PGBin: pgBin}
	for _, dir := range []string{b.DataDir, b.LogDir, b.SocketDir, filepath.Join(b.DataDir, "files")} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := b.loadInstallation(); err != nil {
		t.Fatal(err)
	}
	if err := b.startPostgres(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(b.Stop)

	var target *url.URL
	proxy := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		httputil.NewSingleHostReverseProxy(target).ServeHTTP(w, r)
	}))
	t.Cleanup(proxy.Close)
	enterprise := []string{"BID_DEPLOYMENT=enterprise", "BID_PUBLIC_URL=" + proxy.URL, "BID_COMPANY_NAME=合成测试公司"}
	if err := b.runPython("admin-init-db.log", append(b.ownerEnv(), enterprise...), "-m", "app.admin", "init-db"); err != nil {
		t.Fatal(err)
	}
	out, err := b.pythonOutput("admin-member.log", append(b.ownerEnv(), enterprise...), "-m", "app.admin", "member", "create", "--role", "admin", "--login", "synthetic.admin", "--display-name", "合成管理员")
	if err != nil {
		t.Fatal(err)
	}
	var created Object
	if err := json.Unmarshal([]byte(out), &created); err != nil || str(created, "initial_password") == "" {
		t.Fatalf("bootstrap administrator: %v", err)
	}
	port, err := freePort()
	if err != nil {
		t.Fatal(err)
	}
	target, _ = url.Parse("http://127.0.0.1:" + port)
	log, err := os.Create(filepath.Join(b.LogDir, "api.log"))
	if err != nil {
		t.Fatal(err)
	}
	api := exec.Command(b.Python, "-m", "uvicorn", "app.api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", port, "--no-access-log")
	api.Dir, api.Env, api.Stdout, api.Stderr = server, append(b.baseEnv(), enterprise...), log, log
	if err := api.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = api.Process.Signal(syscall.SIGTERM)
		_ = api.Wait()
		log.Close()
	})
	for deadline := time.Now().Add(90 * time.Second); ; time.Sleep(300 * time.Millisecond) {
		if resp, err := http.Get(target.String() + "/health"); err == nil {
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				break
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("gateway not healthy; see %s", log.Name())
		}
	}
	return proxy, str(created, "initial_password")
}

// Failure matrix before implementation: a member reads the member list or
// sees the administration pages; a created or reset password is not shown, or
// stays after leaving the page; a saved quota or grant does not reach the
// gateway; demoting the last administrator closes the page or claims success;
// adding, handing over or removing a project member leaves the list stale; the
// owner can be removed; a member who is not the owner can change the project.
func TestEnterpriseAdminPages(t *testing.T) {
	root, err := os.MkdirTemp("/tmp", "aibid-admin-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(root) })
	proxy, initial := realGateway(t, root)
	ctx := context.Background()
	pin := certificateFingerprint(proxy.Certificate())

	store := WorkspaceStore{Path: filepath.Join(root, "workspaces.json")}
	list, _ := store.Load()
	list.Workspaces = append(list.Workspaces, Workspace{ID: "6f1c2d3e-4a5b-4c6d-8e7f-8091a2b3c4d5", Kind: "enterprise", Name: "合成测试公司", URL: proxy.URL, PinSHA256: &pin})
	if err := store.Save(list); err != nil {
		t.Fatal(err)
	}
	runner := &WorkspaceRunner{Store: store, Secrets: &memorySecrets{}, NewBackend: func() (*Backend, error) { t.Fatal("the local stack must not start"); return nil, nil }}
	app := NewNativeApp()
	app.Runner = runner
	updates := make(chan func(), 64)
	app.Dispatch = func(apply func()) { updates <- apply }
	app.ConfirmDialog = func(string, string) (bool, error) { return true, nil }
	pump := func() {
		for idle := 0; idle < 8; {
			select {
			case apply := <-updates:
				apply()
				idle = 0
			case <-time.After(60 * time.Millisecond):
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
	signIn := func(login, password, next string) {
		page, ok := app.page.(*loginPage)
		if !ok {
			t.Fatalf("not at sign-in: %s", app.Route.Path)
		}
		page.login, page.password = login, password
		page.signIn(app)
		pump()
		change, ok := app.page.(*passwordPage)
		if !ok {
			t.Fatalf("%s is not held at the password page: %s %q", login, app.Route.Path, app.Error)
		}
		change.current, change.next, change.confirm = password, next, next
		change.change(app)
		pump()
	}

	opened, err := runner.Open(ctx, "6f1c2d3e-4a5b-4c6d-8e7f-8091a2b3c4d5")
	app.opened(opened, err)
	signIn("synthetic.admin", initial, "synthetic-admin-password")
	step("administrator signs in", app.admin() && app.Route.Path == "/org/tasks", "not an administrator in the console")

	app.Navigate("/org/members")
	pump()
	members, ok := app.page.(*membersPage)
	step("member list opens", ok && len(members.items) == 1, "no member list")
	members.creating = true
	members.newLogin, members.newName = "synthetic.member", "合成成员"
	members.newGrants["memory:edit"] = true
	members.create(app)
	pump()
	step("created member shows its one-time password", members.oneTime != nil && members.oneTime.login == "synthetic.member" && len(members.oneTime.password) >= 12 && len(members.items) == 2 && !members.creating, "no one-time password")
	memberPassword := members.oneTime.password
	step("an unread one-time password guards leaving", members.Dirty(), "page not dirty")
	members.oneTime = nil

	var memberID string
	for _, item := range members.items {
		if str(item, "login") == "synthetic.member" {
			memberID = str(item, "id")
		}
	}
	members.choose(app, memberID)
	pump()
	step("member opens in the editor", members.selected == memberID && members.edit.grants["memory:edit"] && members.edit.quota == "", "editor not loaded")
	members.edit.quota = "50000"
	members.edit.grants["library:edit"] = true
	members.save(app, memberID)
	pump()
	usage, err := app.Client.Request(ctx, "GET", "/members/"+memberID+"/usage", nil)
	if err != nil {
		t.Fatal(err)
	}
	saved := members.find(memberID)
	step("quota and grants reach the gateway", integer(usage.Data, "monthly_company_tokens") == 50000 && heldGrants(saved)["library:edit"] && heldGrants(saved)["memory:edit"] && members.edit.same(members.loaded), "not saved")

	members.resetPassword(app, saved)
	pump()
	step("reset shows a new one-time password", members.oneTime != nil && members.oneTime.password != memberPassword, "no new password")
	memberPassword = members.oneTime.password
	members.oneTime = nil

	self := str(app.member(), "id")
	members.choose(app, self)
	pump()
	members.edit.role = "member"
	members.save(app, self)
	pump()
	_, stillMembers := app.page.(*membersPage)
	step("demoting the last administrator is refused in place", stillMembers && app.Error == "至少需要保留一名启用的管理员" && app.admin(), "refusal not shown")
	members.edit = members.loaded
	app.Navigate("/org/members")
	pump()
	step("leaving after the one-time password was dismissed needs no confirmation", app.Error == "", "")

	created, err := app.Client.Request(ctx, "POST", "/tasks", Object{"name": "合成项目成员任务"})
	if err != nil {
		t.Fatal(err)
	}
	task := str(created.Data, "id")
	app.Navigate("/org/tasks/" + task + "/members")
	pump()
	project, ok := app.page.(*projectMembersPage)
	step("project members list the creator as owner", ok && len(project.items) == 1 && str(project.items[0], "role") == "owner" && project.manages(app), "no owner")
	project.add = memberID
	project.change(app, "POST", "/tasks/"+task+"/members", Object{"member_ids": []string{memberID}}, "")
	pump()
	step("administrator adds a member", len(project.items) == 2, "member not added")
	project.change(app, "POST", "/tasks/"+task+"/owner", Object{"member_id": memberID}, "")
	pump()
	owner := ""
	for _, item := range project.items {
		if str(item, "role") == "owner" {
			owner = str(item, "member_id")
		}
	}
	step("ownership moves to the member", owner == memberID && len(project.items) == 2, "owner "+owner)
	project.change(app, "DELETE", "/tasks/"+task+"/members/"+memberID, nil, "")
	pump()
	step("the owner cannot be removed", app.Error == "请先把项目交给其他负责人，再移出原负责人" && len(project.items) == 2, "owner removed")

	app.signOut()
	pump()
	signIn("synthetic.member", memberPassword, "synthetic-member-password")
	step("member signs in without administration", app.Route.Path == "/org/tasks" && app.member() != nil && !app.admin(), "member is an administrator")
	app.Navigate("/org/tasks/" + task + "/members")
	pump()
	project, ok = app.page.(*projectMembersPage)
	step("the owning member manages the project without the member list", ok && project.manages(app) && project.directory == nil, "owner cannot manage")
	project.change(app, "POST", "/tasks/"+task+"/owner", Object{"member_id": self}, "")
	pump()
	step("the owner hands the project back", !project.manages(app) && len(project.items) == 2, "still owner")
	project.change(app, "DELETE", "/tasks/"+task+"/members/"+self, nil, "")
	pump()
	_, open := app.page.(*projectMembersPage)
	step("a member who is not the owner cannot change the project", open && app.Error == "只有项目负责人或管理员可以管理项目成员" && len(project.items) == 2, "change accepted")
	app.Navigate("/org/members")
	pump()
	step("a member cannot read the member list", app.page == nil && app.Error == "当前内容不可访问，请返回任务或重新读取", "member list readable")

	dir := filepath.Join("..", "data", "work", "enterprise-admin")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	report, _ := json.MarshalIndent(Object{"rerun": "BID_DESKTOP_TEST_SERVER=... BID_DESKTOP_TEST_PG_BIN=... go test -run TestEnterpriseAdminPages .", "gateway": "python server, enterprise mode, behind a pinned TLS proxy", "passed": steps}, "", "  ")
	if err := os.WriteFile(filepath.Join(dir, "report.json"), append(report, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(report), memberPassword) || strings.Contains(string(report), initial) {
		t.Fatal("a password reached the report")
	}
}
