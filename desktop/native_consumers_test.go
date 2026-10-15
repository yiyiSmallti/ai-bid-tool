package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"
)

func awaitNativeApply(t *testing.T, updates <-chan func()) func() {
	t.Helper()
	select {
	case apply := <-updates:
		return apply
	case <-time.After(5 * time.Second):
		t.Fatal("native operation did not dispatch its completion")
		return nil
	}
}

func TestNativeNavigationDiscardsAlreadyFetchedResult(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(nativeConsumerResult(true, "task list", Object{"name": "previous page"}))
	}))
	defer server.Close()
	app := NewNativeApp()
	t.Cleanup(func() { app.cancel() })
	app.Client = NewAPIClient(server.URL)
	updates := make(chan func(), 1)
	app.Dispatch = func(apply func()) { updates <- apply }
	applied := false
	app.Request("GET", "/tasks", nil, func(Result) {
		applied = true
		app.Notice = "Previous page result"
	})
	late := awaitNativeApply(t, updates)
	app.Navigate("/login")
	late()
	if applied || app.Busy || app.Error != "" || app.Notice != "" || app.Route.Path != "/login" {
		t.Fatal("an already fetched result changed the newly navigated page")
	}
}

func TestNativeOriginalInspectionRequiresVerifiedSavedBytes(t *testing.T) {
	original := []byte("%PDF-1.4\nsynthetic original\n%%EOF\n")
	digest := sha256.Sum256(original)
	metadata := Object{"sha256": hex.EncodeToString(digest[:]), "size_bytes": len(original), "media_type": "application/pdf"}
	for _, scenario := range []string{"saved", "changed-bytes", "cancelled-save"} {
		t.Run(scenario, func(t *testing.T) {
			served := original
			if scenario == "changed-bytes" {
				served = bytes.Replace(original, []byte("original"), []byte("modified"), 1)
			}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/files/fixed/download-link":
					w.Header().Set("Content-Type", "application/json")
					_ = json.NewEncoder(w).Encode(nativeConsumerResult(true, "file download", Object{
						"url": "/files/fixed/download?signature=synthetic", "expires_in": 300, "file": metadata,
					}))
				case "/files/fixed/download":
					w.Header().Set("Content-Type", "application/pdf")
					_, _ = w.Write(served)
				default:
					t.Error("native download requested an unexpected path")
					w.WriteHeader(http.StatusNotFound)
				}
			}))
			defer server.Close()
			app := NewNativeApp()
			t.Cleanup(func() { app.cancel() })
			app.Client = NewAPIClient(server.URL)
			updates := make(chan func(), 1)
			app.Dispatch = func(apply func()) { updates <- apply }
			destination := filepath.Join(t.TempDir(), "original.pdf")
			previous := []byte("existing destination")
			if err := os.WriteFile(destination, previous, 0600); err != nil {
				t.Fatal(err)
			}
			app.SaveDestination = func(string) (string, error) {
				if scenario == "cancelled-save" {
					return "", nil
				}
				return destination, nil
			}
			inspected := false
			app.DownloadVerified("/files/fixed/download-link", "/files/fixed/download", "original.pdf", metadata, func() { inspected = true })
			awaitNativeApply(t, updates)()
			actual, err := os.ReadFile(destination)
			if err != nil {
				t.Fatal(err)
			}
			if scenario == "saved" {
				if !inspected || !bytes.Equal(actual, original) || app.Error != "" || app.Busy {
					t.Fatal("verified saved original did not complete inspection")
				}
			} else if inspected || !bytes.Equal(actual, previous) || app.Busy {
				t.Fatal("cancelled or changed download marked inspection complete or replaced the destination")
			}
			if scenario == "changed-bytes" && app.Error == "" {
				t.Fatal("changed original bytes did not surface a verification failure")
			}
		})
	}
}

func TestNativeTableSelectionStaysWithinSnapshot(t *testing.T) {
	rows := [][]string{{"记录甲", "第一版"}}
	opened := 0
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() {
			nativeTable(c, []string{"名称", "修订"}, rows, func(int) { opened++ })
		})
	}, 700, 400)
	if err := tt.Click("记录甲"); err != nil {
		t.Fatal(err)
	}
	tt.Key(0, ui.KeyEnter)
	if opened != 1 {
		t.Fatal("Enter did not open the selected record")
	}
	// Refresh can return another record with the same visible fields.
	rows = [][]string{{"记录甲", "第一版"}}
	tt.Frame()
	tt.Key(0, ui.KeyEnter)
	if opened != 1 {
		t.Fatal("selection opened a record from a replacement snapshot")
	}
	if err := tt.Click("记录甲"); err != nil {
		t.Fatal(err)
	}
	tt.Key(0, ui.KeyEnter)
	if opened != 2 {
		t.Fatal("new snapshot did not accept explicit selection")
	}
	rows = nil
	tt.Frame()
	tt.Key(0, ui.KeyEnter)
	if opened != 2 {
		t.Fatal("empty table retained an actionable selection")
	}
	// Separate records may have identical visible fields. MyGo's numeric row
	// keys keep both renderable without confusing them with content identity.
	rows = [][]string{{"记录甲", "第一版"}, {"记录甲", "第一版"}}
	tt.Frame()
	tt.Key(0, ui.KeyEnter)
	if opened != 2 {
		t.Fatal("identical displayed rows restored stale selection")
	}
}
