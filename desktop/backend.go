package main

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
)

var errStopped = errors.New("the app is quitting")

// pgPort only names the Unix socket: the cluster never listens on TCP.
const pgPort = "55440"

// installation holds the generated keys. It lives in the user data directory
// with mode 0600, next to the database it unlocks.
type installation struct {
	EncryptionKey string `json:"encryption_key"`
	TokenKey      string `json:"token_key"`
	SecretsKey    string `json:"secrets_key"`
	AccessKey     string `json:"access_key"`
	// Port stays the same across launches: the console keeps its session in
	// localStorage, which belongs to the origin and so to the port.
	Port string `json:"port"`
}

// Backend runs the stack the console needs: a PostgreSQL cluster in the user
// data directory, reachable only through a private Unix socket, and the API and
// worker processes of the Python server.
type Backend struct {
	DataDir   string // user data: cluster, files, installation.json
	LogDir    string
	SocketDir string // must be short and free of spaces; see socketDir
	ServerDir string // checkout or bundle holding alembic.ini and .venv
	PGBin     string // initdb, pg_ctl, psql, createdb

	install           *installation
	installationDirty bool
	url               string

	// mu guards what Stop tears down, since a quit can arrive mid-startup.
	mu        sync.Mutex
	stopped   bool
	processes []*process
	pgRunning bool
}

// process is a server process; done closes once it has exited.
type process struct {
	cmd  *exec.Cmd
	done chan struct{}
}

// Start brings the stack up and returns the console's base URL.
func (b *Backend) Start() (string, error) {
	for _, dir := range []string{b.DataDir, b.LogDir, b.SocketDir, filepath.Join(b.DataDir, "files")} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return "", err
		}
	}
	if err := b.loadInstallation(); err != nil {
		return "", fmt.Errorf("installation keys: %w", err)
	}
	if err := b.startPostgres(); err != nil {
		return "", fmt.Errorf("PostgreSQL: %w", err)
	}
	// init-db is idempotent: it creates the runtime role once and applies new
	// migrations, so an updated app upgrades the database on its next launch.
	if err := b.runPython("admin-init-db.log", b.ownerEnv(), "-m", "app.admin", "init-db"); err != nil {
		return "", fmt.Errorf("database migration: %w", err)
	}
	port, err := b.consolePort()
	if err != nil {
		return "", err
	}
	// Keep existing installation bytes intact until this database is compatible.
	if err := b.saveInstallation(); err != nil {
		return "", fmt.Errorf("installation keys: %w", err)
	}
	b.url = "http://127.0.0.1:" + port
	python := filepath.Join(b.ServerDir, ".venv", "bin", "python")
	api, err := b.spawn("api.log", python, "-m", "uvicorn", "app.api.main:create_app", "--factory",
		"--host", "127.0.0.1", "--port", port, "--no-access-log")
	if err != nil {
		return "", err
	}
	if _, err := b.spawn("worker.log", python, "-m", "app.jobs.worker"); err != nil {
		return "", err
	}
	if err := b.waitHealthy(api, 90*time.Second); err != nil {
		return "", err
	}
	return b.url, nil
}

// Stop ends the API and worker, then shuts the cluster down cleanly.
func (b *Backend) Stop() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.stopped = true
	for _, p := range b.processes {
		_ = p.cmd.Process.Signal(syscall.SIGTERM)
	}
	deadline := time.After(10 * time.Second)
	for _, p := range b.processes {
		select {
		case <-p.done:
		case <-deadline:
			_ = p.cmd.Process.Kill()
			<-p.done
		}
	}
	b.processes = nil
	if b.pgRunning {
		_ = b.pg("pg_ctl", "-D", b.clusterDir(), "-m", "fast", "-w", "stop")
		b.pgRunning = false
	}
}

func (b *Backend) clusterDir() string { return filepath.Join(b.DataDir, "pg") }

