package main

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"
)

func taskPage(r Route) NativePage {
	if r.Path == "/login" {
		return &loginPage{}
	}
	if r.Path == "/org/tasks" {
		return &tasksPage{}
	}
	id := r.Params["taskId"]
	if id == "" {
		return nil
	}
	base := "/org/tasks/" + id
	switch r.Path {
	case base:
		return &taskDetailPage{id: id}
	case base + "/board", base + "/progress":
		return &taskBoardPage{id: id, route: r, progress: strings.HasSuffix(r.Path, "/progress"), job: r.Query.Get("job"), bucket: r.Query.Get("bucket"), category: r.Query.Get("category"), domain: r.Query.Get("domain"), blocker: r.Query.Get("blocker"), starred: r.Query.Get("starred") == "1", mine: r.Query.Get("mine") == "1"}
	case base + "/requirements":
		return newRequirementsPage(r)
	case base + "/review":
		return newResponsesPage(r)
	case base + "/drafts":
		return newDraftsPage(r)
	case base + "/checks", base + "/scores", base + "/score-rubrics":
		return newAssessmentPage(r)
	case base + "/bid-submissions":
		return newBidSubmissionsPage(r)
	}
	if card := r.Params["cardId"]; card != "" && r.Path == base+"/cards/"+card+"/annotation" {
		return newAnnotationPage(r)
	}
	if submission := r.Params["submissionId"]; submission != "" && r.Path == base+"/bid-submissions/"+submission {
		return newBidSubmissionPage(r)
	}
	if review := r.Params["reviewId"]; review != "" && r.Path == base+"/bid-reviews/"+review+"/report" {
		return newBidReportPage(r)
	}
	if report := r.Params["reportId"]; report != "" && (r.Path == base+"/checks/"+report || r.Path == base+"/scores/"+report) {
		return newAssessmentReportPage(r)
	}
	if rubric := r.Params["rubricId"]; rubric != "" && r.Path == base+"/score-rubrics/"+rubric {
		return newAssessmentReportPage(r)
	}
	return nil
}

type nativeNavigationItem struct{ label, path, icon string }

type nativeNavigationGroup struct {
	title string
	items []nativeNavigationItem
}

var nativeTaskNavigation = []nativeNavigationGroup{
	{"准备与核对", []nativeNavigationItem{{"文件与解析", "", "file-text"}, {"要求确认", "/requirements", "list-checks"}, {"任务附件", "/attachments", "paperclip"}}},
	{"响应与审阅", []nativeNavigationItem{{"任务看板", "/board", "square-kanban"}, {"响应与证据", "/review", "message-square-text"}, {"初稿与导出", "/drafts", "file-output"}}},
	{"检查与评分", []nativeNavigationItem{{"检查", "/checks", "shield-check"}, {"评分规则", "/score-rubrics", "ruler"}, {"评分", "/scores", "gauge"}, {"标书检验", "/bid-submissions", "scan-search"}}},
	{"任务记录", []nativeNavigationItem{{"作业进度", "/progress", "activity"}, {"记忆反馈", "/memory-feedback", "brain"}}},
}

// nativeShellGroups are the installation-wide destinations outside tasks.
var nativeShellGroups = []nativeNavigationGroup{
	{"资料库", []nativeNavigationItem{{"产品库", "/org/products", "package"}, {"功能库", "/org/features", "puzzle"}, {"模板库", "/org/templates", "layout-template"}, {"单位资料与证照", "/org/profiles", "building-2"}, {"附件档案", "/org/attachments", "archive"}, {"单位记忆", "/org/memory", "brain-circuit"}}},
	{"设置与安全", []nativeNavigationItem{{"模型与服务", "/org/settings/models", "cpu"}, {"保密字段", "/org/confidential", "eye-off"}, {"服务凭据", "/platform/credentials", "key-round"}, {"Clef 初筛", "/platform/clef", "scan-search"}, {"信任根证书", "/platform/trust-anchors", "badge-check"}}},
}

