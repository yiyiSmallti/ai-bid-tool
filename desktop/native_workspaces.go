package main

import (
	"context"
	"errors"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"
)

// nativeFullscreen pages show without the workspace shell: they have no
// session, or the session may do nothing else yet.
type nativeFullscreen interface{ Fullscreen(*NativeApp) bool }

func (a *NativeApp) enterprise() bool {
	return a.Workspace != nil && a.Workspace.Workspace.Kind == "enterprise"
}

// member is the signed-in member of an enterprise workspace, or nil.
func (a *NativeApp) member() Object {
	if !a.enterprise() || a.Workspace.Account == nil {
		return nil
	}
	return object(a.Workspace.Account["member"])
}

func (a *NativeApp) admin() bool { return str(a.member(), "role") == "admin" }

func (a *NativeApp) mustChangePassword() bool {
	return a.enterprise() && a.Workspace.Account != nil && a.Workspace.Account["must_change_password"] == true
}

// accountPage holds the enterprise pages: the member's own, company members,
// project members, and adding a gateway.
func accountPage(r Route) NativePage {
	switch r.Path {
	case "/workspaces/add":
		return &addGatewayPage{}
	case "/account/password":
		return &passwordPage{}
	case "/account/sessions":
		return &sessionsPage{}
	case "/org/members":
		return newMembersPage()
	}
	if parts := strings.Split(strings.Trim(r.Path, "/"), "/"); len(parts) == 4 && parts[0] == "org" && parts[1] == "tasks" && parts[3] == "members" {
		return &projectMembersPage{task: parts[2]}
	}
	return nil
}

// opened shows a workspace the runner opened, or why it could not be opened.
func (a *NativeApp) opened(opened *OpenWorkspace, err error) {
	if opened == nil {
		a.Client, a.Workspace = nil, nil
		a.Starting = ""
		if err == nil {
			err = errors.New("工作区未能打开")
		}
		a.Error = err.Error()
		return
	}
	a.Starting = ""
	a.Workspace, a.Client = opened, opened.Client
	a.history = nil
	a.Route = Route{}
	if err != nil || (opened.Workspace.Kind == "enterprise" && opened.Account == nil) {
		a.navigate("/login")
		if err != nil {
			a.Error = err.Error()
		}
		return
	}
	a.home()
}

// home opens the task list, or the password page a new or reset member must
// pass first.
func (a *NativeApp) home() {
	if a.mustChangePassword() {
		a.navigate("/account/password")
		return
	}
	a.navigate("/org/tasks")
}

// switchWorkspace leaves the current workspace and opens id. The local stack
// stops or starts on the way; its data is not touched.
func (a *NativeApp) switchWorkspace(id string) {
	if a.Runner == nil || (a.Workspace != nil && a.Workspace.Workspace.ID == id && a.Client != nil) {
		return
	}
	leave := func() {
		a.PrivacyBlur()
		a.cancel()
		a.ctx, a.cancel = context.WithCancel(context.Background())
		a.epoch++
		if a.page != nil {
			a.page.Close()
		}
		a.page, a.Client, a.Workspace = nil, nil, nil
		a.Busy, a.pending, a.Error, a.Notice = false, 0, "", ""
		a.Starting = "正在打开工作区…"
		if list, err := a.Runner.Store.Load(); err == nil {
			if target, ok := list.Find(id); ok && target.Kind == "personal" {
				a.Starting = "正在启动本地服务…"
			} else if ok {
				a.Starting = "正在连接 " + target.Name + "…"
			}
		}
		runner := a.Runner
		go func() {
			opened, err := runner.Open(context.Background(), id)
			a.update(func() { a.opened(opened, err) })
		}()
	}
	if a.page != nil && a.page.Dirty() {
		a.Confirm("切换工作区", "尚有未保存的更改，切换将丢弃更改。", leave)
		return
	}
	leave()
}

