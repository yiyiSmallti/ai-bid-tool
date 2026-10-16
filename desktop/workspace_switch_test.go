package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io/fs"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// Failure matrix before implementation: switching to an enterprise workspace
// leaves the local API or PostgreSQL running, or switching back changes the
// database, the stored files or installation.json; a gateway is added over
// plain HTTP, as a personal installation, with an unreadable contract or with
// an untrusted certificate nobody confirmed; a pinned gateway accepts another
// certificate; sign-in sends an old session, or a session reaches anything but
// the gateway's origin, or lands outside the Keychain store; an expired
// session is kept; workspaces.json drifts from its documented shape.

type memorySecrets struct {
	mu    sync.Mutex
	items map[string]string
}

func (m *memorySecrets) Get(account string) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.items[account], nil
}

func (m *memorySecrets) Set(account, secret string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.items == nil {
		m.items = map[string]string{}
	}
	m.items[account] = secret
	return nil
}

func (m *memorySecrets) Delete(account string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.items, account)
	return nil
}

func resultJSON(w http.ResponseWriter, status int, command string, data Object) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(Object{
		"ok": status < 300, "command": command, "data": data, "items": []Object{},
		"warnings": []string{}, "duration_ms": 0,
		"cost": Object{"calls": 0, "llm_tokens": 0, "ocr_pages": 0, "search_requests": 0, "browser_units": 0, "unresolved_calls": 0},
	})
}

func resultItems(w http.ResponseWriter, command string, items []Object) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(Object{
		"ok": true, "command": command, "data": Object{}, "items": items,
		"warnings": []string{}, "duration_ms": 0,
		"cost": Object{"calls": 0, "llm_tokens": 0, "ocr_pages": 0, "search_requests": 0, "browser_units": 0, "unresolved_calls": 0},
	})
}

// fakeGateway answers the probe and session routes of an enterprise
// installation over TLS with its own self-signed certificate.
type fakeGateway struct {
	*httptest.Server
	mu       sync.Mutex
	mode     string
	contract string
	valid    string   // the session the gateway accepts
	seen     []string // Authorization headers, in order
	signIns  []Object
	// password is the member's; mustChange holds a new or reset member to
	// the password routes, as the gateway's auth gate does.
	password   string
	mustChange bool
}

func newFakeGateway(t *testing.T, mode, contract string) *fakeGateway {
	g := &fakeGateway{mode: mode, contract: contract, password: "synthetic-password"}
	g.Server = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		g.mu.Lock()
		defer g.mu.Unlock()
		g.seen = append(g.seen, r.Method+" "+r.URL.Path+" "+r.Header.Get("Authorization"))
		member := Object{"id": "11111111-2222-4333-8444-555555555555", "login": "synthetic.member", "display_name": "合成成员", "role": "member"}
		switch {
		case r.Method == "GET" && r.URL.Path == "/installation":
			resultJSON(w, 200, "installation show", Object{"mode": g.mode, "company_name": "合成测试公司", "server_version": "0.0.0-synthetic", "contract_version": g.contract})
		case r.Method == "POST" && r.URL.Path == "/auth/session":
			var body Object
			_ = json.NewDecoder(r.Body).Decode(&body)
			g.signIns = append(g.signIns, body)
			if str(body, "login") != "synthetic.member" || str(body, "password") != g.password {
				resultJSON(w, 401, "auth session create", Object{"error": Object{"code": "invalid_credentials", "message": "Invalid login or password"}})
				return
			}
			g.valid = "synthetic-session.1"
			resultJSON(w, 200, "auth session create", Object{"session": g.valid, "expires_in": 3600, "member": member, "must_change_password": g.mustChange})
		case r.Method == "GET" && r.URL.Path == "/auth/session":
			if g.valid == "" || r.Header.Get("Authorization") != "Bearer "+g.valid {
				resultJSON(w, 401, "auth session show", Object{"error": Object{"code": "unauthorized", "message": "Session expired"}})
				return
			}
			resultJSON(w, 200, "auth session show", Object{"mode": "enterprise", "actor_kind": "member", "scopes": []string{}, "expires_at": nil, "member": member, "must_change_password": g.mustChange})
		case g.valid == "" || r.Header.Get("Authorization") != "Bearer "+g.valid:
			resultJSON(w, 401, "session", Object{"error": Object{"code": "unauthorized", "message": "Session expired"}})
		case r.Method == "DELETE" && r.URL.Path == "/auth/session":
			g.valid = ""
			resultJSON(w, 200, "auth session delete", Object{"revoked": true})
		case r.Method == "POST" && r.URL.Path == "/account/password":
			var body Object
			_ = json.NewDecoder(r.Body).Decode(&body)
			if str(body, "current") != g.password {
				resultJSON(w, 401, "account password change", Object{"error": Object{"code": "invalid_login", "message": "The current password is wrong"}})
				return
			}
			g.password, g.mustChange = str(body, "new"), false
			resultJSON(w, 200, "account password change", Object{"changed": true, "revoked_sessions": 1})
		case g.mustChange:
			resultJSON(w, 403, "forbidden", Object{"error": Object{"code": "password_change_required", "message": "Change the password first"}})
		case r.Method == "GET" && r.URL.Path == "/account/sessions":
			resultItems(w, "account session list", []Object{
				{"id": "aaaaaaaa-2222-4333-8444-555555555555", "created_at": "2026-10-15T01:00:00Z", "expires_at": "2026-10-15T13:00:00Z", "last_seen_at": "2026-10-15T01:05:00Z", "client_label": "AI Bid 桌面客户端", "current": true},
			})
		case r.Method == "GET" && r.URL.Path == "/tasks":
			resultItems(w, "task list", []Object{})
		default:
			resultJSON(w, 404, "not found", Object{"error": Object{"code": "not_found", "message": "Not found"}})
		}
	}))
	t.Cleanup(g.Close)
	return g
}