func (a *NativeApp) extractionScope() string {
	switch p := a.page.(type) {
	case *taskBoardPage:
		return p.job
	case *requirementsPage:
		return p.job
	case *responsesPage:
		return p.job
	case *draftsPage:
		return p.job
	case *annotationPage:
		return p.jobID
	case *assessmentPage:
		return p.extraction
	case *assessmentReportPage:
		return str(p.report(), "extraction_job_id")
	}
	return a.Route.Query.Get("extraction")
}

func nativeTaskDestination(task, path, extraction string) string {
	base := "/org/tasks/" + task
	if task == "" || extraction == "" || path != base && !strings.HasPrefix(path, base+"/") {
		return path
	}
	key := "extraction"
	switch strings.TrimPrefix(path, base) {
	case "/board", "/requirements", "/review", "/drafts", "/checks", "/scores", "/score-rubrics", "/progress":
		key = "job"
	}
	return path + "?" + key + "=" + url.QueryEscape(extraction)
}

func (a *NativeApp) sidebar(c *ui.Context) {
	p := nativePalette(c)
	bar := c.TitleBar()
	task := a.Route.Params["taskId"]
	selected := a.Route.Path
	if task == "" {
		parts := strings.Split(strings.Trim(selected, "/"), "/")
		if len(parts) > 2 && parts[1] != "settings" {
			selected = "/" + parts[0] + "/" + parts[1]
		}
		if strings.HasPrefix(selected, "/org/certificates") {
			selected = "/org/profiles"
		}
	} else {
		base := "/org/tasks/" + task
		suffix := strings.TrimPrefix(selected, base)
		if strings.HasPrefix(suffix, "/cards/") {
			selected = base + "/review"
		}
		for _, part := range []string{"checks", "scores", "score-rubrics", "bid-submissions"} {
			if strings.HasPrefix(suffix, "/"+part+"/") {
				selected = base + "/" + part
			}
		}
		if strings.HasPrefix(suffix, "/bid-reviews/") {
			selected = base + "/bid-submissions"
		}
	}
	item := func(path, icon, label string) {
		row := ui.ButtonBase(c.Key("nav"+path)).FillWidth().Padding(7, 10).Gap(10).Radius(8).Justify(ui.Start).Label(label)
		active := path == selected
		color, iconColor := p.Text, p.Muted
		switch {
		case active:
			row.Background(p.AccentSoft)
			color, iconColor = p.AccentInk, p.AccentInk
		case row.Hovered():
			row.Background(p.SidebarHover)
		}
		row.Children(func() {
			ui.Icon(c, nativeIcon(icon)).FontSize(16).TextColor(iconColor)
			text := ui.Text(c, label).FontSize(13.5).TextColor(color).SingleLine().Grow(1)
			if active {
				text.FontWeight(600)
			}
		}).OnClick(func() { a.Navigate(nativeTaskDestination(task, path, a.extractionScope())) })
	}
	groupTitle := func(title string, open *bool) {
		if open == nil {
			ui.Text(c, title).FontSize(11.5).FontWeight(600).TextColor(p.Subtle).Padding(14, 10, 4, 10)
			return
		}
		toggle := ui.ButtonBase(c.Key("group"+title)).FillWidth().Padding(14, 10, 4, 10).Gap(4).Justify(ui.SpaceBetween).Label(title).Expanded(*open)
		color := p.Subtle
		if toggle.Hovered() {
			color = p.Muted
		}
		toggle.Children(func() {
			ui.Text(c, title).FontSize(11.5).FontWeight(600).TextColor(color)
			chevron := ui.Icon(c, nativeIcon("chevron-down")).FontSize(13).TextColor(color)
			if !*open {
				chevron.Rotate(-90)
			}
		}).OnClick(func() { *open = !*open })
	}
	box := ui.Column(c.Key(task)).Width(236).Shrink(0).FillHeight().Background(p.Sidebar).BorderWidth(0, 1, 0, 0).BorderColor(p.Border)
	isSettings := strings.HasPrefix(a.Route.Path, "/platform/") || a.Route.Path == "/org/settings/models" || a.Route.Path == "/org/confidential"
	library := ui.Local(box, "library", func() bool { return task == "" && !isSettings })
	settings := ui.Local(box, "settings", func() bool { return isSettings })
	box.Children(func() {
		ui.Row(c).FillWidth().Height(max(bar.Height, 14)).DragWindow()
		ui.Row(c).FillWidth().Padding(4, 16, 14, 16).Gap(10).AlignItems(ui.Center).DragWindow().Children(func() {
			nativeBrandMark(c, 30)
			ui.Column(c).Gap(1).Children(func() {
				ui.Text(c, "AI 标书").FontSize(15).FontWeight(650).TextColor(p.Text)
				ui.Text(c, "投标准备工作台").FontSize(11.5).TextColor(p.Muted)
			})
		})
		if a.Runner != nil {
			ui.Row(c).FillWidth().Padding(0, 12, 10, 12).Children(func() { a.workspaceSwitcher(c) })
		}
		ui.Scroll(c).Grow(1).FillWidth().MinHeight(0).Padding(0, 10, 12, 10).Gap(2).Children(func() {
			if task == "" {
				item("/org/tasks", "briefcase-business", "招标任务")
			} else {
				back := ui.ButtonBase(c.Key("back")).Padding(6, 8).Gap(6).Radius(8).AlignSelf(ui.Start).Label("返回全部任务")
				color := p.Muted
				if back.Hovered() {
					color = p.Text
				}
				back.Children(func() {
					ui.Icon(c, nativeIcon("arrow-left")).FontSize(14).TextColor(color)
					ui.Text(c, "全部任务").FontSize(12.5).TextColor(color)
				}).OnClick(func() { a.Navigate("/org/tasks") })
				base := "/org/tasks/" + task
				ui.Column(c).FillWidth().Margin(6, 0, 4, 0).Padding(12).Gap(8).Radius(10).Background(p.Card).Border(1, p.Border).Children(func() {
					ui.Text(c, "当前任务").FontSize(11).FontWeight(600).TextColor(p.Subtle)
					name := a.taskNames[task]
					if name == "" {
						name = "招标任务"
					}
					ui.Text(c, name).FontSize(13.5).FontWeight(600).TextColor(p.Text).MaxLines(2)
					if deadline, ok := a.taskDeadlines[task]; ok {
						if text, tone := nativeDeadline(deadline, time.Now()); text != "" {
							nativeBadge(c, text, tone)
						}
					}
				})
				for _, group := range nativeTaskNavigation {
					groupTitle(group.title, nil)
					for _, entry := range group.items {
						item(base+entry.path, entry.icon, entry.label)
					}
				}
			}
			for i, group := range nativeShellGroups {
				open := library
				if i == 1 {
					open = settings
				}
				groupTitle(group.title, open)
				if *open {
					for _, entry := range group.items {
						item(entry.path, entry.icon, entry.label)
					}
				}
			}
			if a.member() != nil {
				groupTitle("我的账号", nil)
				item("/account/sessions", "monitor-smartphone", "我的登录")
				item("/account/password", "key-round", "修改密码")
			}
		})
		ui.Row(c).FillWidth().Padding(10, 12).Gap(8).AlignItems(ui.Center).BorderWidth(1, 0, 0, 0).BorderColor(p.Border).Children(func() {
			if member := a.member(); member != nil {
				ui.Column(c).Grow(1).MinWidth(0).Gap(1).Children(func() {
					ui.Text(c, str(member, "display_name")).FontSize(13).FontWeight(600).SingleLine()
					role := "成员"
					if a.admin() {
						role = "管理员"
					}
					ui.Text(c, role).FontSize(11.5).TextColor(p.Muted)
				})
				nativeGhostButton(c, "log-out", "退出登录", a.Busy).Tooltip("结束本设备的登录").OnClick(a.signOut)
				return
			}
			nativeGhostButton(c, "lock", "锁定安装", a.Client == nil).Tooltip("清除会话，需重新输入访问密钥").OnClick(a.lock)
		})
	})
}
func tableRows(items []Object, keys ...string) [][]string {
	rows := make([][]string, len(items))
	for i, item := range items {
		rows[i] = make([]string, len(keys))
		for j, key := range keys {
			switch key {
			case "status", "state", "validity", "completion", "severity", "outcome", "review_domain", "mode", "category", "origin", "kind", "citation_mode", "model_redaction_enabled":
				rows[i][j] = nativeValue(item[key])
			default:
				rows[i][j] = str(item, key)
			}
		}
	}
	return rows
}
func nullable(value string) any {
	if value == "" {
		return nil
	}
	return value
}
func signedDownload(a *NativeApp, path, name string) {
	a.DownloadVerified(path, expectedDownloadPath(path), name, nil, nil)
}

