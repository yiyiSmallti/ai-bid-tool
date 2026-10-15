package main

import (
	"context"
	"errors"
	"net/url"
	"strings"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"
)

type Route struct {
	Path   string
	Params map[string]string
	Query  url.Values
}
type NativePage interface {
	Title() string
	Open(*NativeApp)
	View(*ui.Context, *NativeApp)
	Dirty() bool
	Close()
}
type NativeApp struct {
	Route           Route
	Client          *APIClient
	Window          *mygo.Window
	Busy            bool
	Error, Notice   string
	page            NativePage
	cancel          context.CancelFunc
	ctx             context.Context
	epoch           uint64
	pending         int
	history         []string
	Dispatch        func(func())
	ConfirmDialog   func(string, string) (bool, error)
	SaveDestination func(string) (string, error)
	allowExit       bool
	// taskNames keeps loaded task names for the sidebar and breadcrumbs.
	taskNames     map[string]string
	taskDeadlines map[string]any
}

func NewNativeApp() *NativeApp {
	ctx, cancel := context.WithCancel(context.Background())
	return &NativeApp{ctx: ctx, cancel: cancel, taskNames: map[string]string{}, taskDeadlines: map[string]any{}}
}
func parseRoute(path string) Route {
	u, err := url.Parse(path)
	if err != nil {
		u = &url.URL{Path: "/org/tasks"}
	}
	p := u.Path
	if p == "/app" {
		p = "/"
	} else if strings.HasPrefix(p, "/app/") {
		p = strings.TrimPrefix(p, "/app")
	}
	if p == "" || p == "/" {
		p = "/org/tasks"
	}
	route := Route{Path: p, Query: u.Query(), Params: map[string]string{}}
	parts := strings.Split(strings.Trim(p, "/"), "/")
	for i := 0; i+1 < len(parts); i++ {
		key := ""
		switch parts[i] {
		case "tasks":
			key = "taskId"
		case "products":
			key = "productId"
		case "features":
			key = "featureId"
		case "templates":
			key = "templateId"
		case "revisions":
			key = "revisionId"
		case "attachments":
			key = "attachmentId"
		case "profiles", "certificates":
			key = "resourceId"
		case "memory":
			key = "memoryId"
		case "cards":
			key = "cardId"
		case "bid-submissions":
			key = "submissionId"
		case "bid-reviews":
			key = "reviewId"
		case "checks", "scores":
			key = "reportId"
		case "score-rubrics":
			key = "rubricId"
		}
		if key != "" {
			route.Params[key] = parts[i+1]
		}
	}
	return route
}
func (a *NativeApp) Navigate(path string) {
	if a.page != nil && a.page.Dirty() {
		a.Confirm("离开当前页面", "尚有未保存的更改，离开将丢弃更改。", func() { a.navigate(path) })
		return
	}
	a.navigate(path)
}
func (a *NativeApp) navigate(path string) {
	if a.Client == nil {
		a.Error = "本地服务尚未就绪"
		return
	}
	a.cancel()
	a.ctx, a.cancel = context.WithCancel(context.Background())
	a.epoch++
	a.pending = 0
	a.Busy = false
	a.Error = ""
	a.Notice = ""
	if a.page != nil {
		a.page.Close()
	}
	if a.Route.Path != "" {
		prior := a.Route.Path
		if query := a.Route.Query.Encode(); query != "" {
			prior += "?" + query
		}
		a.history = append(a.history, prior)
	}
	a.Route = parseRoute(path)
	a.page = managementPage(a.Route)
	if a.page == nil {
		a.page = settingsPage(a.Route)
	}
	if a.page == nil {
		a.page = taskPage(a.Route)
	}
	if a.page == nil {
		a.Route = parseRoute("/org/tasks")
		a.page = &tasksPage{}
	}
	a.page.Open(a)
	a.loadTaskName()
}