// workspaceSwitcher lists every workspace; choosing one switches to it.
func (a *NativeApp) workspaceSwitcher(c *ui.Context) {
	p := nativePalette(c)
	if a.Runner == nil {
		return
	}
	list, err := a.Runner.Store.Load()
	if err != nil {
		return
	}
	const add = "+add"
	current := ""
	if a.Workspace != nil {
		current = a.Workspace.Workspace.ID
	}
	value := current
	names := map[string]string{add: "添加企业服务…"}
	options := []string{}
	for _, item := range list.Workspaces {
		options = append(options, item.ID)
		names[item.ID] = item.Name
	}
	options = append(options, add)
	choice := ui.SelectBase(c.Key("workspaces"), &value)
	trigger := choice.Trigger.FillWidth().Padding(6, 10).Gap(8).MinHeight(34).Radius(8).Border(1, p.Border).Background(p.Card).Justify(ui.SpaceBetween).AlignItems(ui.Center).Label("工作区")
	if trigger.Hovered() {
		trigger.Background(c.Theme().SurfaceHover)
	}
	trigger.Children(func() {
		icon := "monitor"
		if a.enterprise() {
			icon = "building-2"
		}
		ui.Icon(c, nativeIcon(icon)).FontSize(14).TextColor(p.Muted)
		ui.Text(c, names[current]).FontSize(13).SingleLine().Grow(1).MinWidth(0)
		ui.Icon(c, nativeIcon("chevrons-up-down")).FontSize(13).TextColor(p.Muted)
	})
	choice.Popup(func(panel ui.Element) {
		panel.Margin(4, 0, 0, 0).Padding(4).Radius(10).Background(p.Card).Border(1, p.Border).Shadow(0, 8, 24, 0, p.Shadow.Alpha(0.16))
		for _, option := range options {
			item := choice.Item(option).Role(ui.RoleListItem).Label(names[option]).Padding(8, 10).Radius(6)
			if item.Highlighted() {
				item.Background(p.AccentSoft).TextColor(p.AccentInk)
			}
			item.Children(func() {
				ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
					ui.Text(c, names[option]).SingleLine()
					if option == current {
						ui.Icon(c, nativeIcon("check")).FontSize(13).TextColor(p.AccentInk)
					}
				})
			})
		}
	})
	if trigger.Changed() && value != current {
		if value == add {
			if a.Client != nil {
				_, signedOut := a.page.(*loginPage)
				a.Navigate("/workspaces/add")
				if page, ok := a.page.(*addGatewayPage); ok {
					page.signedOut = signedOut
				}
			}
			return
		}
		a.switchWorkspace(value)
	}
}

// signOut ends an enterprise session; a personal workspace locks instead.
func (a *NativeApp) signOut() {
	if !a.enterprise() {
		a.lock()
		return
	}
	out := func() {
		a.PrivacyBlur()
		workspace := *a.Workspace
		runner := a.Runner
		a.Do(func(ctx context.Context) (func(), error) {
			err := runner.SignOut(ctx, &workspace)
			return func() { *a.Workspace = workspace; a.navigate("/login") }, err
		})
	}
	if a.page != nil && a.page.Dirty() {
		a.Confirm("退出登录", "退出将丢弃未保存更改，继续？", out)
	} else {
		out()
	}
}

// expired returns an enterprise workspace to sign-in and forgets its session.
func (a *NativeApp) expired() {
	workspace, runner := a.Workspace.Workspace, a.Runner
	a.Workspace.Account = nil
	a.Client.SetSession("")
	go func() { _ = runner.Secrets.Delete(workspace.ID) }()
	a.navigate("/login")
	a.Error = "登录已过期，请重新登录"
}

