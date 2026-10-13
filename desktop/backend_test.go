package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"net"
	"os"
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
			b := &Backend{DataDir: filepath.Join(root, "data"), LogDir: filepath.Join(root, "logs"), SocketDir: filepath.Join(root, "socket"), ServerDir: server, PGBin: pgBin}
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
			restarted := &Backend{DataDir: b.DataDir, LogDir: b.LogDir, SocketDir: b.SocketDir, ServerDir: server, PGBin: pgBin}
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