// loadTaskName fetches the name of a task entered below its detail page,
// for the sidebar and breadcrumbs. Failure leaves the generic label.
func (a *NativeApp) loadTaskName() {
	task := a.Route.Params["taskId"]
	if _, ok := a.taskNames[task]; task == "" || ok {
		return
	}
	ctx, client := a.ctx, a.Client
	go func() {
		r, err := client.Request(ctx, "GET", "/tasks/"+task, nil)
		if err != nil || ctx.Err() != nil {
			return
		}
		a.update(func() { a.rememberTask(r.Data) })
	}()
}
func (a *NativeApp) Reload() {
	path := a.Route.Path
	if q := a.Route.Query.Encode(); q != "" {
		path += "?" + q
	}
	a.Navigate(path)
}
func (a *NativeApp) Do(work func(context.Context) (func(), error)) {
	epoch, ctx := a.epoch, a.ctx
	a.pending++
	a.Busy = true
	a.Error = ""
	go func() {
		apply, err := work(ctx)
		a.update(func() {
			if epoch != a.epoch || ctx.Err() != nil {
				return
			}
			a.pending--
			a.Busy = a.pending > 0
			if err != nil {
				a.handleError(err)
				return
			}
			if apply != nil {
				apply()
			}
		})
	}()
}
func (a *NativeApp) Request(method, path string, body any, done func(Result)) {
	a.Do(func(ctx context.Context) (func(), error) {
		r, err := a.Client.Request(ctx, method, path, body)
		return func() {
			if len(r.Warnings) > 0 {
				a.Notice = nativeWarnings(r.Warnings)
			}
			if done != nil {
				done(r)
			}
		}, err
	})
}
func (a *NativeApp) Upload(path string, fields map[string]string, files []UploadFile, done func(Result)) {
	a.Do(func(ctx context.Context) (func(), error) {
		r, err := a.Client.Upload(ctx, path, fields, files)
		return func() {
			if done != nil {
				done(r)
			}
		}, err
	})
}
func (a *NativeApp) PickFiles(multiple bool, done func([]string)) {
	a.Do(func(context.Context) (func(), error) {
		files, err := mygo.Dialog.Open(mygo.OpenDialogOptions{Parent: a.Window, Title: "选择本地文件", Multiple: multiple})
		return func() {
			if len(files) > 0 {
				done(files)
			}
		}, err
	})
}
func (a *NativeApp) Confirm(title, message string, action func()) {
	a.Do(func(context.Context) (func(), error) {
		var confirmed bool
		var err error
		if a.ConfirmDialog != nil {
			confirmed, err = a.ConfirmDialog(title, message)
		} else {
			result, dialogErr := mygo.Dialog.Message(mygo.MessageOptions{Parent: a.Window, Type: mygo.MessageQuestion, Title: title, Message: message, Buttons: []string{"取消", "确认"}, CancelButton: 0, DefaultButton: 0})
			err = dialogErr
			confirmed = result.Button == 1
		}
		return func() {
			if confirmed {
				action()
			}
		}, err
	})
}
func (a *NativeApp) update(fn func()) {
	if a.Dispatch != nil {
		a.Dispatch(fn)
		return
	}
	a.Window.Update(fn)
}
func (a *NativeApp) handleError(err error) bool {
	var api *APIError
	if errors.As(err, &api) {
		if api.Status == 401 {
			a.Client.SetSession("")
			a.navigate("/login")
			a.Error = "会话已失效，请重新输入访问密钥"
			return true
		}
		// A removed model is a stale choice on the page, not an inaccessible page.
		if api.Code == "provider_model_unavailable" {
			a.Error = settingsFailures[api.Code]
			return true
		}
		if api.Status == 403 || api.Status == 404 {
			a.cancel()
			a.ctx, a.cancel = context.WithCancel(context.Background())
			a.epoch++
			if a.page != nil {
				a.page.Close()
			}
			a.page = nil
			a.pending = 0
			a.Busy = false
			a.Notice = ""
			a.Error = "当前内容不可访问，请返回任务或重新读取"
			return true
		}
	}
	a.Error = err.Error()
	return false
}
func (a *NativeApp) PrivacyBlur() {
	if page, ok := a.page.(interface{ PrivacyBlur() }); ok {
		page.PrivacyBlur()
	}
}
func (a *NativeApp) nativeFocus() {
	if page, ok := a.page.(interface{ NativeFocus(*NativeApp) }); ok {
		page.NativeFocus(a)
	}
}
func (a *NativeApp) lock() {
	lock := func() {
		a.PrivacyBlur()
		if a.Client != nil {
			a.Client.SetSession("")
		}
		a.navigate("/login")
	}
	if a.page != nil && a.page.Dirty() {
		a.Confirm("锁定安装", "锁定将丢弃未保存更改，继续？", lock)
	} else {
		lock()
	}
}
func (a *NativeApp) bindWindow(win *mygo.Window) {
	a.Window = win
	win.OnBlur(a.PrivacyBlur)
	win.OnHide(a.PrivacyBlur)
	win.OnMinimize(a.PrivacyBlur)
	win.OnFocus(a.nativeFocus)
	win.OnShow(a.nativeFocus)
	win.OnRestore(a.nativeFocus)
	win.OnClose(func(event *mygo.CloseEvent) {
		if !a.allowExit && a.page != nil && a.page.Dirty() {
			event.PreventDefault()
			a.Confirm("关闭窗口", "尚有未保存更改，关闭将丢弃更改。", func() { a.allowExit = true; win.Close() })
		}
	})
	win.OnClosed(func() {
		a.PrivacyBlur()
		a.cancel()
		if a.page != nil {
			a.page.Close()
		}
	})
	mygo.App.OnBeforeQuit(func(event *mygo.QuitEvent) {
		if !a.allowExit && a.page != nil && a.page.Dirty() {
			event.PreventDefault()
			a.Confirm("退出应用", "尚有未保存更改，退出将丢弃更改。", func() { a.allowExit = true; mygo.App.Quit() })
		}
	})
}