type tasksPage struct {
	items                  []Object
	name, number, deadline string
	query                  string
	creating               bool
}

func (*tasksPage) Title() string { return "招标任务" }
func (*tasksPage) Cardless()     {}
func (p *tasksPage) Dirty() bool {
	return p.creating && (p.name != "" || p.number != "" || p.deadline != "")
}
func (p *tasksPage) Close() { p.name = ""; p.number = ""; p.deadline = "" }
func (p *tasksPage) Open(a *NativeApp) {
	a.Request("GET", "/tasks", nil, func(r Result) {
		p.items = r.Items
		for _, item := range r.Items {
			a.rememberTask(item)
		}
	})
}
func (p *tasksPage) View(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	now := time.Now()
	soon, closed := 0, 0
	for _, item := range p.items {
		if deadline, err := time.Parse(time.RFC3339, str(item, "deadline")); err == nil {
			if deadline.Before(now) {
				closed++
			} else if deadline.Sub(now) < 7*24*time.Hour {
				soon++
			}
		}
	}
	ui.Row(c).FillWidth().Wrap().Gap(14).Children(func() {
		for _, stat := range []struct {
			icon, label string
			value       int
			tone        string
		}{{"briefcase-business", "全部任务", len(p.items), toneAccent}, {"calendar-clock", "7 天内截止", soon, toneWarning}, {"clock", "已过截止时间", closed, toneNeutral}} {
			soft, ink, _ := nativeToneColors(palette, stat.tone)
			nativeCard(c.Key(stat.label)).Basis(200).Grow(1).Padding(16, 18).Children(func() {
				ui.Row(c).FillWidth().Gap(14).AlignItems(ui.Center).Children(func() {
					ui.Row(c).Size(40, 40).Shrink(0).Radius(10).Background(soft).Justify(ui.Center).AlignItems(ui.Center).Children(func() {
						ui.Icon(c, nativeIcon(stat.icon)).FontSize(19).TextColor(ink)
					})
					ui.Column(c).Gap(2).Children(func() {
						ui.Text(c, stat.label).FontSize(12.5).TextColor(palette.Muted)
						ui.Text(c, strconv.Itoa(stat.value)).FontSize(24).FontWeight(650).TextColor(palette.Text)
					})
				})
			})
		}
	})
	query := strings.ToLower(strings.TrimSpace(p.query))
	var shown []Object
	for _, item := range p.items {
		if query == "" || strings.Contains(strings.ToLower(str(item, "name")), query) || strings.Contains(strings.ToLower(str(item, "tender_number")), query) {
			shown = append(shown, item)
		}
	}
	nativeCard(c).Padding(0).Gap(0).Children(func() {
		ui.Row(c).FillWidth().Wrap().Padding(16, 20).Gap(12).AlignItems(ui.Center).BorderWidth(0, 0, 1, 0).BorderColor(palette.Border).Children(func() {
			ui.Column(c).Grow(1).Gap(2).Children(func() {
				ui.Text(c, "任务列表").FontSize(15.5).FontWeight(600)
				ui.Text(c, "上传招标文件，核对要求，逐条审阅响应与证据。").FontSize(12.5).TextColor(palette.Muted)
			})
			nativeSearch(c, &p.query, "搜索任务", "搜索名称或招标编号", nil).Width(260)
			nativeIconButton(c, "plus", "新建任务", true).OnClick(func() { p.creating = true })
		})
		if len(p.items) == 0 {
			ui.Column(c).FillWidth().Padding(20).Children(func() {
				nativeEmpty(c, "briefcase-business", "还没有招标任务。新建任务后，上传招标文件开始准备。", func() {
					nativeIconButton(c, "plus", "新建第一个任务", true).AlignSelf(ui.Center).OnClick(func() { p.creating = true })
				})
			})
			return
		}
		if len(shown) == 0 {
			ui.Column(c).FillWidth().Padding(20).Children(func() { nativeEmpty(c, "search", "没有名称或编号匹配「"+p.query+"」的任务。", nil) })
			return
		}
		// Narrow windows fold the tender number under the task name.
		width, _ := c.Size()
		compact := width < 1180
		ui.Row(c).FillWidth().Padding(10, 20).Gap(16).Background(palette.Canvas.Alpha(0.6)).BorderWidth(0, 0, 1, 0).BorderColor(palette.Border).Children(func() {
			for _, column := range []struct {
				title string
				width float32
			}{{"任务", 0}, {"招标编号", 150}, {"截止时间", 190}, {"模型外发遮挡", 104}, {"", 16}} {
				if compact && column.width == 150 {
					continue
				}
				header := ui.Text(c, column.title).FontSize(12).FontWeight(600).TextColor(palette.Subtle).SingleLine()
				if column.width == 0 {
					header.Grow(1).MinWidth(0)
				} else {
					header.Width(column.width).Shrink(0)
				}
			}
		})
		for i, item := range shown {
			row := ui.ButtonBase(c.Key(str(item, "id"))).FillWidth().Padding(14, 20).Gap(16).AlignItems(ui.Center).Justify(ui.Start).Label(str(item, "name"))
			if i < len(shown)-1 {
				row.BorderWidth(0, 0, 1, 0).BorderColor(palette.Border)
			}
			if row.Hovered() {
				row.Background(c.Theme().SurfaceHover)
			}
			row.Children(func() {
				number, numberColor := str(item, "tender_number"), palette.Muted
				if number == "" {
					number, numberColor = "未填写", palette.Subtle
				}
				ui.Column(c).Grow(1).MinWidth(0).Gap(3).Children(func() {
					ui.Text(c, str(item, "name")).FontSize(14.5).FontWeight(600).TextColor(palette.Text).MaxLines(2)
					if compact {
						ui.Text(c, "编号 "+number).FontSize(12).TextColor(numberColor).SingleLine()
					}
					if created, ok := nativeTime(str(item, "created_at")); ok {
						ui.Text(c, "创建于 "+created).FontSize(12).TextColor(palette.Subtle).SingleLine()
					}
				})
				if !compact {
					ui.Text(c, number).Width(150).Shrink(0).FontSize(13).TextColor(numberColor).SingleLine()
				}
				ui.Column(c).Width(190).Shrink(0).Gap(5).Children(func() {
					if local, ok := nativeTime(str(item, "deadline")); ok {
						ui.Text(c, local).FontSize(13).TextColor(palette.Text)
					}
					if text, tone := nativeDeadline(item["deadline"], now); text != "" {
						nativeBadge(c, text, tone)
					}
				})
				ui.Row(c).Width(104).Shrink(0).Children(func() {
					if item["model_redaction_enabled"] == true {
						nativeBadge(c, "已开启", toneSuccess)
					} else {
						nativeBadge(c, "未开启", toneNeutral)
					}
				})
				ui.Icon(c, nativeIcon("chevron-right")).FontSize(16).TextColor(palette.Subtle)
			}).OnClick(func() { a.Navigate("/org/tasks/" + str(item, "id")) })
		}
	})
	if ui.Modal(c, &p.creating, func() { p.createView(c, a) }).Width(480).Dismissed() && p.Dirty() {
		p.creating = true
	}
}
func (p *tasksPage) createView(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	ui.Column(c).FillWidth().Gap(16).Padding(6).Children(func() {
		ui.Column(c).Gap(4).Children(func() {
			ui.Text(c, "新建招标任务").FontSize(18).FontWeight(650)
			ui.Text(c, "创建后在任务里上传招标文件并解析。").FontSize(13).TextColor(palette.Muted)
		})
		nativeField(c, "任务名称", &p.name, false)
		nativeField(c, "招标编号（选填）", &p.number, false)
		nativeField(c, "截止时间（选填，使用本机时区）", &p.deadline, false)
		nativeHint(c, "按 年-月-日 时:分 填写，例如 2026-11-20 09:30。")
		if a.Error != "" {
			nativeCallout(c, toneDanger, a.Error)
		}
		ui.Row(c).FillWidth().Gap(8).Justify(ui.End).Padding(6, 0, 0, 0).Children(func() {
			nativeButton(c, "取消").OnClick(func() { p.Close(); p.creating = false })
			nativePrimaryButton(c, "创建任务").Disabled(a.Busy || strings.TrimSpace(p.name) == "").OnClick(func() {
				var deadline any
				if p.deadline != "" {
					layout := "2006-01-02 15:04"
					if strings.Contains(p.deadline, "T") {
						layout = "2006-01-02T15:04"
					}
					parsed, err := time.ParseInLocation(layout, p.deadline, time.Local)
					if err != nil {
						a.Error = "请输入有效的截止日期和时间"
						return
					}
					deadline = parsed.UTC().Format(time.RFC3339)
				}
				a.Request("POST", "/tasks", Object{"name": p.name, "tender_number": nullable(p.number), "deadline": deadline}, func(r Result) {
					p.creating = false
					a.rememberTask(r.Data)
					a.Navigate("/org/tasks/" + str(r.Data, "id"))
				})
			})
		})
	})
}

