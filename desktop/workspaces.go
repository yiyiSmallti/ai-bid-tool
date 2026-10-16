package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
)

// The personal workspace is this computer's own server and database; it always
// exists and cannot be removed.
const personalWorkspace = "personal"

var (
	workspaceIDPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	pinPattern         = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// Workspace is one entry of workspaces.json. An enterprise workspace names its
// gateway's origin and, for a certificate the system does not trust, the
// SHA-256 of the certificate its user confirmed. Sessions live in the Keychain.
type Workspace struct {
	ID        string
	Kind      string // "personal" or "enterprise"
	Name      string
	URL       string
	PinSHA256 *string
}

func (w Workspace) MarshalJSON() ([]byte, error) {
	if w.Kind == "personal" {
		return json.Marshal(struct {
			ID   string `json:"id"`
			Kind string `json:"kind"`
			Name string `json:"name"`
		}{w.ID, w.Kind, w.Name})
	}
	return json.Marshal(struct {
		ID        string  `json:"id"`
		Kind      string  `json:"kind"`
		Name      string  `json:"name"`
		URL       string  `json:"url"`
		PinSHA256 *string `json:"pin_sha256"`
	}{w.ID, w.Kind, w.Name, w.URL, w.PinSHA256})
}

func (w *Workspace) UnmarshalJSON(data []byte) error {
	var raw struct {
		ID        string  `json:"id"`
		Kind      string  `json:"kind"`
		Name      string  `json:"name"`
		URL       string  `json:"url"`
		PinSHA256 *string `json:"pin_sha256"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	*w = Workspace{ID: raw.ID, Kind: raw.Kind, Name: raw.Name, URL: raw.URL, PinSHA256: raw.PinSHA256}
	return nil
}

// Workspaces is the client's list of workspaces and the one it opens.
type Workspaces struct {
	Current    string      `json:"current"`
	Workspaces []Workspace `json:"workspaces"`
}

func defaultWorkspaces() Workspaces {
	return Workspaces{Current: personalWorkspace, Workspaces: []Workspace{{ID: personalWorkspace, Kind: "personal", Name: "本机"}}}
}

func (w Workspaces) Find(id string) (Workspace, bool) {
	for _, item := range w.Workspaces {
		if item.ID == id {
			return item, true
		}
	}
	return Workspace{}, false
}

func (w Workspaces) validate() error {
	personal := 0
	seen := map[string]bool{}
	for _, item := range w.Workspaces {
		if seen[item.ID] {
			return errors.New("工作区重复：" + item.ID)
		}
		seen[item.ID] = true
		switch item.Kind {
		case "personal":
			if item.ID != personalWorkspace || item.URL != "" || item.PinSHA256 != nil {
				return errors.New("本机工作区记录无效")
			}
			personal++
		case "enterprise":
			if !workspaceIDPattern.MatchString(item.ID) || item.Name == "" {
				return errors.New("企业工作区记录无效")
			}
			if origin, err := gatewayOrigin(item.URL); err != nil || origin != item.URL {
				return errors.New("企业工作区地址无效：" + item.URL)
			}
			if item.PinSHA256 != nil && !pinPattern.MatchString(*item.PinSHA256) {
				return errors.New("企业工作区证书指纹无效")
			}
		default:
			return errors.New("工作区类型无效：" + item.Kind)
		}
	}
	if personal != 1 {
		return errors.New("缺少本机工作区")
	}
	if !seen[w.Current] {
		return errors.New("当前工作区不存在")
	}
	return nil
}

// WorkspaceStore reads and writes workspaces.json in the client data directory.
type WorkspaceStore struct{ Path string }

func (s WorkspaceStore) Load() (Workspaces, error) {
	data, err := os.ReadFile(s.Path)
	if errors.Is(err, os.ErrNotExist) {
		return defaultWorkspaces(), nil
	}
	if err != nil {
		return Workspaces{}, err
	}
	var w Workspaces
	if err := json.Unmarshal(data, &w); err != nil {
		return Workspaces{}, errors.New("workspaces.json 无法读取：" + err.Error())
	}
	if err := w.validate(); err != nil {
		return Workspaces{}, err
	}
	return w, nil
}

// Save replaces the file atomically, owner-only.
func (s WorkspaceStore) Save(w Workspaces) error {
	if err := w.validate(); err != nil {
		return err
	}
	data, err := json.MarshalIndent(w, "", "  ")
	if err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(s.Path), ".workspaces-*.json")
	if err != nil {
		return err
	}
	defer os.Remove(temp.Name())
	if _, err := temp.Write(append(data, '\n')); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Chmod(0o600); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(temp.Name(), s.Path)
}

// newWorkspaceID names an added enterprise workspace.
func newWorkspaceID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	h := hex.EncodeToString(b[:])
	return h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:], nil
}