// nativeCardless pages lay out their own cards on the canvas; the shell puts
// every other page on one card.
type nativeCardless interface{ Cardless() }

func (a *NativeApp) View(c *ui.Context) {
	nativeTheme(c)
	p := nativePalette(c)
	if _, ok := a.page.(*loginPage); ok || a.Client == nil {
		a.fullscreen(c)
		return
	}
	ui.Row(c).Fill().Background(p.Canvas).Children(func() {
		a.sidebar(c)
		ui.Column(c).Grow(1).MinWidth(0).FillHeight().Children(func() {
			a.header(c)
			if a.Error != "" || a.Notice != "" {
				ui.Column(c).FillWidth().Gap(8).Padding(0, 32, 10, 32).Children(func() {
					if a.Error != "" {
						nativeCallout(c, toneDanger, a.Error)
					}
					if a.Notice != "" {
						nativeCallout(c, toneAccent, a.Notice)
					}
				})
			}
			ui.Scroll(c.Key(a.Route.Path)).Grow(1).FillWidth().Padding(4, 32, 36, 32).Children(func() {
				ui.Column(c).FillWidth().MaxWidth(1240).AlignSelf(ui.Center).Gap(16).Children(func() {
					if job := a.extractionScope(); job != "" {
						ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
							nativeBadge(c, "当前抽取集合", toneAccent)
							ui.Text(c, job).FontSize(12.5).TextColor(p.Muted).Selectable()
						})
					}
					if a.page == nil {
						nativeEmpty(c, "inbox", "当前没有可显示的内容。可从左侧导航重新进入。", nil)
						return
					}
					if _, ok := a.page.(nativeCardless); ok {
						a.page.View(c, a)
						return
					}
					nativeCard(c).Padding(24, 26).Children(func() { a.page.View(c, a) })
				})
			})
		})
	})
}

// header shows where the page sits, its title and the shell's actions. With
// a hidden title bar it also drags the window.
func (a *NativeApp) header(c *ui.Context) {
	p := nativePalette(c)
	bar := c.TitleBar()
	ui.Row(c).FillWidth().MinHeight(bar.Height).Padding(max(bar.Height-12, 20), 32, 16, 32).Gap(14).AlignItems(ui.End).DragWindow().Children(func() {
		ui.Column(c).Grow(1).MinWidth(0).Gap(6).Children(func() {
			crumbs := a.breadcrumbs()
			if len(crumbs) > 0 {
				ui.Row(c).Gap(4).AlignItems(ui.Center).Children(func() {
					for i, crumb := range crumbs {
						if i > 0 {
							ui.Icon(c, nativeIcon("chevron-right")).FontSize(12).TextColor(p.Subtle)
						}
						if crumb.path == "" {
							ui.Text(c, crumb.label).FontSize(12.5).TextColor(p.Muted).SingleLine()
							continue
						}
						link := ui.ButtonBase(c.Key(crumb.path)).Padding(1, 2).Radius(4).Cursor(ui.CursorPointer).Label(crumb.label)
						color := p.Muted
						if link.Hovered() {
							color = p.AccentInk
						}
						link.Children(func() { ui.Text(c, crumb.label).FontSize(12.5).TextColor(color).SingleLine() }).OnClick(func() { a.Navigate(crumb.path) })
					}
				})
			}
			title := "正在启动本地服务"
			if a.page != nil {
				title = a.page.Title()
			}
			ui.Text(c, title).FontSize(24).FontWeight(650).TextColor(p.Text).SingleLine()
		})
		if a.Busy {
			ui.Row(c).Gap(8).AlignItems(ui.Center).Padding(0, 4).Children(func() {
				ui.Spinner(c).Label("正在处理")
				ui.Text(c, "正在处理…").FontSize(12.5).TextColor(p.Muted)
			})
		}
		nativeIconButton(c, "refresh-cw", "刷新", false).Disabled(a.Busy || a.page == nil).OnClick(a.Reload)
	})
}

type nativeCrumb struct{ label, path string }

