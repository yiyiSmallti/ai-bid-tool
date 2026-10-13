package main

import (
	"errors"
	"fmt"
	"log"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/egoist/mygo"
)

// locate finds the Python server and the PostgreSQL binaries: the copies
// shipped in the app's resources, or, until those are bundled, the paths in
// BID_DESKTOP_SERVER and BID_DESKTOP_PG_BIN.
func locate() (server, pgBin string, err error) {
	resources, err := mygo.App.Path(mygo.PathResources)
	if err != nil {
		return "", "", err
	}
	server = filepath.Join(resources, "server")
	pgBin = filepath.Join(resources, "pgsql", "bin")
	if v := os.Getenv("BID_DESKTOP_SERVER"); v != "" {
		server = v
	}
	if v := os.Getenv("BID_DESKTOP_PG_BIN"); v != "" {
		pgBin = v
	}
	if _, err := os.Stat(filepath.Join(server, "alembic.ini")); err != nil {
		return "", "", fmt.Errorf("server not found at %s: %w", server, err)
	}
	if _, err := os.Stat(filepath.Join(pgBin, "pg_ctl")); err != nil {
		return "", "", fmt.Errorf("PostgreSQL not found at %s: %w", pgBin, err)
	}
	return server, pgBin, nil
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
	backend := &Backend{}

	mygo.App.WhenReady(func() {
		win := mygo.NewWindow(mygo.WindowOptions{
			Title:     "AI Bid",
			Width:     1360,
			Height:    880,
			MinWidth:  960,
			MinHeight: 640,
			StateKey:  "main",
			URL:       "/", // the startup page until the console is up
		})
		mygo.App.OnSecondInstance(func([]string, string) { win.Focus() })

		go func() {
			baseURL, err := start(backend)
			if err != nil {
				backend.Stop()
				mygo.Dialog.Error("AI Bid 无法启动", err.Error())
				mygo.App.Quit()
				return
			}
			// Console pages that open a new tab stay in the app, where the
			// session lives; other sites go to the default browser.
			win.Page().SetWindowOpenHandler(func(req mygo.WindowOpenRequest) *mygo.WindowOptions {
				if strings.HasPrefix(req.URL, baseURL+"/") {
					return &mygo.WindowOptions{Title: "AI Bid", Width: 1200, Height: 800, URL: req.URL}
				}
				externalURL, err := url.Parse(req.URL)
				if err != nil {
					return nil
				}
				// The access key is only exchanged inside the console.
				externalURL.Fragment, externalURL.RawFragment = "", ""
				_ = mygo.Shell.OpenExternal(externalURL.String())
				return nil
			})
			if err := win.Page().LoadURL(baseURL + "/app/#key=" + url.QueryEscape(backend.install.AccessKey)); err != nil {
				log.Print(err)
			}
		}()
	})
	mygo.App.OnWillQuit(func(*mygo.QuitEvent) { backend.Stop() })

	if err := mygo.App.Run(); err != nil {
		log.Fatal(err)
	}
}

func start(b *Backend) (string, error) {
	server, pgBin, err := locate()
	if err != nil {
		return "", err
	}
	data, err := mygo.App.Path(mygo.PathUserData)
	if err != nil {
		return "", err
	}
	logs, err := mygo.App.Path(mygo.PathLogs)
	if err != nil {
		return "", err
	}
	socket, err := socketDir()
	if err != nil {
		return "", err
	}
	b.DataDir, b.LogDir, b.SocketDir, b.ServerDir, b.PGBin = data, logs, socket, server, pgBin
	return b.Start()
}