// enterpriseSignIn is the login page of an enterprise workspace.
func (p *loginPage) enterpriseSignIn(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	ui.Column(c).FillWidth().Gap(6).Children(func() {
		ui.Text(c, "登录 "+a.Workspace.Workspace.Name).FontSize(22).FontWeight(650)
		ui.Text(c, a.Workspace.Workspace.URL).FontSize(12.5).TextColor(palette.Muted).Selectable()
	})
	signIn := func() { p.signIn(a) }
	ui.Column(c).FillWidth().Gap(6).Children(func() {
		ui.Text(c, "登录名").FontSize(13).FontWeight(500)
		if ui.TextInput(c, &p.login).Label("登录名").Placeholder("管理员分配的登录名").AutoFocus().Height(40).FillWidth().Radius(8).Submitted() {
			signIn()
		}
	})
	ui.Column(c).FillWidth().Gap(6).Children(func() {
		ui.Text(c, "密码").FontSize(13).FontWeight(500)
		if ui.TextInput(c, &p.password).Password().Label("密码").Placeholder("输入密码").Height(40).FillWidth().Radius(8).Submitted() {
			signIn()
		}
	})
	nativeIconButton(c, "log-in", "登录", true).FillWidth().AlignSelf(ui.Stretch).MinHeight(40).Justify(ui.Center).Disabled(a.Busy || p.login == "" || p.password == "").OnClick(signIn)
}

// signIn trades the typed login and password for a session; the password
// field is cleared whatever the outcome.
func (p *loginPage) signIn(a *NativeApp) {
	if a.Busy || p.login == "" || p.password == "" {
		return
	}
	login, password := p.login, p.password
	p.password = ""
	workspace := *a.Workspace
	runner := a.Runner
	a.Do(func(ctx context.Context) (func(), error) {
		err := runner.SignIn(ctx, &workspace, login, password)
		var api *APIError
		if errors.As(err, &api) && api.Status == 401 {
			err = errors.New("登录名或密码不正确")
		}
		return func() { *a.Workspace = workspace; a.home() }, err
	})
}

// fullscreenSwitcher lets a signed-out or failed workspace give way to another.
func (a *NativeApp) fullscreenSwitcher(c *ui.Context) {
	if a.Runner == nil {
		return
	}
	palette := nativePalette(c)
	ui.Column(c).FillWidth().Gap(6).Padding(12, 0, 0, 0).BorderWidth(1, 0, 0, 0).BorderColor(palette.Border).Children(func() {
		ui.Text(c, "工作区").FontSize(12).TextColor(palette.Muted)
		a.workspaceSwitcher(c)
	})
}

type addGatewayPage struct {
	address   string
	info      *GatewayInfo
	untrusted string // fingerprint the user is asked to confirm
	pin       *string
	// signedOut pages came from sign-in and return there without the shell.
	signedOut bool
}

