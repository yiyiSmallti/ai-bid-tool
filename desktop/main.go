package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

// paths are what the backend runs, from the app's resources unless
// BID_DESKTOP_SERVER and BID_DESKTOP_PG_BIN select a development checkout and
// PostgreSQL binaries.
type paths struct {
	server, python, pgBin string
	renderer, ocrData     string // empty when the resources lack them
}

func locate() (paths, error) {
	resources, err := mygo.App.Path(mygo.PathResources)
	if err != nil {
		return paths{}, err
	}
	p := paths{
		server: filepath.Join(resources, "server"),
		python: filepath.Join(resources, "python", "bin", "python3"),
		pgBin:  filepath.Join(resources, "pgsql", "bin"),
	}
	if v := os.Getenv("BID_DESKTOP_SERVER"); v != "" {
		p.server, p.python = v, filepath.Join(v, ".venv", "bin", "python")
	}
	if v := os.Getenv("BID_DESKTOP_PG_BIN"); v != "" {
		p.pgBin = v
	}
	for _, required := range []string{filepath.Join(p.server, "alembic.ini"), p.python} {
		if _, err := os.Stat(required); err != nil {
			return paths{}, fmt.Errorf("server not found: %w", err)
		}
	}
	if _, err := os.Stat(filepath.Join(p.pgBin, "pg_ctl")); err != nil {
		return paths{}, fmt.Errorf("PostgreSQL not found at %s: %w", p.pgBin, err)
	}
	// A development checkout without these falls back to its own renderer
	// build and runs without OCR data.
	if v := filepath.Join(resources, "bin", "bid-screenshot-renderer"); exists(v) {
		p.renderer = v
	}
	if v := filepath.Join(resources, "tessdata"); exists(v) {
		p.ocrData = v
	}
	return p, nil
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// socketDir is where the cluster's Unix socket lives. macOS limits socket
// paths to 103 bytes, and the database URLs carry it unquoted, so it avoids
// the user data directory, whose name has a space.
func socketDir() (string, error) {
	cache, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	dir := filepath.Join(cache, "io.github.yiyismallti.aibid", "pg")
	if strings.ContainsAny(dir, " %?&#") || len(dir) > 80 {
		return "", errors.New("unsupported socket directory: " + dir)
	}
	return dir, nil
}

func main() {
	if !mygo.App.RequestSingleInstanceLock() {
		return // the running instance owns the database
	}
	runner := &WorkspaceRunner{Secrets: Keychain{}, NewBackend: newBackend}
	app := NewNativeApp()
	app.Runner = runner

	mygo.App.WhenReady(func() {
		win := mygo.NewWindow(mygo.WindowOptions{
			Title: "AI Bid",
			// The sidebar and page header draw the title bar and drag the window.
			TitleBarStyle: mygo.TitleBarHidden,
			Width:         1360,
			Height:        880,
			MinWidth:      960,
			MinHeight:     640,
			StateKey:      "main",
			Content:       ui.View(app.View),
		})
		app.bindWindow(win)
		mygo.App.OnSecondInstance(func([]string, string) { win.Focus() })

		go func() {
			data, err := mygo.App.Path(mygo.PathUserData)
			if err == nil {
				err = os.MkdirAll(data, 0o700)
			}
			if err != nil {
				mygo.Dialog.Error("AI Bid 无法启动", err.Error())
				mygo.App.Quit()
				return
			}
			runner.Store = WorkspaceStore{Path: filepath.Join(data, "workspaces.json")}
			list, err := runner.Store.Load()
			if err != nil {
				mygo.Dialog.Error("AI Bid 无法读取工作区", err.Error())
				mygo.App.Quit()
				return
			}
			opened, err := runner.Open(context.Background(), list.Current)
			if opened == nil {
				runner.Stop()
				mygo.Dialog.Error("AI Bid 无法启动", err.Error())
				mygo.App.Quit()
				return
			}
			win.Update(func() { app.opened(opened, err) })
		}()
	})
	mygo.App.OnWillQuit(func(*mygo.QuitEvent) { runner.Stop() })

	if err := mygo.App.Run(); err != nil {
		log.Fatal(err)
	}
}

// newBackend prepares the local stack from the app's resources and user paths.
func newBackend() (*Backend, error) {
	p, err := locate()
	if err != nil {
		return nil, err
	}
	data, err := mygo.App.Path(mygo.PathUserData)
	if err != nil {
		return nil, err
	}
	logs, err := mygo.App.Path(mygo.PathLogs)
	if err != nil {
		return nil, err
	}
	socket, err := socketDir()
	if err != nil {
		return nil, err
	}
	return &Backend{DataDir: data, LogDir: logs, SocketDir: socket, ServerDir: p.server, Python: p.python, PGBin: p.pgBin, Renderer: p.renderer, OCRData: p.ocrData}, nil
}
