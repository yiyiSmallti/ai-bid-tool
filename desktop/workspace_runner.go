package main

import (
	"context"
	"errors"
	"sync"
)

// WorkspaceRunner opens one workspace at a time. The personal workspace runs
// the local server and database; an enterprise workspace runs nothing locally
// and talks to its gateway. Switching starts or stops the local stack and never
// writes its data.
type WorkspaceRunner struct {
	Store   WorkspaceStore
	Secrets SecretStore
	// NewBackend prepares a local stack; a stopped Backend is not restarted.
	NewBackend func() (*Backend, error)

	mu      sync.Mutex
	backend *Backend
	local   *APIClient
	stopped bool
}

// OpenWorkspace is the workspace the console shows and the client it uses.
type OpenWorkspace struct {
	Workspace Workspace
	Client    *APIClient
	// Account is GET /auth/session of a signed-in enterprise workspace; nil
	// when its user has to sign in.
	Account Object
}

// Open makes id the current workspace. An enterprise workspace that cannot be
// reached is still opened, with the error, so the console can offer a retry.
func (r *WorkspaceRunner) Open(ctx context.Context, id string) (*OpenWorkspace, error) {
	list, err := r.Store.Load()
	if err != nil {
		return nil, err
	}
	workspace, ok := list.Find(id)
	if !ok {
		return nil, errors.New("工作区不存在")
	}
	opened := &OpenWorkspace{Workspace: workspace}
	var unlocked error
	if workspace.Kind == "personal" {
		if opened.Client, unlocked = r.startLocal(ctx); opened.Client == nil {
			return nil, unlocked
		}
	} else {
		r.stopLocal()
		opened.Client = NewGatewayClient(workspace.URL, workspace.PinSHA256)
	}
	if list.Current != id {
		list.Current = id
		if err := r.Store.Save(list); err != nil {
			return nil, err
		}
	}
	if workspace.Kind == "enterprise" {
		return opened, r.Resume(ctx, opened)
	}
	return opened, unlocked
}

// Resume checks the gateway's contract and the stored session. An expired or
// revoked session is removed from the Keychain and leaves Account nil.
func (r *WorkspaceRunner) Resume(ctx context.Context, opened *OpenWorkspace) error {
	opened.Account = nil
	if err := checkContract(ctx, opened.Client); err != nil {
		return err
	}
	token, err := r.Secrets.Get(opened.Workspace.ID)
	if err != nil || token == "" {
		return err
	}
	opened.Client.SetSession(token)
	result, err := opened.Client.Request(ctx, "GET", "/auth/session", nil)
	var apiError *APIError
	if errors.As(err, &apiError) && apiError.Status == 401 {
		return r.Secrets.Delete(opened.Workspace.ID)
	}
	if err != nil {
		return err
	}
	opened.Account = result.Data
	return nil
}

// SignIn exchanges a login and password for a session kept in the Keychain.
// The password is not kept anywhere.
func (r *WorkspaceRunner) SignIn(ctx context.Context, opened *OpenWorkspace, login, password string) error {
	if err := checkContract(ctx, opened.Client); err != nil {
		return err
	}
	result, err := opened.Client.Request(ctx, "POST", "/auth/session", Object{"login": login, "password": password, "client_label": "AI Bid 桌面客户端"})
	if err != nil {
		return err
	}
	session := str(result.Data, "session")
	if session == "" || integer(result.Data, "expires_in") <= 0 {
		return errors.New("会话响应不符合契约")
	}
	if err := r.Secrets.Set(opened.Workspace.ID, session); err != nil {
		return err
	}
	opened.Client.SetSession(session)
	return r.Resume(ctx, opened)
}

// SignOut revokes the session on the gateway and forgets it here.
func (r *WorkspaceRunner) SignOut(ctx context.Context, opened *OpenWorkspace) error {
	_, err := opened.Client.Request(ctx, "DELETE", "/auth/session", nil)
	var apiError *APIError
	if err != nil && !(errors.As(err, &apiError) && apiError.Status == 401) {
		return err
	}
	opened.Client.SetSession("")
	opened.Account = nil
	return r.Secrets.Delete(opened.Workspace.ID)
}

func checkContract(ctx context.Context, client *APIClient) error {
	result, err := client.Request(ctx, "GET", "/installation", nil)
	if err != nil {
		return err
	}
	if str(result.Data, "mode") != "enterprise" {
		return errors.New("该地址已不是企业服务")
	}
	if problem := contractProblem(str(result.Data, "contract_version")); problem != "" {
		return errors.New(problem)
	}
	return nil
}

func (r *WorkspaceRunner) startLocal(ctx context.Context) (*APIClient, error) {
	r.mu.Lock()
	if r.stopped {
		r.mu.Unlock()
		return nil, errStopped
	}
	if r.local != nil {
		local := r.local
		r.mu.Unlock()
		return local, nil
	}
	if r.backend != nil {
		r.mu.Unlock()
		return nil, errors.New("本机服务正在启动")
	}
	backend, err := r.NewBackend()
	if err != nil {
		r.mu.Unlock()
		return nil, err
	}
	r.backend = backend
	r.mu.Unlock()
	url, err := backend.Start()
	if err != nil {
		r.stopLocal()
		return nil, err
	}
	client := NewAPIClient(url)
	r.mu.Lock()
	if r.backend != backend {
		r.mu.Unlock()
		return nil, errStopped
	}
	r.local = client
	r.mu.Unlock()
	// A refused key leaves the stack running for the user to enter it.
	return client, client.Exchange(ctx, backend.install.AccessKey)
}

func (r *WorkspaceRunner) stopLocal() {
	r.mu.Lock()
	backend := r.backend
	r.backend, r.local = nil, nil
	r.mu.Unlock()
	if backend != nil {
		backend.Stop()
	}
}

// Stop ends the local stack for good, as the app quits.
func (r *WorkspaceRunner) Stop() {
	r.mu.Lock()
	r.stopped = true
	r.mu.Unlock()
	r.stopLocal()
}