func (b *Backend) loadInstallation() error {
	b.installationDirty = false
	path := filepath.Join(b.DataDir, "installation.json")
	data, err := os.ReadFile(path)
	if err == nil {
		b.install = &installation{}
		if err := json.Unmarshal(data, b.install); err != nil {
			return err
		}
		if b.install.AccessKey != "" {
			return nil
		}
	} else {
		if !errors.Is(err, os.ErrNotExist) {
			return err
		}
		b.install = &installation{
			EncryptionKey: fernetKey(),
			TokenKey:      fernetKey(),
			SecretsKey:    fernetKey(),
		}
	}
	b.install.AccessKey = base64.RawURLEncoding.EncodeToString(randomBytes(32))
	b.installationDirty = true
	return nil
}

func (b *Backend) saveInstallation() error {
	if !b.installationDirty {
		return nil
	}
	data, err := json.MarshalIndent(b.install, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(b.DataDir, "installation.json")
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	b.installationDirty = false
	return nil
}

func (b *Backend) startPostgres() error {
	cluster := b.clusterDir()
	if _, err := os.Stat(filepath.Join(cluster, "PG_VERSION")); errors.Is(err, os.ErrNotExist) {
		if err := b.pg("initdb", "-D", cluster, "-U", "bid_owner", "-E", "UTF8", "--locale=C",
			"--auth-local=trust", "--auth-host=reject"); err != nil {
			return err
		}
		conf := fmt.Sprintf("\n# AI Bid desktop: private Unix socket only.\nlisten_addresses = ''\nport = %s\nunix_socket_directories = '%s'\nunix_socket_permissions = 0700\n", pgPort, b.SocketDir)
		f, err := os.OpenFile(filepath.Join(cluster, "postgresql.conf"), os.O_APPEND|os.O_WRONLY, 0)
		if err != nil {
			return err
		}
		_, err = f.WriteString(conf)
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			return err
		}
	}
	// A cluster left running by a crashed launch is stopped and restarted so
	// it uses this launch's log and settings.
	if b.pg("pg_ctl", "-D", cluster, "status") == nil {
		if err := b.pg("pg_ctl", "-D", cluster, "-m", "fast", "-w", "stop"); err != nil {
			return err
		}
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.stopped {
		return errStopped
	}
	if err := b.pg("pg_ctl", "-D", cluster, "-l", filepath.Join(b.LogDir, "postgres.log"), "-w", "-t", "60", "start"); err != nil {
		return err
	}
	b.pgRunning = true
	out, err := b.pgOutput("psql", "-h", b.SocketDir, "-p", pgPort, "-U", "bid_owner", "-d", "postgres",
		"-tAc", "SELECT 1 FROM pg_database WHERE datname = 'bid'")
	if err != nil {
		return err
	}
	if strings.TrimSpace(out) != "1" {
		return b.pg("createdb", "-h", b.SocketDir, "-p", pgPort, "-U", "bid_owner", "bid")
	}
	return nil
}

func (b *Backend) databaseURL(role string) string {
	return fmt.Sprintf("postgresql+psycopg://%s@/bid?host=%s&port=%s", role, b.SocketDir, pgPort)
}

// baseEnv is the whole environment of the server processes: the user's shell
// variables never reach them, so a stray BID_* setting cannot change the app.
func (b *Backend) baseEnv() []string {
	return []string{
		"HOME=" + os.Getenv("HOME"),
		"TMPDIR=" + os.TempDir(),
		"PATH=" + b.PGBin + ":/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
		"LANG=en_US.UTF-8",
		"LC_ALL=en_US.UTF-8",
		"PYTHONUNBUFFERED=1",
		"BID_DATABASE_URL=" + b.databaseURL("bid_app"),
		"BID_ACCESS_KEY=" + b.install.AccessKey,
		"BID_ENCRYPTION_KEY=" + b.install.EncryptionKey,
		"BID_TOKEN_KEY=" + b.install.TokenKey,
		"BID_SECRETS_KEY=" + b.install.SecretsKey,
		"BID_DATA_DIR=" + filepath.Join(b.DataDir, "files"),
		"BID_WEB_DIR=" + filepath.Join(b.ServerDir, "web", "dist"),
		// Vendor search needs no local service: it uses the Perplexity
		// credential added under 服务凭据, and reports itself unavailable without one.
		"BID_SEARCH_PROVIDER=perplexity",
	}
}

func (b *Backend) ownerEnv() []string {
	return append(b.baseEnv(), "BID_MIGRATION_DATABASE_URL="+b.databaseURL("bid_owner"))
}

func (b *Backend) runPython(logName string, env []string, args ...string) error {
	cmd := exec.Command(filepath.Join(b.ServerDir, ".venv", "bin", "python"), args...)
	cmd.Dir = b.ServerDir
	cmd.Env = env
	log, err := os.OpenFile(filepath.Join(b.LogDir, logName), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer log.Close()
	cmd.Stdout, cmd.Stderr = log, log
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%w (see %s)", err, log.Name())
	}
	return nil
}

func (b *Backend) spawn(logName, name string, args ...string) (*process, error) {
	cmd := exec.Command(name, args...)
	cmd.Dir = b.ServerDir
	cmd.Env = b.baseEnv()
	log, err := os.OpenFile(filepath.Join(b.LogDir, logName), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, err
	}
	cmd.Stdout, cmd.Stderr = log, log
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.stopped {
		log.Close()
		return nil, errStopped
	}
	if err := cmd.Start(); err != nil {
		log.Close()
		return nil, err
	}
	p := &process{cmd: cmd, done: make(chan struct{})}
	b.processes = append(b.processes, p)
	go func() { _ = cmd.Wait(); log.Close(); close(p.done) }()
	return p, nil
}

func (b *Backend) waitHealthy(api *process, timeout time.Duration) error {
	client := http.Client{Timeout: 2 * time.Second}
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		select {
		case <-api.done:
			return fmt.Errorf("API exited during startup (see %s)", filepath.Join(b.LogDir, "api.log"))
		default:
		}
		resp, err := client.Get(b.url + "/health")
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return nil
			}
		}
		time.Sleep(300 * time.Millisecond)
	}
	return fmt.Errorf("API not healthy after %s (see %s)", timeout, filepath.Join(b.LogDir, "api.log"))
}