func (g *fakeGateway) pin() *string {
	pin := certificateFingerprint(g.Certificate())
	return &pin
}

// localData is what switching must not change: a dump of the database, every
// stored file and the installation keys. The job queue's worker registry is
// left out: each launch's worker registers and deregisters itself there.
func localData(t *testing.T, b *Backend) map[string]string {
	t.Helper()
	dump := exec.Command(filepath.Join(b.PGBin, "pg_dump"), "-h", b.SocketDir, "-p", pgPort, "-U", "bid_owner", "-d", "bid", "--exclude-table-data=public.procrastinate_*")
	var out, stderr bytes.Buffer
	dump.Stdout, dump.Stderr = &out, &stderr
	if err := dump.Run(); err != nil {
		t.Fatalf("pg_dump: %v %s", err, stderr.String())
	}
	sum := func(data []byte) string { s := sha256.Sum256(data); return hex.EncodeToString(s[:]) }
	// pg_dump guards each dump with a fresh random \restrict key.
	var kept []string
	for _, line := range strings.Split(out.String(), "\n") {
		if !strings.HasPrefix(line, "\\restrict ") && !strings.HasPrefix(line, "\\unrestrict ") {
			kept = append(kept, line)
		}
	}
	data := map[string]string{"database": sum([]byte(strings.Join(kept, "\n")))}
	root := filepath.Join(b.DataDir, "files")
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return err
		}
		content, err := os.ReadFile(path)
		rel, _ := filepath.Rel(root, path)
		data["files/"+rel] = sum(content)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	installation, err := os.ReadFile(filepath.Join(b.DataDir, "installation.json"))
	if err != nil {
		t.Fatal(err)
	}
	data["installation.json"] = sum(installation)
	return data
}

func listening(addr string) bool {
	conn, err := net.Dial("tcp", addr)
	if err == nil {
		conn.Close()
	}
	return err == nil
}