// nativeJobKinds names job kinds apart from the shared labels, where words
// such as "check" and "score" mean other things.
var nativeJobKinds = map[string]string{
	"parse": "原文解析", "extract": "要求抽取", "check": "合规检查", "score_rubric": "评分规则生成", "score": "评分",
	"annotation_render": "批注渲染", "annotation_release": "批注发布", "product_simulation": "模拟拟投",
	"screenshot_search": "截图检索", "prototype_generate": "原型生成", "provider_test": "模型连接测试",
}

type jobPanel struct {
	id         string
	data       Object
	cost       map[string]int64
	warnings   []string
	error      string
	cancel     context.CancelFunc
	generation uint64
	finished   func()
	completed  bool
}

func (j *jobPanel) Close() {
	j.generation++
	if j.cancel != nil {
		j.cancel()
	}
	j.data = nil
	j.warnings = nil
	j.error = ""
	j.completed = false
}
func (j *jobPanel) Load(a *NativeApp, id string, finished func()) {
	j.Close()
	j.id = id
	j.finished = finished
	j.refresh(a)
}
func (j *jobPanel) refresh(a *NativeApp) {
	if j.id == "" {
		return
	}
	if j.cancel != nil {
		j.cancel()
	}
	j.generation++
	generation, epoch, id := j.generation, a.epoch, j.id
	ctx, cancel := context.WithCancel(a.ctx)
	j.cancel = cancel
	go func() {
		failures := 0
		for {
			r, err := a.Client.Request(ctx, "GET", "/jobs/"+id, nil)
			if ctx.Err() != nil {
				return
			}
			terminal := err == nil && containsString([]string{"succeeded", "failed", "cancelled"}, str(r.Data, "status"))
			delay := 2 * time.Second
			retry := true
			if err != nil {
				failures++
				retry = failures <= 5
				var api *APIError
				if errors.As(err, &api) {
					retry = retry && (api.Status == 429 || api.Status >= 500)
					if seconds, parseErr := strconv.Atoi(api.RetryAfter); parseErr == nil {
						delay = time.Duration(seconds) * time.Second
					}
				}
				delay = max(delay, min(30*time.Second, 2*time.Second*time.Duration(1<<min(failures, 5))))
			} else {
				failures = 0
			}
			a.update(func() {
				if generation != j.generation || epoch != a.epoch || ctx.Err() != nil {
					return
				}
				if err != nil {
					j.error = err.Error()
					if a.handleError(err) {
						j.Close()
					}
					return
				}
				j.data = r.Data
				j.cost = r.Cost
				j.warnings = r.Warnings
				j.error = ""
				if terminal && !j.completed {
					j.completed = true
					if j.finished != nil {
						j.finished()
					}
				}
			})
			if terminal || !retry {
				return
			}
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
		}
	}()
}
func (j *jobPanel) View(c *ui.Context, a *NativeApp) {
	if j.id == "" {
		return
	}
	palette := nativePalette(c)
	status := str(j.data, "status")
	running := !containsString([]string{"succeeded", "failed", "cancelled"}, status)
	progress := object(j.data["progress"])
	if progress == nil {
		progress = object(object(j.data["result"])["progress"])
	}
	ui.Row(c).FillWidth().Wrap().Gap(10).AlignItems(ui.Center).Children(func() {
		ui.Icon(c, nativeIcon("activity")).FontSize(17).TextColor(palette.AccentInk)
		ui.Text(c, "作业进度").FontSize(15.5).FontWeight(600)
		if status != "" {
			nativeStatusBadge(c, nativeLabel(status))
		}
		ui.Row(c).Grow(1)
		nativeGhostButton(c, "refresh-cw", "刷新作业", false).OnClick(func() { j.refresh(a) })
		if running {
			nativeDangerButton(c, "取消作业").Disabled(a.Busy).OnClick(func() {
				a.Confirm("取消作业", "已发出的调用仍可能返回结果和用量。", func() { a.Request("POST", "/jobs/"+j.id+"/cancel", nil, func(Result) { j.refresh(a) }) })
			})
		}
	})
	if str(progress, "scheme") == "two_stage" && str(progress, "stage") == "items" && sha256Text(str(progress, "sections_sha256")) && integer(progress, "total_batches") > 0 {
		ui.Column(c).FillWidth().Gap(6).Children(func() {
			nativeHint(c, "已提交批次 "+str(progress, "completed_batches")+" / "+str(progress, "total_batches"))
			ui.Progress(c, float64(integer(progress, "completed_batches"))/float64(integer(progress, "total_batches"))).FillWidth()
		})
	} else if running {
		ui.Progress(c, -1).FillWidth()
	}
	kind := str(j.data, "kind")
	if label, ok := nativeJobKinds[kind]; ok {
		kind = label
	}
	labels := []string{"作业", "类型", "状态", "尝试次数"}
	values := []string{nativeShortID(j.id), nativeLabel(kind), nativeLabel(status), str(j.data, "attempts")}
	if stage := str(progress, "stage"); stage != "" {
		labels, values = append(labels, "阶段"), append(values, nativeLabel(stage))
	}
	if reasoning := str(j.data, "reasoning"); reasoning != "" {
		labels, values = append(labels, "推理档位"), append(values, reasoning)
	}
	nativePairs(c, labels, values)
	if j.cost != nil {
		usage := []string{fmt.Sprintf("模型调用已完成 %d 次", j.cost["calls"])}
		if pending := j.cost["unresolved_calls"]; pending > 0 {
			// Calls without a recorded outcome are still in flight while the job runs.
			if running {
				usage = append(usage, fmt.Sprintf("进行中 %d 次", pending))
			} else {
				usage = append(usage, fmt.Sprintf("结果未确定 %d 次", pending))
			}
		}
		usage = append(usage, fmt.Sprintf("tokens %d", j.cost["llm_tokens"]))
		for _, part := range []struct {
			key, format string
		}{{"ocr_pages", "OCR %d 页"}, {"search_requests", "搜索 %d 次"}, {"browser_units", "浏览器 %d"}} {
			if j.cost[part.key] > 0 {
				usage = append(usage, fmt.Sprintf(part.format, j.cost[part.key]))
			}
		}
		nativeHint(c, strings.Join(usage, " · "))
	}
	if running && status != "" {
		nativeHint(c, "作业在后台运行，可以离开本页；完成后结果会自动刷新。")
	}
	if message := str(object(j.data["error"]), "message"); message != "" {
		nativeCallout(c, toneDanger, message)
	}
	if j.error != "" {
		nativeCallout(c, toneDanger, j.error)
	}
	for _, warning := range j.warnings {
		nativeCallout(c, toneWarning, nativeWarning(warning))
	}
	result := object(j.data["result"])
	if str(result, "completion") == "partial" {
		nativeCallout(c, toneWarning, "部分完成；请查看已保存报告中的未完成内容")
	}
	if str(result, "stop_reason") != "" {
		nativeText(c, "停止原因："+str(result, "stop_reason"))
	}
}
