package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadInstallationStagesKeys(t *testing.T) {
	b := &Backend{DataDir: t.TempDir()}
	if err := b.loadInstallation(); err != nil {
		t.Fatal(err)
	}
	if b.install.EncryptionKey == "" || b.install.TokenKey == "" || b.install.SecretsKey == "" || len(b.install.AccessKey) < 32 {
		t.Fatal("installation keys were not staged")
	}
	if _, err := os.Stat(filepath.Join(b.DataDir, "installation.json")); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("loading a fresh installation must not persist keys")
	}
}

func TestLoadInstallationPreservesExistingBytes(t *testing.T) {
	b := &Backend{DataDir: t.TempDir()}
	original := []byte("{\n  \"encryption_key\": \"existing-data-key\", \"token_key\": \"existing-token-key\", \"secrets_key\": \"existing-secret-key\", \"removed_field\": \"retain until migration succeeds\"\n}\n")
	path := filepath.Join(b.DataDir, "installation.json")
	if err := os.WriteFile(path, original, 0600); err != nil {
		t.Fatal(err)
	}
	if err := b.loadInstallation(); err != nil {
		t.Fatal(err)
	}
	if b.install.EncryptionKey != "existing-data-key" || b.install.TokenKey != "existing-token-key" || b.install.SecretsKey != "existing-secret-key" || len(b.install.AccessKey) < 32 {
		t.Fatal("existing keys were not preserved while staging access")
	}
	actual, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(original, actual) {
		t.Fatal("loading an existing installation changed its bytes")
	}
}