// Scripted switches between the personal workspace and a synthetic gateway on
// a disposable data directory. BID_DESKTOP_TEST_KEYCHAIN=1 keeps the sessions
// in the login keychain instead of memory. The report lands in
// data/work/workspace-switch.
func TestWorkspaceSwitchLeavesLocalDataIntact(t *testing.T) {
	server, pgBin := os.Getenv("BID_DESKTOP_TEST_SERVER"), os.Getenv("BID_DESKTOP_TEST_PG_BIN")
	if server == "" || pgBin == "" {
		t.Skip("set BID_DESKTOP_TEST_SERVER and BID_DESKTOP_TEST_PG_BIN for isolated runtime acceptance")
	}
	ctx := context.Background()
	root, err := os.MkdirTemp("/tmp", "aibid-switch-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(root)
	report := Object{"rerun": "go test -run TestWorkspaceSwitchLeavesLocalDataIntact ."}
	var secrets SecretStore = &memorySecrets{}
	report["secrets"] = "memory"
	if os.Getenv("BID_DESKTOP_TEST_KEYCHAIN") == "1" {
		secrets = Keychain{}
		report["secrets"] = "keychain"
	}
	var latest *Backend
	runner := &WorkspaceRunner{
		Store:   WorkspaceStore{Path: filepath.Join(root, "data", "workspaces.json")},
		Secrets: secrets,
		NewBackend: func() (*Backend, error) {
			latest = &Backend{DataDir: filepath.Join(root, "data"), LogDir: filepath.Join(root, "logs"), SocketDir: filepath.Join(root, "socket"), ServerDir: server, Python: filepath.Join(server, ".venv", "bin", "python"), PGBin: pgBin}
			return latest, nil
		},
	}
	defer runner.Stop()

	local, err := runner.Open(ctx, personalWorkspace)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := local.Client.Request(ctx, "POST", "/tasks", Object{"name": "合成工作区切换任务"}); err != nil {
		t.Fatal(err)
	}
	// The model page saves a model on the local server and reads it back; the
	// owner the server reports with each model is part of the contract.
	app := NewNativeApp()
	app.Client, app.Workspace = local.Client, local
	updates := make(chan func(), 64)
	app.Dispatch = func(apply func()) { updates <- apply }
	app.ConfirmDialog = func(string, string) (bool, error) { return true, nil }
	app.Navigate("/org/settings/models")
	pumpRequests(t, app, updates)
	models, ok := app.page.(*modelSettingsPage)
	if !ok || app.Error != "" || models.settings == nil || !models.writable(app, "company") || !models.testable(app) {
		t.Fatalf("local model page: %q", app.Error)
	}
	models.startEdit(app, nil, "company")
	models.template = "zhipu"
	models.applyTemplate("zhipu")
	models.key = "synthetic-local-key-0003"
	models.save(app)
	pumpRequests(t, app, updates)
	app.Navigate("/org/settings/models")
	pumpRequests(t, app, updates)
	models, ok = app.page.(*modelSettingsPage)
	if !ok || app.Error != "" || len(models.owned("company")) != 1 || str(models.settings, "default_slot") != str(models.owned("company")[0], "slot") {
		t.Fatalf("local model not saved and read back: %q", app.Error)
	}
	report["local_model_page"] = "saved and read back"
	before := localData(t, latest)
	localAddr := strings.TrimPrefix(local.Client.BaseURL, "http://")
	localSession := local.Client.session

	// Adding a gateway: only an HTTPS enterprise installation with a readable
	// contract, and an untrusted certificate only by its confirmed fingerprint.
	gateway := newFakeGateway(t, "enterprise", "5.0")
	refusals := Object{}
	probeError := func(raw string, pin *string) error { _, err := ProbeGateway(ctx, raw, pin); return err }
	refusals["http"] = probeError(strings.Replace(gateway.URL, "https://", "http://", 1), nil) != nil
	refusals["path"] = probeError(gateway.URL+"/app", nil) != nil
	personal := newFakeGateway(t, "personal", "5.0")
	refusals["personal"] = probeError(personal.URL, personal.pin()) != nil
	unreadable := newFakeGateway(t, "enterprise", "five")
	refusals["unreadable_contract"] = probeError(unreadable.URL, unreadable.pin()) != nil
	var untrusted *UntrustedCertificateError
	if !errors.As(probeError(gateway.URL, nil), &untrusted) || untrusted.Fingerprint != *gateway.pin() {
		t.Fatalf("an untrusted certificate must report its fingerprint, got %v", untrusted)
	}
	refusals["untrusted_reports_fingerprint"] = true
	var mismatch *PinMismatchError
	// Every httptest server shares one certificate, so a synthetic pin stands for another.
	other := strings.Repeat("ab", 32)
	refusals["other_pin"] = errors.As(probeError(gateway.URL, &other), &mismatch)
	report["refusals"] = refusals
	for name, refused := range refusals {
		if refused != true {
			t.Fatalf("gateway probe accepted %s", name)
		}
	}
	info, err := ProbeGateway(ctx, gateway.URL, gateway.pin())
	if err != nil || info.CompanyName != "合成测试公司" || info.Origin != gateway.URL {
		t.Fatalf("pinned probe: %+v %v", info, err)
	}
	id, err := newWorkspaceID()
	if err != nil {
		t.Fatal(err)
	}
	if report["secrets"] == "keychain" {
		defer secrets.Delete(id)
	}
	list, err := runner.Store.Load()
	if err != nil {
		t.Fatal(err)
	}
	list.Workspaces = append(list.Workspaces, Workspace{ID: id, Kind: "enterprise", Name: info.CompanyName, URL: info.Origin, PinSHA256: gateway.pin()})
	if err := runner.Store.Save(list); err != nil {
		t.Fatal(err)
	}

	// Switching to the gateway stops the local stack.
	enterprise, err := runner.Open(ctx, id)
	if err != nil || enterprise.Account != nil {
		t.Fatalf("a new gateway workspace starts signed out: %v", err)
	}
	if listening(localAddr) || exists(filepath.Join(latest.DataDir, "pg", "postmaster.pid")) {
		t.Fatal("the local API or PostgreSQL kept running in an enterprise workspace")
	}
	if err := runner.SignIn(ctx, enterprise, "synthetic.member", "wrong-password"); err == nil {
		t.Fatal("a wrong password signed in")
	}
	if err := runner.SignIn(ctx, enterprise, "synthetic.member", "synthetic-password"); err != nil || enterprise.Account == nil {
		t.Fatalf("sign-in: %v", err)
	}
	if stored, _ := secrets.Get(id); stored != "synthetic-session.1" {
		t.Fatal("the session was not kept in the secret store")
	}
	raw, err := os.ReadFile(runner.Store.Path)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte("synthetic-session")) || bytes.Contains(raw, []byte("synthetic-password")) {
		t.Fatal("workspaces.json holds a session or password")
	}

	// Switching back starts the local stack on unchanged data.
	again, err := runner.Open(ctx, personalWorkspace)
	if err != nil {
		t.Fatal(err)
	}
	after := localData(t, latest)
	report["local_data"] = Object{"before": before, "after": after}
	if len(before) != len(after) {
		t.Fatalf("local data changed: %v != %v", before, after)
	}
	for key, value := range before {
		if after[key] != value {
			t.Fatalf("switching changed %s", key)
		}
	}
	tasks, err := again.Client.Request(ctx, "GET", "/tasks", nil)
	if err != nil || len(tasks.Items) != 1 {
		t.Fatalf("the local task is not visible after switching back: %v", err)
	}

	// A stored session resumes; an expired one is forgotten.
	resumed, err := runner.Open(ctx, id)
	if err != nil || resumed.Account == nil {
		t.Fatalf("the stored session did not resume: %v", err)
	}
	gateway.mu.Lock()
	gateway.valid = ""
	gateway.mu.Unlock()
	if _, err := runner.Open(ctx, personalWorkspace); err != nil {
		t.Fatal(err)
	}
	expired, err := runner.Open(ctx, id)
	if err != nil || expired.Account != nil {
		t.Fatalf("an expired session stayed signed in: %v", err)
	}
	if stored, _ := secrets.Get(id); stored != "" {
		t.Fatal("an expired session stayed in the secret store")
	}

	// Sign-in carries no session; nothing else ever carries the local one.
	gateway.mu.Lock()
	seen := append([]string(nil), gateway.seen...)
	gateway.mu.Unlock()
	report["gateway_requests"] = seen
	for _, line := range seen {
		if strings.HasPrefix(line, "POST /auth/session ") && !strings.HasSuffix(line, "/auth/session ") {
			t.Fatalf("sign-in sent a session: %s", line)
		}
		if strings.Contains(line, localSession) {
			t.Fatal("the local session reached the gateway")
		}
	}

	// workspaces.json keeps its documented shape, owner-only.
	stat, err := os.Stat(runner.Store.Path)
	if err != nil || stat.Mode().Perm() != 0o600 {
		t.Fatal("workspaces.json must be owner-only")
	}
	raw, err = os.ReadFile(runner.Store.Path)
	if err != nil {
		t.Fatal(err)
	}
	var shape Object
	if err := json.Unmarshal(raw, &shape); err != nil {
		t.Fatal(err)
	}
	report["workspaces_json"] = shape
	want := Object{"current": id, "workspaces": []any{
		Object{"id": "personal", "kind": "personal", "name": "本机"},
		Object{"id": id, "kind": "enterprise", "name": "合成测试公司", "url": gateway.URL, "pin_sha256": *gateway.pin()},
	}}
	if got, _ := json.Marshal(shape); string(got) != string(must(json.Marshal(want))) {
		t.Fatalf("workspaces.json shape: %s", got)
	}

	dir := filepath.Join("..", "data", "work", "workspace-switch")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.MarshalIndent(report, "", "  ")
	if err := os.WriteFile(filepath.Join(dir, "report.json"), encoded, 0o644); err != nil {
		t.Fatal(err)
	}
}

func must(data []byte, err error) []byte {
	if err != nil {
		panic(err)
	}
	return data
}