func (b *Backend) pg(tool string, args ...string) error {
	_, err := b.pgOutput(tool, args...)
	return err
}

func (b *Backend) pgOutput(tool string, args ...string) (string, error) {
	cmd := exec.Command(filepath.Join(b.PGBin, tool), args...)
	cmd.Env = []string{"LANG=en_US.UTF-8", "LC_ALL=en_US.UTF-8", "TMPDIR=" + os.TempDir()}
	out, err := cmd.CombinedOutput()
	if err != nil {
		return string(out), fmt.Errorf("%s: %w: %s", tool, err, strings.TrimSpace(string(out)))
	}
	return string(out), nil
}

// consolePort reuses the stored port, or selects a new one when it is taken.
// Start persists the installation after migrations succeed, including reused ports.
func (b *Backend) consolePort() (string, error) {
	if b.install.Port != "" {
		if l, err := net.Listen("tcp", "127.0.0.1:"+b.install.Port); err == nil {
			l.Close()
			return b.install.Port, nil
		}
	}
	port, err := freePort()
	if err != nil {
		return "", err
	}
	b.install.Port = port
	b.installationDirty = true
	return port, nil
}

func freePort() (string, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return "", err
	}
	defer l.Close()
	_, port, err := net.SplitHostPort(l.Addr().String())
	return port, err
}

func randomBytes(n int) []byte {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return b
}

// fernetKey matches cryptography's Fernet.generate_key: 32 random bytes in
// URL-safe base64 with padding.
func fernetKey() string { return base64.URLEncoding.EncodeToString(randomBytes(32)) }
