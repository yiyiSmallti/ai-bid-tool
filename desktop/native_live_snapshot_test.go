//go:build uilive

package main

import (
	"context"
	"encoding/json"
	"image/png"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"
)

// Renders console pages against the running local client's API into PNG
// files for visual review. Requests other than reads, list queries and the
// session exchange are refused, so rendering cannot change the installation.
//
//	BID_UI_LIVE_API=http://127.0.0.1:59102 BID_UI_SNAP_DIR=../data/work/ui-snap/live \
//	  go test -tags uilive -run TestNativeLiveSnapshots .
func TestNativeLiveSnapshots(t *testing.T) {
	api, dir := os.Getenv("BID_UI_LIVE_API"), os.Getenv("BID_UI_SNAP_DIR")
	if api == "" || dir == "" {
		t.Skip("BID_UI_LIVE_API and BID_UI_SNAP_DIR are required")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	home, _ := os.UserHomeDir()
	data, err := os.ReadFile(filepath.Join(home, "Library", "Application Support", "AI Bid", "installation.json"))
	if err != nil {
		t.Fatal(err)
	}
	var install installation
	if err := json.Unmarshal(data, &install); err != nil {
		t.Fatal(err)
	}
	target, _ := url.Parse(api)
	proxy := httputil.NewSingleHostReverseProxy(target)
	var refused []string
	guard := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" || (r.Method == "POST" && (r.URL.Path == "/auth/session" || strings.HasSuffix(r.URL.Path, "/query"))) {
			proxy.ServeHTTP(w, r)
			return
		}
		refused = append(refused, r.Method+" "+r.URL.Path)
		http.Error(w, "refused by snapshot guard", http.StatusForbidden)
	})
	server := &http.Server{Handler: guard}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go server.Serve(listener)
	defer server.Close()
	base := "http://" + listener.Addr().String()

	client := NewAPIClient(base)
	if err := client.Exchange(context.Background(), install.AccessKey); err != nil {
		t.Fatal(err)
	}
	tasks, err := client.Request(context.Background(), "GET", "/tasks", nil)
	if err != nil {
		t.Fatal(err)
	}
	routes := map[string]string{"tasks": "/org/tasks"}
	for _, group := range nativeShellGroups {
		for _, item := range group.items {
			routes[strings.ReplaceAll(strings.Trim(item.path, "/"), "/", "-")] = item.path
		}
	}
	only := os.Getenv("BID_UI_LIVE_TASK")
	for _, task := range tasks.Items {
		id := str(task, "id")
		if only != "" && id != only {
			continue
		}
		for _, group := range nativeTaskNavigation {
			for _, item := range group.items {
				name := "task-" + id[:8] + strings.ReplaceAll(item.path, "/", "-")
				routes[name] = "/org/tasks/" + id + item.path
			}
		}
		if only == "" {
			break // the first task covers the task pages
		}
	}
	if filter := os.Getenv("BID_UI_SNAP_ONLY"); filter != "" {
		for name := range routes {
			if !strings.Contains(name, filter) {
				delete(routes, name)
			}
		}
	}
	for name, route := range routes {
		app := NewNativeApp()
		app.Client = client
		updates := make(chan func(), 64)
		app.Dispatch = func(apply func()) { updates <- apply }
		tt := ui.NewTester(app.View, 1360, 2400)
		tt.SetScale(1.5)
		tt.SetTitleBar(ui.TitleBar{Height: 28, Left: 78})
		app.Navigate(route)
		for idle := 0; idle < 8; {
			select {
			case apply := <-updates:
				apply()
				idle = 0
			case <-time.After(80 * time.Millisecond):
				idle++
			}
			tt.Frame()
		}
		tt.Frame()
		file, err := os.Create(filepath.Join(dir, name+".png"))
		if err != nil {
			t.Fatal(err)
		}
		if err := png.Encode(file, tt.Image()); err != nil {
			t.Fatal(err)
		}
		file.Close()
		if app.Error != "" {
			t.Logf("%s: error %q", name, app.Error)
		}
		app.cancel()
	}
	if len(refused) > 0 {
		t.Logf("refused writes: %v", refused)
	}
}