func (*addGatewayPage) Title() string                { return "添加企业服务" }
func (p *addGatewayPage) Dirty() bool                { return p.address != "" }
func (p *addGatewayPage) Close()                     { *p = addGatewayPage{} }
func (*addGatewayPage) Open(*NativeApp)              {}
func (p *addGatewayPage) Fullscreen(*NativeApp) bool { return p.signedOut }
func (p *addGatewayPage) probe(a *NativeApp, pin *string) {
	address := p.address
	a.Do(func(ctx context.Context) (func(), error) {
		info, err := ProbeGateway(ctx, address, pin)
		var untrusted *UntrustedCertificateError
		if errors.As(err, &untrusted) {
			return func() { p.info, p.untrusted, p.pin = nil, untrusted.Fingerprint, nil }, nil
		}
		return func() { p.info, p.untrusted, p.pin = &info, "", pin }, err
	})
}
func (p *addGatewayPage) add(a *NativeApp) {
	info, pin, runner := *p.info, p.pin, a.Runner
	a.Do(func(context.Context) (func(), error) {
		list, err := runner.Store.Load()
		if err != nil {
			return nil, err
		}
		for _, item := range list.Workspaces {
			if item.URL == info.Origin {
				return nil, errors.New("该企业服务已添加：" + item.Name)
			}
		}
		id, err := newWorkspaceID()
		if err != nil {
			return nil, err
		}
		list.Workspaces = append(list.Workspaces, Workspace{ID: id, Kind: "enterprise", Name: info.CompanyName, URL: info.Origin, PinSHA256: pin})
		if err := runner.Store.Save(list); err != nil {
			return nil, err
		}
		return func() { p.address = ""; a.switchWorkspace(id) }, nil
	})
}
func (p *addGatewayPage) View(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	if p.signedOut {
		ui.Text(c, "添加企业服务").FontSize(22).FontWeight(650)
	}
	nativeHint(c, "输入公司企业服务的 HTTPS 地址。客户端会先读取服务信息，确认是企业服务后再添加；本机数据不会上传。")
	if nativeField(c, "企业服务地址", &p.address, false) {
		p.info, p.untrusted, p.pin = nil, "", nil
	}
	if p.untrusted != "" {
		nativeCallout(c, toneWarning, "系统不信任该服务的证书。请向管理员核对下面的 SHA-256 指纹，一致后再确认添加；之后该工作区只接受这一证书。")
		// Two lines of 16 bytes read more easily than one wrapped run.
		fingerprint := formatFingerprint(p.untrusted)
		ui.Column(c).Gap(2).Children(func() {
			ui.Text(c, fingerprint[:47]).FontSize(13).TextColor(palette.Text).Selectable()
			ui.Text(c, fingerprint[48:]).FontSize(13).TextColor(palette.Text).Selectable()
		})
	}
	if p.info != nil {
		nativePairs(c, []string{"公司", "服务版本", "契约版本"}, []string{p.info.CompanyName, p.info.ServerVersion, p.info.ContractVersion})
		if problem := contractProblem(p.info.ContractVersion); problem != "" {
			nativeCallout(c, toneDanger, problem)
		}
	}
	nativeActions(c).Children(func() {
		switch {
		case p.untrusted != "":
			fingerprint := p.untrusted
			nativeIconButton(c, "badge-check", "指纹一致，继续", true).Disabled(a.Busy).OnClick(func() { p.probe(a, &fingerprint) })
		case p.info != nil:
			nativeIconButton(c, "plus", "添加并切换", true).Disabled(a.Busy || contractProblem(p.info.ContractVersion) != "").OnClick(func() { p.add(a) })
		default:
			nativeIconButton(c, "search", "读取服务信息", true).Disabled(a.Busy || p.address == "").OnClick(func() { p.probe(a, nil) })
		}
		nativeButton(c, "返回").Disabled(a.Busy).OnClick(func() {
			p.address = ""
			if p.signedOut {
				a.navigate("/login")
				return
			}
			a.navigate("/org/tasks")
		})
	})
}

type passwordPage struct{ current, next, confirm string }

func (*passwordPage) Title() string                { return "修改密码" }
func (p *passwordPage) Dirty() bool                { return p.current != "" || p.next != "" || p.confirm != "" }
func (p *passwordPage) Close()                     { *p = passwordPage{} }
func (*passwordPage) Open(*NativeApp)              {}
func (*passwordPage) Fullscreen(a *NativeApp) bool { return a.mustChangePassword() }
func (p *passwordPage) View(c *ui.Context, a *NativeApp) {
	forced := a.mustChangePassword()
	if forced {
		ui.Text(c, "设置新密码").FontSize(22).FontWeight(650)
		nativeHint(c, "首次登录或密码被重置后，需要先设置自己的密码才能继续。")
	} else {
		nativeHint(c, "修改后，本账号在其他设备上的登录会全部退出。")
	}
	ui.Column(c).FillWidth().Gap(12).Children(func() {
		for _, field := range []struct {
			label string
			value *string
		}{{"当前密码", &p.current}, {"新密码", &p.next}, {"再次输入新密码", &p.confirm}} {
			ui.Column(c.Key(field.label)).FillWidth().MaxWidth(420).Gap(6).Children(func() {
				ui.Text(c, field.label).FontSize(12.5).TextColor(c.Theme().TextMuted)
				ui.TextInput(c, field.value).Password().Label(field.label).Height(38).FillWidth().Radius(8)
			})
		}
	})
	mismatch := p.confirm != "" && p.next != p.confirm
	if mismatch {
		nativeCallout(c, toneWarning, "两次输入的新密码不一致")
	}
	change := func() { p.change(a) }
	ui.Row(c).FillWidth().Gap(8).Padding(6, 0, 0, 0).Children(func() {
		nativeIconButton(c, "key-round", "更新密码", true).Disabled(a.Busy || p.current == "" || p.next == "" || p.next != p.confirm).OnClick(change)
		if forced {
			nativeButton(c, "退出登录").Disabled(a.Busy).OnClick(a.signOut)
		}
	})
}