func TestConsolePortStagesChangedPort(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	_, occupied, err := net.SplitHostPort(listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	b := &Backend{install: &installation{Port: occupied}}
	selected, err := b.consolePort()
	if err != nil || selected == occupied || !b.installationDirty {
		t.Fatal("a changed console port must stage an installation update")
	}
}

// Opt in with explicit external runtimes. Every cluster, socket, key and log is
// created under a new short temporary root; MyGo and default user paths are unused.
func TestStartInstallationPersistence(t *testing.T) {
	server, pgBin := os.Getenv("BID_DESKTOP_TEST_SERVER"), os.Getenv("BID_DESKTOP_TEST_PG_BIN")
	if server == "" || pgBin == "" {
		t.Skip("set BID_DESKTOP_TEST_SERVER and BID_DESKTOP_TEST_PG_BIN for isolated runtime acceptance")
	}
	for _, legacy := range []bool{true, false} {
		name := "compatible"
		if legacy {
			name = "incompatible_legacy"
		}
		t.Run(name, func(t *testing.T) {
			root, err := os.MkdirTemp("/tmp", "aibid-backend-")
			if err != nil {
				t.Fatal(err)
			}
			defer os.RemoveAll(root)
			b := &Backend{DataDir: filepath.Join(root, "data"), LogDir: filepath.Join(root, "logs"), SocketDir: filepath.Join(root, "socket"), ServerDir: server, Python: filepath.Join(server, ".venv", "bin", "python"), PGBin: pgBin}
			defer b.Stop()
			for _, dir := range []string{b.DataDir, b.LogDir, b.SocketDir} {
				if err := os.MkdirAll(dir, 0700); err != nil {
					t.Fatal(err)
				}
			}
			port, err := freePort()
			if err != nil {
				t.Fatal(err)
			}
			original, err := json.Marshal(map[string]string{"encryption_key": fernetKey(), "token_key": fernetKey(), "secrets_key": fernetKey(), "port": port, "removed_field": "synthetic legacy metadata"})
			if err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(b.DataDir, "installation.json")
			if err := os.WriteFile(path, original, 0600); err != nil {
				t.Fatal(err)
			}
			if legacy {
				if err := b.startPostgres(); err != nil {
					t.Fatal(err)
				}
				if err := b.pg("psql", "-h", b.SocketDir, "-p", pgPort, "-U", "bid_owner", "-d", "bid", "-v", "ON_ERROR_STOP=1", "-c", "CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY); INSERT INTO alembic_version VALUES ('0069')"); err != nil {
					t.Fatal(err)
				}
			}
			_, startErr := b.Start()
			actual, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if legacy {
				if startErr == nil || !strings.Contains(startErr.Error(), "database migration:") {
					t.Fatalf("expected migration failure, got %v", startErr)
				}
				if !bytes.Equal(original, actual) {
					t.Fatal("incompatible database changed preexisting installation bytes")
				}
				if len(b.processes) != 0 {
					t.Fatal("migration failure launched API or worker")
				}
				if backups := listBackups(t, b); len(backups) != 0 {
					t.Fatalf("a database at an unknown revision was backed up: %v", backups)
				}
				return
			}
			if startErr != nil {
				t.Fatal(startErr)
			}
			var saved installation
			if err := json.Unmarshal(actual, &saved); err != nil {
				t.Fatal(err)
			}
			if saved.AccessKey != b.install.AccessKey || len(saved.AccessKey) < 32 || saved.Port != port {
				t.Fatal("compatible installation did not persist its staged key and reused port")
			}
			info, err := os.Stat(path)
			if err != nil || info.Mode().Perm() != 0600 {
				t.Fatal("installation must remain owner-only")
			}
			b.Stop()
			restarted := &Backend{DataDir: b.DataDir, LogDir: b.LogDir, SocketDir: b.SocketDir, ServerDir: server, Python: filepath.Join(server, ".venv", "bin", "python"), PGBin: pgBin}
			defer restarted.Stop()
			if _, err := restarted.Start(); err != nil {
				t.Fatal(err)
			}
			after, err := os.ReadFile(path)
			if err != nil || !bytes.Equal(actual, after) {
				t.Fatal("unchanged installation was rewritten on restart")
			}
			afterInfo, err := os.Stat(path)
			if err != nil || !info.ModTime().Equal(afterInfo.ModTime()) {
				t.Fatal("unchanged installation modification time changed on restart")
			}
		})
	}
}

// An updated app backs up the database before migrating it. Revisions added to
// a copy of the server's migrations stand in for later app versions; the
// Python environment, which imports the server from its own installation, is
// the real one. The roots are disposable, as above.
func TestStartBacksUpBeforeMigration(t *testing.T) {
	server, pgBin := os.Getenv("BID_DESKTOP_TEST_SERVER"), os.Getenv("BID_DESKTOP_TEST_PG_BIN")
	if server == "" || pgBin == "" {
		t.Skip("set BID_DESKTOP_TEST_SERVER and BID_DESKTOP_TEST_PG_BIN for isolated runtime acceptance")
	}
	root, err := os.MkdirTemp("/tmp", "aibid-backup-")
	if err != nil {
		t.Fatal(err)
	}
	// Cleanups run last first: every backend below stops its cluster before
	// the root is removed.
	t.Cleanup(func() { os.RemoveAll(root) })
	serverDir := filepath.Join(root, "server")
	versions := filepath.Join(serverDir, "server", "migrations", "versions")
	if err := os.MkdirAll(filepath.Join(serverDir, "server"), 0o700); err != nil {
		t.Fatal(err)
	}
	for _, copy := range [][2]string{
		{filepath.Join(server, "alembic.ini"), serverDir},
		{filepath.Join(server, "server", "migrations"), filepath.Join(serverDir, "server")},
	} {
		if out, err := exec.Command("cp", "-R", copy[0], copy[1]).CombinedOutput(); err != nil {
			t.Fatalf("%v: %s", err, out)
		}
	}
	setRevision := func(revision, down, statement string) {
		writeFile(t, filepath.Join(versions, revision+"_probe.py"), fmt.Sprintf(
			"from alembic import op\n\nrevision = %q\ndown_revision = %q\n\n\ndef upgrade():\n    op.execute(%q)\n",
			revision, down, statement))
	}
	backend := func() *Backend {
		b := &Backend{DataDir: filepath.Join(root, "data"), LogDir: filepath.Join(root, "logs"),
			SocketDir: filepath.Join(root, "socket"), ServerDir: serverDir,
			Python: filepath.Join(server, ".venv", "bin", "python"), PGBin: pgBin}
		t.Cleanup(b.Stop)
		return b
	}
	query := func(b *Backend, sql string) string {
		t.Helper()
		out, err := b.pgOutput("psql", "-h", b.SocketDir, "-p", pgPort, "-U", "bid_owner", "-d", "bid",
			"-v", "ON_ERROR_STOP=1", "-tAc", sql)
		if err != nil {
			t.Fatal(err)
		}
		return strings.TrimSpace(out)
	}
	// start launches the app version the migrations describe and leaves it running.
	start := func() *Backend {
		t.Helper()
		b := backend()
		if _, err := b.Start(); err != nil {
			t.Fatal(err)
		}
		return b
	}
	expectBackups := func(b *Backend, revisions ...string) []string {
		t.Helper()
		backups := listBackups(t, b)
		if len(backups) != len(revisions) {
			t.Fatalf("backups %v, want one at each of %v", backups, revisions)
		}
		for i, revision := range revisions {
			if !strings.HasSuffix(backups[i], "-"+revision+".dump") {
				t.Fatalf("backups %v, want one at each of %v", backups, revisions)
			}
		}
		return backups
	}

	// A new installation has nothing to back up, and owns its data directory.
	b := start()
	query(b, "CREATE TABLE desktop_backup_marker (note text); INSERT INTO desktop_backup_marker VALUES ('kept')")
	expectBackups(b)
	second := backend()
	if _, err := second.Start(); err == nil || !strings.Contains(err.Error(), "another AI Bid process") {
		t.Fatalf("a second backend on the same data directory started: %v", err)
	}
	second.Stop()
	if resp, err := http.Get(b.url + "/health"); err != nil || resp.StatusCode != http.StatusOK {
		t.Fatalf("the refused backend disturbed the running one: %v", err)
	} else {
		resp.Body.Close()
	}
	b.Stop()

	// A failing migration leaves the database at its revision; retrying it
	// replaces the backup taken at that revision.
	setRevision("0002", "0001", "CREATE TABLE desktop_probe_0002 (id int); SELECT 1/0")
	var failed string
	for attempt := 0; attempt < 2; attempt++ {
		b = backend()
		_, err := b.Start()
		if err == nil || !strings.Contains(err.Error(), "database migration:") {
			t.Fatalf("expected the probe migration to fail, got %v", err)
		}
		if got := query(b, "SELECT version_num FROM alembic_version"); got != "0001" {
			t.Fatalf("failed migration left revision %q", got)
		}
		if got := query(b, "SELECT to_regclass('desktop_probe_0002') IS NULL"); got != "t" {
			t.Fatal("failed migration left its table")
		}
		backups := expectBackups(b, "0001")
		if backups[0] == failed {
			t.Fatal("the retry did not replace the backup")
		}
		failed = backups[0]
		b.Stop()
	}

	// Each update keeps its own backup and the one before it.
	setRevision("0002", "0001", "CREATE TABLE desktop_probe_0002 (id int)")
	b = start()
	expectBackups(b, "0001")
	b.Stop()
	setRevision("0003", "0002", "CREATE TABLE desktop_probe_0003 (id int)")
	b = start()
	expectBackups(b, "0001", "0002")
	b.Stop()
	setRevision("0004", "0003", "CREATE TABLE desktop_probe_0004 (id int)")
	b = start()
	backups := expectBackups(b, "0002", "0003")
	b.Stop()

	// Going back to the previous version: with the app quit, recreate the
	// database from the backup taken before the last update, as the backup
	// how-to does, then launch that version.
	b = backend()
	if err := b.pg("pg_ctl", "-D", b.clusterDir(), "-l", filepath.Join(b.LogDir, "restore.log"), "-w", "start"); err != nil {
		t.Fatal(err)
	}
	restoreErr := b.pg("dropdb", "-h", b.SocketDir, "-p", pgPort, "-U", "bid_owner", "bid")
	if restoreErr == nil {
		restoreErr = b.pg("pg_restore", "-h", b.SocketDir, "-p", pgPort, "-U", "bid_owner", "--create",
			"-d", "postgres", filepath.Join(b.backupDir(), backups[1]))
	}
	if err := b.pg("pg_ctl", "-D", b.clusterDir(), "-m", "fast", "-w", "stop"); err != nil {
		t.Fatal(err)
	}
	if restoreErr != nil {
		t.Fatal(restoreErr)
	}
	if err := os.Remove(filepath.Join(versions, "0004_probe.py")); err != nil {
		t.Fatal(err)
	}
	b = start()
	if got := query(b, "SELECT version_num FROM alembic_version"); got != "0003" {
		t.Fatalf("restored database is at revision %q", got)
	}
	if got := query(b, "SELECT note FROM desktop_backup_marker"); got != "kept" {
		t.Fatalf("restored database lost its rows: %q", got)
	}
	if got := query(b, "SELECT to_regclass('desktop_probe_0004') IS NULL"); got != "t" {
		t.Fatal("restored database has the later revision's table")
	}
	if got := listBackups(t, b); len(got) != 2 || got[0] != backups[0] || got[1] != backups[1] {
		t.Fatalf("starting the restored version changed the backups: %v", got)
	}
}

func listBackups(t *testing.T, b *Backend) []string {
	t.Helper()
	entries, err := os.ReadDir(b.backupDir())
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}