func (a *NativeApp) breadcrumbs() []nativeCrumb {
	path := a.Route.Path
	if task := a.Route.Params["taskId"]; task != "" {
		crumbs := []nativeCrumb{{"招标任务", "/org/tasks"}}
		base := "/org/tasks/" + task
		if path != base {
			name := a.taskNames[task]
			if name == "" {
				name = "任务"
			}
			crumbs = append(crumbs, nativeCrumb{name, base})
			for _, group := range nativeTaskNavigation {
				for _, item := range group.items {
					if item.path != "" && strings.HasPrefix(path, base+item.path+"/") {
						crumbs = append(crumbs, nativeCrumb{item.label, nativeTaskDestination(task, base+item.path, a.extractionScope())})
					}
				}
			}
		}
		return crumbs
	}
	for _, group := range nativeShellGroups {
		for _, item := range group.items {
			if path == item.path || strings.HasPrefix(path, item.path+"/") {
				if path != item.path {
					return []nativeCrumb{{group.title, ""}, {item.label, item.path}}
				}
				return []nativeCrumb{{group.title, ""}}
			}
		}
	}
	return nil
}

// fullscreen shows the unlock page and the startup screen without the
// workspace shell, since neither has a session to navigate with.
func (a *NativeApp) fullscreen(c *ui.Context) {
	p := nativePalette(c)
	ui.Column(c).Fill().Background(p.Canvas).Justify(ui.Center).AlignItems(ui.Center).Padding(32).DragWindow().Children(func() {
		ui.Column(c).Width(420).MaxWidthPercent(100).Padding(36, 36, 32, 36).Gap(18).Radius(16).Background(p.Card).Border(1, p.Border).Shadow(0, 12, 32, 0, p.Shadow.Alpha(0.12)).Children(func() {
			nativeBrandMark(c, 44)
			if a.Client == nil {
				ui.Text(c, "AI 标书").FontSize(22).FontWeight(650)
				ui.Row(c).Gap(10).AlignItems(ui.Center).Children(func() {
					ui.Spinner(c).Label("正在启动本地服务")
					ui.Text(c, "正在启动本地服务…").FontSize(13.5).TextColor(p.Muted)
				})
				return
			}
			if a.Error != "" {
				nativeCallout(c, toneDanger, a.Error)
			}
			a.page.View(c, a)
		})
	})
}

// nativeBrandMark is the app's logo tile.
func nativeBrandMark(c *ui.Context, size float32) {
	p := nativePalette(c)
	ui.Row(c).Size(size, size).Shrink(0).Radius(size*0.28).Gradient(p.Accent, ui.Hex("#5E3BD8"), 135).Justify(ui.Center).AlignItems(ui.Center).
		Shadow(0, 2, 6, 0, p.Accent.Alpha(0.3)).Children(func() {
		ui.Icon(c, nativeIcon("stamp")).FontSize(size * 0.5).TextColor(ui.Hex("#FFFFFF"))
	})
}

type loginPage struct{ key string }

func (*loginPage) Title() string   { return "访问密钥" }
func (*loginPage) Dirty() bool     { return false }
func (p *loginPage) Close()        { p.key = "" }
func (*loginPage) Open(*NativeApp) {}
func (p *loginPage) View(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	ui.Column(c).FillWidth().Gap(6).Children(func() {
		ui.Text(c, "解锁 AI 标书").FontSize(22).FontWeight(650)
		ui.Text(c, "输入本安装的访问密钥，以本人身份操作。密钥仅在本地交换会话。").FontSize(13.5).LineHeight(1.5).TextColor(palette.Muted)
	})
	unlock := func() {
		if a.Busy || p.key == "" {
			return
		}
		key := p.key
		p.key = ""
		a.Do(func(ctx context.Context) (func(), error) {
			err := a.Client.Exchange(ctx, key)
			return func() { a.navigate("/org/tasks") }, err
		})
	}
	ui.Column(c).FillWidth().Gap(6).Children(func() {
		ui.Text(c, "访问密钥").FontSize(13).FontWeight(500)
		if ui.TextInput(c, &p.key).Password().Label("访问密钥").Placeholder("粘贴或输入访问密钥").AutoFocus().Height(40).FillWidth().Radius(8).Submitted() {
			unlock()
		}
	})
	nativeIconButton(c, "lock", "解锁安装", true).FillWidth().AlignSelf(ui.Stretch).MinHeight(40).Justify(ui.Center).Disabled(a.Busy || p.key == "").OnClick(unlock)
}

// rememberTask caches what the shell shows of a loaded task.
func (a *NativeApp) rememberTask(task Object) {
	if id := str(task, "id"); id != "" {
		a.taskNames[id] = str(task, "name")
		a.taskDeadlines[id] = task["deadline"]
	}
}