// change replaces the member's password and reads the session again, which
// lifts a forced change. A wrong current password keeps the session.
func (p *passwordPage) change(a *NativeApp) {
	if a.Busy || p.current == "" || p.next == "" || p.next != p.confirm {
		return
	}
	current, next := p.current, p.next
	workspace := *a.Workspace
	runner := a.Runner
	a.Do(func(ctx context.Context) (func(), error) {
		r, err := workspace.Client.Request(ctx, "POST", "/account/password", Object{"current": current, "new": next})
		var api *APIError
		if errors.As(err, &api) {
			switch api.Code {
			case "invalid_login":
				return nil, errors.New("当前密码不正确")
			case "password_unchanged":
				return nil, errors.New("新密码不能与当前密码相同")
			case "password_too_short":
				if n := regexp.MustCompile(`[0-9]+`).FindString(api.Message); n != "" {
					return nil, errors.New("新密码至少需要 " + n + " 个字符")
				}
				return nil, errors.New("新密码太短")
			}
		}
		if err != nil {
			return nil, err
		}
		err = runner.Resume(ctx, &workspace)
		return func() {
			*a.Workspace = workspace
			*p = passwordPage{}
			revoked := integer(r.Data, "revoked_sessions")
			a.navigate("/org/tasks")
			a.Notice = "密码已更新"
			if revoked > 0 {
				a.Notice += "，其他 " + strconv.Itoa(revoked) + " 处登录已退出"
			}
		}, err
	})
}

type sessionsPage struct{ items []Object }

func (*sessionsPage) Title() string { return "我的登录" }
func (*sessionsPage) Dirty() bool   { return false }
func (p *sessionsPage) Close()      { p.items = nil }
func (p *sessionsPage) Open(a *NativeApp) {
	a.Request("GET", "/account/sessions", nil, func(r Result) { p.items = r.Items })
}
func (p *sessionsPage) View(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	nativeHint(c, "本账号当前有效的登录。退出某处登录后，该设备需要重新登录。")
	if len(p.items) == 0 {
		nativeEmpty(c, "monitor-smartphone", "没有有效的登录。", nil)
		return
	}
	items := slices.Clone(p.items)
	ui.Column(c).FillWidth().Gap(8).Children(func() {
		for _, item := range items {
			id := str(item, "id")
			ui.Row(c.Key(id)).FillWidth().Gap(12).Padding(12, 14).Radius(10).Border(1, palette.Border).AlignItems(ui.Center).Children(func() {
				ui.Icon(c, nativeIcon("monitor")).FontSize(16).TextColor(palette.Muted)
				ui.Column(c).Grow(1).MinWidth(0).Gap(3).Children(func() {
					ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
						label := str(item, "client_label")
						if label == "" {
							label = "未命名客户端"
						}
						ui.Text(c, label).FontSize(13.5).FontWeight(600).SingleLine()
						if item["current"] == true {
							nativeBadge(c, "本机", toneAccent)
						}
					})
					created, _ := nativeTime(str(item, "created_at"))
					seen, _ := nativeTime(str(item, "last_seen_at"))
					expires, _ := nativeTime(str(item, "expires_at"))
					ui.Text(c, "登录于 "+created+" · 最近活动 "+seen+" · 到期 "+expires).FontSize(12).TextColor(palette.Muted).SingleLine()
				})
				if item["current"] != true {
					nativeButton(c, "退出该登录").Disabled(a.Busy).OnClick(func() {
						a.Confirm("退出该登录", "该设备需要重新登录。", func() {
							a.Request("DELETE", "/account/sessions/"+id, nil, func(Result) { p.Open(a) })
						})
					})
				}
			})
		}
	})
}
