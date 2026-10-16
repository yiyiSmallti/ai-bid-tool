package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// waitPackage applies dispatched updates until a package job ends; its polls
// run outside the busy count, so pumpRequests alone would stop early.
func waitPackage(t *testing.T, app *NativeApp, updates chan func(), panel *packagePanel) {
	t.Helper()
	deadline := time.After(120 * time.Second)
	for panel.id == "" || !panel.done() || app.Busy {
		select {
		case apply := <-updates:
			apply()
		case <-time.After(60 * time.Millisecond):
		case <-deadline:
			t.Fatalf("package job %s still %q (error %q)", panel.id, panel.status(), app.Error)
		}
	}
	pumpRequests(t, app, updates)
}

// Failure matrix before implementation: a gateway project that does not allow
// export offers the export action, or a refused export closes the page; the
// administrator's sharing switch does not reach the gateway; a saved package
// differs from the exported bytes, is readable by others, or a package whose
// bytes differ from the report is accepted; importing into an installation
// that already holds the project claims success or hides the reason; an import
// into a personal installation does not list the project under its name or
// leaves it without the export action.
func TestPackagePages(t *testing.T) {
	root, err := os.MkdirTemp("/tmp", "aibid-package-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(root) })
	proxy, initial := realGateway(t, root)
	ctx := context.Background()
	pin := certificateFingerprint(proxy.Certificate())
	store := WorkspaceStore{Path: filepath.Join(root, "workspaces.json")}
	list, _ := store.Load()
	list.Workspaces = append(list.Workspaces, Workspace{ID: "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", Kind: "enterprise", Name: "合成测试公司", URL: proxy.URL, PinSHA256: &pin})
	if err := store.Save(list); err != nil {
		t.Fatal(err)
	}
	runner := &WorkspaceRunner{Store: store, Secrets: &memorySecrets{}, NewBackend: func() (*Backend, error) { t.Fatal("the local stack must not start"); return nil, nil }}
	saved := filepath.Join(root, "saved")
	if err := os.MkdirAll(saved, 0o700); err != nil {
		t.Fatal(err)
	}
	newApp := func() (*NativeApp, chan func()) {
		app := NewNativeApp()
		updates := make(chan func(), 64)
		app.Dispatch = func(apply func()) { updates <- apply }
		app.ConfirmDialog = func(string, string) (bool, error) { return true, nil }
		app.SaveDestination = func(name string) (string, error) { return filepath.Join(saved, name), nil }
		return app, updates
	}
	app, updates := newApp()
	app.Runner = runner
	pump := func() { pumpRequests(t, app, updates) }
	step := func(name string, ok bool, detail string) {
		if !ok {
			t.Fatalf("%s: %s (route %s, error %q, notice %q)", name, detail, app.Route.Path, app.Error, app.Notice)
		}
	}

	opened, err := runner.Open(ctx, "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
	app.opened(opened, err)
	login := app.page.(*loginPage)
	login.login, login.password = "synthetic.admin", initial
	login.signIn(app)
	pump()
	change := app.page.(*passwordPage)
	change.current, change.next, change.confirm = initial, "synthetic-admin-password", "synthetic-admin-password"
	change.change(app)
	pump()
	step("administrator signs in", app.admin() && app.Route.Path == "/org/tasks", "not signed in")

	created, err := app.Client.Request(ctx, "POST", "/tasks", Object{"name": "合成项目包任务", "tender_number": "SYN-PKG-001"})
	if err != nil {
		t.Fatal(err)
	}
	task := str(created.Data, "id")
	app.Navigate("/org/tasks/" + task)
	pump()
	detail, ok := app.page.(*taskDetailPage)
	step("a project that does not allow export hides the action", ok && detail.task != nil && !detail.exportable(app), "export offered")
	detail.exportPackage(app)
	pump()
	_, stays := app.page.(*taskDetailPage)
	step("a refused export stays on the page with its reason", stays && app.Error == packageFailures["package_export_not_allowed"] && detail.exported.id == "", "refusal not shown")

	app.Navigate("/org/tasks/" + task + "/members")
	pump()
	project, ok := app.page.(*projectMembersPage)
	step("the sharing switch opens off", ok && project.sharingLoaded && !project.exportable, "switch not loaded")
	project.share(app, true)
	pump()
	shown, err := app.Client.Request(ctx, "GET", "/tasks/"+task, nil)
	step("the sharing switch reaches the gateway", err == nil && project.exportable && shown.Data["export_to_personal_allowed"] == true, "not shared")

	app.Navigate("/org/tasks/" + task)
	pump()
	detail = app.page.(*taskDetailPage)
	step("an allowed project offers export", detail.exportable(app), "export hidden")
	detail.exportPackage(app)
	waitPackage(t, app, updates, &detail.exported)
	report := object(detail.exported.data["report"])
	step("the export succeeds with its report", detail.exported.status() == "succeeded" && integer(report, "size") > 0 && integer(object(report["tables"]), "tasks") == 1, packageJobError(detail.exported.data))

	forged := Object{"sha256": strings.Repeat("0", 64), "size": report["size"]}
	_, err = app.Client.PackageFile(ctx, detail.exported.id, forged)
	step("bytes that differ from the report are refused", err != nil && strings.Contains(err.Error(), "SHA-256"), "forged report accepted")
	detail.exported.Save(app, detail.packageName())
	pump()
	path := filepath.Join(saved, "合成项目包任务.bidpkg.zip")
	data, err := os.ReadFile(path)
	info, statErr := os.Stat(path)
	digest := sha256.Sum256(data)
	step("the saved package is the exported one, owner-only", err == nil && statErr == nil && info.Mode().Perm() == 0o600 && hex.EncodeToString(digest[:]) == str(report, "sha256") && app.Notice == "项目包长度与 SHA-256 已核验，保存完成", "saved file differs")

	app.Navigate("/org/tasks")
	pump()
	tasks := app.page.(*tasksPage)
	tasks.importPackage(app, path)
	waitPackage(t, app, updates, &tasks.imported)
	step("importing a project the gateway holds fails with the reason", tasks.imported.status() == "failed" && packageJobError(tasks.imported.data) == packageFailures["package_task_exists"], packageJobError(tasks.imported.data))

	server, pgBin := os.Getenv("BID_DESKTOP_TEST_SERVER"), os.Getenv("BID_DESKTOP_TEST_PG_BIN")
	personal := &Backend{DataDir: filepath.Join(root, "personal", "data"), LogDir: filepath.Join(root, "personal", "logs"), SocketDir: filepath.Join(root, "personal", "socket"), ServerDir: server, Python: filepath.Join(server, ".venv", "bin", "python"), PGBin: pgBin}
	t.Cleanup(personal.Stop)
	address, err := personal.Start()
	if err != nil {
		t.Fatal(err)
	}
	local, localUpdates := newApp()
	local.Client = NewAPIClient(address)
	if err := local.Client.Exchange(ctx, personal.install.AccessKey); err != nil {
		t.Fatal(err)
	}
	local.Workspace = &OpenWorkspace{Workspace: Workspace{ID: personalWorkspace, Kind: "personal", Name: "本机"}, Client: local.Client}
	app, updates = local, localUpdates
	app.Navigate("/org/tasks")
	pump()
	tasks = app.page.(*tasksPage)
	tasks.importPackage(app, path)
	waitPackage(t, app, updates, &tasks.imported)
	listed := false
	for _, item := range tasks.items {
		listed = listed || str(item, "id") == task && str(item, "name") == "合成项目包任务"
	}
	step("the personal installation lists the imported project", tasks.imported.status() == "succeeded" && str(tasks.imported.data, "task_id") == task && listed, packageJobError(tasks.imported.data))
	app.Navigate("/org/tasks/" + task)
	pump()
	detail, ok = app.page.(*taskDetailPage)
	step("the imported project opens with the export action", ok && str(detail.task, "tender_number") == "SYN-PKG-001" && detail.exportable(app), "task not opened")
}
