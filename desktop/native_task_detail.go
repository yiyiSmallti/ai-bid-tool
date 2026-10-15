package main

import (
	"context"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"
)

type taskDetailPage struct {
	id                                                  string
	task                                                Object
	documents, history, exports, jobs                   []Object
	historyRows, exportRows                             [][]string
	selected, upload, reasoning, simJob, selectedExport string
	preview, simulation, simulationResult               Object
	levels                                              []string
	serial                                              uint64
	job                                                 jobPanel
	document                                            documentPanel
	exportPreview                                       exportPreviewPanel
	confidential                                        NativePage
	models                                              modelPicker
}

func (p *taskDetailPage) Title() string {
	if name := str(p.task, "name"); name != "" {
		return name
	}
	return "任务详情"
}
func (*taskDetailPage) Cardless()     {}
func (p *taskDetailPage) Dirty() bool { return p.confidential != nil && p.confidential.Dirty() }
func (p *taskDetailPage) PrivacyBlur() {
	if panel, ok := p.confidential.(interface{ PrivacyBlur() }); ok {
		panel.PrivacyBlur()
	}
}
func (p *taskDetailPage) Close() {
	p.serial++
	p.document.Close()
	p.job.Close()
	p.exportPreview.Close()
	if p.confidential != nil {
		p.confidential.Close()
	}
	p.confidential = nil
}
func (p *taskDetailPage) Open(a *NativeApp) {
	p.confidential = NewConfidentialPanel(p.id)
	p.confidential.Open(a)
	p.models.loaded = false
	p.models.load(a)
	p.load(a)
}
func (p *taskDetailPage) load(a *NativeApp) {
	id := p.id
	a.Do(func(ctx context.Context) (func(), error) {
		paths := []string{"/tasks/" + id, "/tasks/" + id + "/documents", "/tasks/" + id + "/extractions", "/tasks/" + id + "/jobs?kind=parse", "/tasks/" + id + "/exports"}
		results := make([]Result, len(paths))
		for i, path := range paths {
			result, err := a.Client.Request(ctx, "GET", path, nil)
			if err != nil {
				return nil, err
			}
			results[i] = result
		}
		return func() {
			p.task = results[0].Data
			a.rememberTask(p.task)
			p.documents = results[1].Items
			p.history = results[2].Items
			p.jobs = results[3].Items
			p.exports = results[4].Items
			p.historyRows = nil
			for _, entry := range p.history {
				reason := "—"
				if str(entry, "status") == "failed" {
					reason = nativeJobError(object(entry["error"]))
				}
				p.historyRows = append(p.historyRows, []string{nativeDisplay(str(entry, "created_at")), nativeDisplay(nativeLabel(str(entry, "status"))), nativeDisplay(str(entry, "reasoning")), nativeDisplay(str(entry, "model")), nativeDisplay(str(entry, "saved")), nativeDisplay(str(entry, "rejected")), reason})
			}
			p.exportRows = nil
			for _, item := range p.exports {
				p.exportRows = append(p.exportRows, []string{str(object(item["file"]), "name"), nativeLabel(str(item, "mode")), nativeLabel(str(item, "validity")), str(item, "released_at")})
			}
			if p.selected == "" && len(p.documents) > 0 {
				p.selected = str(p.documents[0], "id")
			}
		}, nil
	})
}
func (p *taskDetailPage) run(a *NativeApp, kind string, dry, retry bool) {
	body := Object{"dry_run": dry, "retry": retry}
	if kind == "extract" && p.reasoning != "" {
		body["reasoning"] = p.reasoning
	}
	if kind == "extract" {
		p.models.set(body)
	}
	p.serial++
	serial := p.serial
	document := p.selected
	a.Request("POST", "/documents/"+document+"/"+kind, body, func(r Result) {
		if serial != p.serial || document != p.selected {
			return
		}
		if dry {
			p.preview = r.Data
			p.levels = []string{""}
			for _, level := range objects(r.Data["reasoning_levels"]) {
				p.levels = append(p.levels, str(level, "name"))
			}
		} else {
			p.preview = nil
			p.job.Load(a, str(r.Data, "job_id"), func() { p.load(a) })
		}
	})
}
func (p *taskDetailPage) View(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	p.summaryView(c, a)
	p.stepsView(c, a)
	if p.job.id != "" {
		nativeCard(c.Key("job")).Children(func() { p.job.View(c, a) })
	}
	nativeSection(c, "file-text", "招标文件", "上传 PDF 或 DOCX 招标文件，解析后才能抽取要求。", func() {
		if p.upload != "" {
			ui.Row(c).Gap(6).AlignItems(ui.Center).MaxWidth(260).Children(func() {
				ui.Icon(c, nativeIcon("file-up")).FontSize(14).TextColor(palette.Muted)
				ui.Text(c, filepath.Base(p.upload)).FontSize(12.5).TextColor(palette.Muted).SingleLine()
			})
		}
		nativeIconButton(c, "folder-open", "选择 PDF / DOCX", false).OnClick(func() { a.PickFiles(false, func(paths []string) { p.upload = paths[0] }) })
		nativeIconButton(c, "upload", "上传文件", true).Disabled(a.Busy || p.upload == "").OnClick(func() {
			a.Upload("/tasks/"+p.id+"/documents", nil, []UploadFile{{Field: "file", Path: p.upload}}, func(r Result) { p.serial++; p.selected = str(r.Data, "id"); p.upload = ""; p.preview = nil; p.load(a) })
		})
	}, func() {
		if len(p.documents) == 0 {
			nativeEmpty(c, "file-up", "还没有招标文件。选择 PDF 或 DOCX 后上传。", nil)
			return
		}
		ui.Column(c).FillWidth().Gap(8).Children(func() {
			for _, document := range p.documents {
				p.documentRow(c, document)
			}
		})
		if p.selected == "" {
			return
		}
		ui.Row(c).FillWidth().Wrap().Gap(8).AlignItems(ui.Center).Padding(4, 0, 0, 0).Children(func() {
			nativeIconButton(c, "play", "开始解析", false).Disabled(a.Busy).OnClick(func() { p.run(a, "parse", false, false) })
			nativeIconButton(c, "eye", "预览原文", false).OnClick(func() { p.document.Open(a, p.selected, 1, "") })
			nativeIconButton(c, "download", "下载招标原件", false).OnClick(func() { signedDownload(a, "/documents/"+p.selected+"/download-link", "tender") })
			ui.Row(c).Grow(1)
			nativeGhostButton(c, "rotate-ccw", "显式重试解析", a.Busy).Tooltip("忽略已有结果重新解析当前文档").OnClick(func() { p.run(a, "parse", false, true) })
		})
		if len(p.jobs) > 0 {
			ui.Row(c).FillWidth().Wrap().Gap(6).AlignItems(ui.Center).Children(func() {
				ui.Text(c, "解析作业").FontSize(12.5).TextColor(palette.Muted).Margin(0, 4, 0, 0)
				for _, job := range p.jobs {
					detail := nativeDisplay(str(job, "finished_at"))
					if str(job, "finished_at") == "" {
						detail = nativeShortID(str(job, "id"))
					}
					label := nativeLabel(str(job, "status")) + " · " + detail
					chip := ui.ButtonBase(c.Key(str(job, "id"))).Padding(4, 10).Gap(6).Radius(999).Border(1, palette.Border).AlignItems(ui.Center).Label("查看解析作业 · " + nativeLabel(str(job, "status")) + " · " + str(job, "id"))
					if chip.Hovered() {
						chip.Background(palette.SidebarHover)
					}
					chip.Children(func() {
						_, _, solid := nativeToneColors(palette, nativeLabelTones[nativeLabel(str(job, "status"))])
						ui.Box(c).Size(6, 6).Radius(3).Background(solid)
						ui.Text(c, label).FontSize(12).TextColor(palette.Text).SingleLine()
					}).OnClick(func() { p.job.Load(a, str(job, "id"), func() { p.load(a) }) })
				}
			})
		}
		p.parseSummaryView(c, a)
		if p.document.opened {
			ui.Column(c).FillWidth().Gap(12).Padding(16).Radius(10).Background(palette.Canvas).Border(1, palette.Border).Children(func() { p.document.View(c, a) })
		}
	})
	nativeSection(c, "sparkles", "要求抽取", "先预检范围与推理档位，再提交抽取。预检不会调用模型。", nil, func() {
		if p.selected == "" {
			nativeCallout(c, toneNeutral, "先上传并选择一份招标文件。")
		} else {
			ui.Row(c).FillWidth().Wrap().Gap(8).AlignItems(ui.End).Children(func() {
				// Reasoning levels belong to the chosen model; preflight again to list them.
				if p.models.View(c) {
					p.serial++
					p.preview, p.reasoning, p.levels = nil, "", nil
				}
				if len(p.levels) > 0 {
					ui.Column(c).Gap(6).Children(func() {
						ui.Text(c, "官方推理档位").FontSize(12.5).FontWeight(500).TextColor(palette.Muted)
						labels := map[string]string{"": "使用模型默认档位"}
						for _, level := range p.levels {
							if level != "" {
								labels[level] = level
							}
						}
						if nativeChoice(c, &p.reasoning, p.levels, labels).Label("官方推理档位").MinWidth(180).Changed() {
							p.serial++
							p.preview = nil
						}
					})
				}
				ready := p.preview != nil && p.preview["parsed"] == true
				nativeIconButton(c, "scan-search", "抽取预检", false).Disabled(a.Busy).OnClick(func() { p.run(a, "extract", true, false) })
				nativeIconButton(c, "wand-sparkles", "开始抽取", true).Disabled(a.Busy || !ready).OnClick(func() { p.run(a, "extract", false, false) })
				nativeGhostButton(c, "rotate-ccw", "显式重试抽取", a.Busy || !ready).OnClick(func() { p.run(a, "extract", false, true) })
			})
			if p.preview == nil {
				nativeHint(c, "请先点击「抽取预检」，通过后才能开始抽取或显式重试。切换文档或推理档位后需重新预检。")
			}
			if p.preview != nil {
				if p.preview["parsed"] != true {
					nativeCallout(c, toneWarning, "当前文档尚未解析完成，解析成功后才能开始抽取。")
				}
				nativePairs(c, []string{"文档已解析", "推理档位", "模型"}, []string{nativeValue(p.preview["parsed"]), nativeDisplay(str(p.preview, "reasoning")), nativeDisplay(str(p.preview, "model"))})
			}
		}
		ui.Text(c, "抽取历史").FontSize(13.5).FontWeight(600).TextColor(palette.Text).Margin(6, 0, 0, 0)
		nativeHint(c, "成功记录打开要求确认，失败记录查看作业详情；完成预检后，可点击「显式重试抽取」重新提交。")
		nativeTable(c, []string{"抽取记录", "状态", "档位", "模型", "保存", "拒绝", "失败原因"}, p.historyRows, func(i int) {
			entry := p.history[i]
			if str(entry, "status") == "succeeded" {
				a.Navigate("/org/tasks/" + p.id + "/requirements?job=" + str(entry, "job_id"))
			} else {
				p.job.Load(a, str(entry, "job_id"), func() { p.load(a) })
			}
		})
		// Only the newest run matters; an older failure was superseded by a later run.
		if len(p.history) > 0 && str(p.history[0], "status") == "failed" {
			entry := p.history[0]
			nativeCallout(c, toneDanger, nativeDisplay(str(entry, "created_at"))+" 抽取失败："+nativeJobError(object(entry["error"]))+"。完成预检后，可点击「显式重试抽取」重新运行。")
		}
	})
	nativeSection(c, "file-output", "导出文件", "仅当前有效的导出可以下载或在线预览。", nil, func() {
		nativeTable(c, []string{"文件", "类型", "有效状态", "发布时间"}, p.exportRows, func(i int) { p.selectedExport = str(p.exports[i], "id"); p.exportPreview.Close() })
		for _, item := range p.exports {
			if str(item, "id") != p.selectedExport {
				continue
			}
			current := str(item, "validity") == "current"
			ui.Row(c).FillWidth().Wrap().Gap(8).AlignItems(ui.Center).Children(func() {
				ui.Icon(c, nativeIcon("file-check")).FontSize(16).TextColor(palette.Muted)
				ui.Text(c, "所选文件："+str(object(item["file"]), "name")).FontSize(13.5).Grow(1).MinWidth(0)
				nativeIconButton(c, "download", "下载导出文件", false).Disabled(a.Busy || !current).OnClick(func() {
					signedDownload(a, "/exports/"+str(item, "id")+"/download-link", str(object(item["file"]), "name"))
				})
				nativeIconButton(c, "eye", "在线预览导出文件", false).Disabled(a.Busy || !current).OnClick(func() { p.exportPreview.Open(a, str(item, "id"), str(object(item["file"]), "name")) })
			})
			if !current {
				nativeCallout(c, toneWarning, "该导出已失效，请重新生成并发布。")
			}
		}
		if p.exportPreview.opened {
			ui.Column(c).FillWidth().Gap(12).Padding(16).Radius(10).Background(palette.Canvas).Border(1, palette.Border).Children(func() { p.exportPreview.View(c, a) })
		}
	})
	if p.confidential != nil {
		nativeSection(c, "shield", "报价与保密信息", "敏感值加密保存；模型只接收占位符，导出时填入。", func() {
			nativeGhostButton(c, "refresh-cw", "重新读取保密字段", a.Busy).OnClick(func() { panel := p.confidential.(*confidentialPanel); panel.list.Previous = nil; panel.load(a, "") })
		}, func() { p.confidential.View(c, a) })
	}
	nativeDisclosure(c, "模拟拟投（演示）", false, func() { p.simulationView(c, a) })
}

// summaryView shows the task's facts and the libraries it draws from.
func (p *taskDetailPage) summaryView(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	nativeCard(c.Key("summary")).Padding(0).Gap(0).Children(func() {
		ui.Row(c).FillWidth().Wrap().Gap(0).Children(func() {
			fact := func(icon, label string, body func()) {
				ui.Row(c.Key(label)).Basis(220).Grow(1).Padding(16, 20).Gap(12).AlignItems(ui.Center).Children(func() {
					ui.Row(c).Size(36, 36).Shrink(0).Radius(9).Background(palette.NeutralSoft).Justify(ui.Center).AlignItems(ui.Center).Children(func() {
						ui.Icon(c, nativeIcon(icon)).FontSize(17).TextColor(palette.Muted)
					})
					ui.Column(c).Gap(4).MinWidth(0).Children(func() {
						ui.Text(c, label).FontSize(12).TextColor(palette.Muted)
						body()
					})
				})
			}
			fact("hash", "招标编号", func() {
				number, color := str(p.task, "tender_number"), palette.Text
				if number == "" {
					number, color = "未填写", palette.Subtle
				}
				ui.Text(c, number).FontSize(14.5).FontWeight(600).TextColor(color).Selectable().SingleLine()
			})
			fact("calendar-clock", "截止时间", func() {
				ui.Row(c).Gap(8).AlignItems(ui.Center).Wrap().Children(func() {
					if local, ok := nativeTime(str(p.task, "deadline")); ok {
						ui.Text(c, local).FontSize(14.5).FontWeight(600).TextColor(palette.Text)
					}
					if text, tone := nativeDeadline(p.task["deadline"], time.Now()); text != "" {
						nativeBadge(c, text, tone)
					}
				})
			})
			fact("shield", "模型外发遮挡", func() {
				if p.task["model_redaction_enabled"] == true {
					nativeBadge(c, "已开启", toneSuccess)
				} else {
					nativeBadge(c, "未开启", toneNeutral)
				}
			})
		})
		ui.Row(c).FillWidth().Wrap().Gap(8).Padding(12, 20).AlignItems(ui.Center).Background(palette.Canvas.Alpha(0.6)).BorderWidth(1, 0, 0, 0).BorderColor(palette.Border).Children(func() {
			ui.Text(c, "投标资料").FontSize(12.5).FontWeight(600).TextColor(palette.Muted).Margin(0, 6, 0, 0)
			for _, item := range []struct{ label, path, icon string }{{"选产品", "products", "package"}, {"选功能", "features", "puzzle"}, {"选模板", "templates", "layout-template"}, {"选单位资料与证照", "profiles", "building-2"}} {
				nativeIconButton(c.Key(item.path), item.icon, item.label, false).MinHeight(30).Padding(4, 12).OnClick(func() { a.Navigate("/org/" + item.path + "?task=" + p.id) })
			}
		})
	})
}

// stepsView is the task's preparation flow. The first three steps are done
// when the loaded records show it; the later ones open their pages.
func (p *taskDetailPage) stepsView(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	parsed, current := 0, 0
	for _, document := range p.documents {
		if str(document, "status") == "parsed" {
			parsed++
		}
	}
	for _, item := range p.exports {
		if str(item, "validity") == "current" {
			current++
		}
	}
	var latest Object
	for _, entry := range p.history {
		if str(entry, "status") == "succeeded" {
			latest = entry
			break
		}
	}
	base := "/org/tasks/" + p.id
	scope := a.extractionScope()
	requirements := nativeTaskDestination(p.id, base+"/requirements", scope)
	if latest != nil {
		requirements = base + "/requirements?job=" + str(latest, "job_id")
	}
	type step struct {
		title, detail, path string
		done                bool
	}
	steps := []step{
		{"上传招标文件", "尚未上传", "", len(p.documents) > 0},
		{"解析原文", "待解析", "", parsed > 0},
		{"抽取要求", "待抽取", "", latest != nil},
		{"确认要求", "逐条核对抽取结果", requirements, false},
		{"响应与证据", "起草响应并挂接证据", nativeTaskDestination(p.id, base+"/review", scope), false},
		{"初稿与导出", "生成初稿并发布", nativeTaskDestination(p.id, base+"/drafts", scope), current > 0},
	}
	if len(p.documents) > 0 {
		steps[0].detail = fmt.Sprintf("已上传 %d 份", len(p.documents))
	}
	if parsed > 0 {
		steps[1].detail = fmt.Sprintf("已解析 %d 份", parsed)
	}
	if latest != nil {
		steps[2].detail = "最近保存 " + str(latest, "saved") + " 条要求"
	}
	if len(p.history) > 0 && containsString([]string{"queued", "running"}, str(p.history[0], "status")) {
		steps[2].detail = "抽取进行中"
	}
	if current > 0 {
		steps[5].detail = fmt.Sprintf("已发布 %d 份", current)
	}
	next := len(steps)
	for i, s := range steps {
		if !s.done {
			next = i
			break
		}
	}
	nativeCard(c.Key("steps")).Padding(18, 20).Children(func() {
		ui.Row(c).FillWidth().Gap(8).AlignItems(ui.Center).Children(func() {
			ui.Text(c, "准备流程").FontSize(15.5).FontWeight(600).Grow(1)
			if next < len(steps) {
				ui.Text(c, "下一步："+steps[next].title).FontSize(12.5).TextColor(palette.AccentInk)
			}
		})
		ui.Row(c).FillWidth().Wrap().Gap(10).AlignItems(ui.Stretch).Children(func() {
			for i, s := range steps {
				box := ui.ButtonBase(c.Key(s.title)).Basis(140).Grow(1).Padding(12, 14).Gap(10).Radius(10).AlignItems(ui.Start).Justify(ui.Start).Label(s.title).Border(1, palette.Border)
				if s.path == "" {
					box.Cursor(ui.CursorDefault)
				} else if box.Hovered() {
					box.Background(palette.SidebarHover)
				}
				if i == next {
					box.Background(palette.AccentSoft).Border(1, palette.Accent.Alpha(0.35))
				}
				box.Children(func() {
					circle := ui.Row(c).Size(26, 26).Shrink(0).Radius(13).Justify(ui.Center).AlignItems(ui.Center)
					switch {
					case s.done:
						circle.Background(palette.SuccessSoft).Children(func() { ui.Icon(c, nativeIcon("check")).FontSize(14).TextColor(palette.SuccessInk) })
					case i == next:
						circle.Background(palette.Accent).Children(func() { ui.Text(c, strconv.Itoa(i+1)).FontSize(12.5).FontWeight(650).TextColor(ui.Hex("#FFFFFF")) })
					default:
						circle.Background(palette.NeutralSoft).Children(func() { ui.Text(c, strconv.Itoa(i+1)).FontSize(12.5).FontWeight(600).TextColor(palette.Muted) })
					}
					ui.Column(c).Grow(1).MinWidth(0).Gap(2).Children(func() {
						ui.Row(c).FillWidth().Gap(4).AlignItems(ui.Center).Children(func() {
							ui.Text(c, s.title).FontSize(13.5).FontWeight(600).TextColor(palette.Text).SingleLine().Grow(1)
							if s.path != "" {
								ui.Icon(c, nativeIcon("chevron-right")).FontSize(13).TextColor(palette.Subtle)
							}
						})
						ui.Text(c, s.detail).FontSize(12).TextColor(palette.Muted).MaxLines(2)
					})
				}).OnClick(func() {
					if s.path != "" {
						a.Navigate(s.path)
					}
				})
			}
		})
	})
}

// documentRow is one tender file; clicking it makes it the current file.
func (p *taskDetailPage) documentRow(c *ui.Context, document Object) {
	palette := nativePalette(c)
	id := str(document, "id")
	row := ui.ButtonBase(c.Key(id)).FillWidth().Padding(10, 14).Gap(12).Radius(10).AlignItems(ui.Center).Justify(ui.Start).Label(str(document, "name")).Border(1, palette.Border)
	selected := id == p.selected
	switch {
	case selected:
		row.Background(palette.AccentSoft).Border(1, palette.Accent.Alpha(0.45))
	case row.Hovered():
		row.Background(palette.SidebarHover)
	}
	row.Children(func() {
		ui.Row(c).Size(34, 34).Shrink(0).Radius(8).Background(palette.Card).Border(1, palette.Border).Justify(ui.Center).AlignItems(ui.Center).Children(func() {
			ui.Icon(c, nativeIcon("file-text")).FontSize(16).TextColor(palette.AccentInk)
		})
		ui.Column(c).Grow(1).MinWidth(0).Gap(2).Children(func() {
			ui.Text(c, str(document, "name")).FontSize(13.5).FontWeight(600).TextColor(palette.Text).SingleLine()
			var detail []string
			if selected {
				detail = append(detail, "当前文档")
			}
			if pages := str(document, "page_count"); pages != "" {
				detail = append(detail, pages+" 页")
			}
			if mode := str(document, "citation_mode"); mode != "" {
				detail = append(detail, nativeLabel(mode))
			}
			if len(detail) > 0 {
				ui.Text(c, strings.Join(detail, " · ")).FontSize(12).TextColor(palette.Muted).SingleLine()
			}
		})
		ui.Row(c).Shrink(0).Children(func() { nativeStatusBadge(c, nativeLabel(str(document, "status"))) })
	}).OnClick(func() {
		p.serial++
		p.selected = id
		p.preview = nil
		p.reasoning = ""
		p.document.Close()
	})
}
func (p *taskDetailPage) simulationView(c *ui.Context, a *NativeApp) {
	nativeHeading(c, "模拟拟投（演示）")
	nativeText(c, "模型提出硬件候选并搜索真实官网。模拟材料不能进入正式件。")
	options := []string{""}
	for _, entry := range p.history {
		if str(entry, "status") == "succeeded" {
			options = append(options, str(entry, "job_id"))
		}
	}
	if nativeChoice(c, &p.simJob, options).Label("模拟抽取记录").Changed() {
		p.serial++
		p.simulation = nil
		p.simulationResult = nil
	}
	nativeButton(c, "模拟预检").Disabled(a.Busy || p.simJob == "").OnClick(func() {
		serial := p.serial
		a.Request("POST", "/tasks/"+p.id+"/product-simulations", Object{"extraction_job_id": p.simJob, "dry_run": true}, func(r Result) {
			if serial == p.serial {
				p.simulation = r.Data
			}
		})
	})
	if p.simulation != nil {
		nativeText(c, "阻塞原因："+str(p.simulation, "admission_blocker"))
		nativeText(c, "涉及技术要求 "+str(p.simulation, "requirement_count")+" 条")
		for _, item := range objects(p.simulation["items"]) {
			nativeText(c, str(item, "name")+" / 要求 "+str(item, "requirements"))
		}
		nativeButton(c, "开始模拟拟投").Disabled(a.Busy || str(p.simulation, "admission_blocker") != "" || len(objects(p.simulation["items"])) == 0).OnClick(func() {
			a.Confirm("开始模拟拟投", "将调用已配置模型和搜索服务，录入模拟材料并固定到本任务；模拟材料不能用于正式件。", func() {
				a.Request("POST", "/tasks/"+p.id+"/product-simulations", Object{"extraction_job_id": p.simJob, "expected_input_hash": p.simulation["input_hash"], "retry": true}, func(r Result) {
					p.job.Load(a, str(r.Data, "job_id"), func() {
						if str(p.job.data, "status") == "succeeded" {
							p.simulationResult = object(p.job.data["result"])
						}
						p.load(a)
					})
				})
			})
		})
	}
	if p.simulationResult != nil {
		for _, item := range objects(p.simulationResult["items"]) {
			nativeHeading(c, str(item, "name"))
			nativeText(c, nativeLabel(str(item, "kind"))+" / "+str(item, "vendor")+" "+str(item, "model")+" / "+nativeLabel(str(item, "status")))
			nativeText(c, fmt.Sprintf("参数 %d 条 / 来源 %s", len(objects(item["parameters"])), str(item, "url")))
			if str(item, "source") == "search_extract" {
				nativeText(c, "搜索摘录：官网页面无法直接抓取，参数取自搜索服务对该页面的摘录")
			}
			for _, attempt := range objects(item["tried"]) {
				nativeText(c, str(attempt, "vendor")+" / "+nativeLabel(str(attempt, "result"))+" / "+str(attempt, "url"))
			}
		}
		counts := object(p.simulationResult["counts"])
		nativeText(c, "已录入 "+str(counts, "products")+" 个模拟产品、"+str(counts, "parameters")+" 条参数；可到响应审阅运行模型起草。")
	}
}

// parseSummaryView states what the latest successful parse of the selected
// document produced, so the result is visible without opening a job.
func (p *taskDetailPage) parseSummaryView(c *ui.Context, a *NativeApp) {
	var parse Object
	for _, job := range p.jobs {
		if str(job, "status") == "succeeded" && str(job, "document_id") == p.selected {
			parse = job
			break
		}
	}
	if parse == nil {
		return
	}
	palette := nativePalette(c)
	result := object(parse["result"])
	detail := "已解析 " + str(result, "pages") + " 页"
	if integer(result, "pages") == 0 {
		detail = "已解析 " + str(result, "sections") + " 节"
	}
	if ocr := integer(object(result["cost"]), "ocr_pages"); ocr > 0 {
		detail += fmt.Sprintf("，%d 页经 OCR 识别", ocr)
	}
	warnings := []string{}
	for _, warning := range anyList(result["warnings"]) {
		if text, ok := warning.(string); ok {
			warnings = append(warnings, nativeParseWarning(text))
		}
	}
	ui.Column(c.Key("parse-summary")).FillWidth().Gap(10).Padding(12, 14).Radius(10).Background(palette.SuccessSoft).Border(1, palette.Success.Alpha(0.25)).Children(func() {
		ui.Row(c).FillWidth().Gap(10).AlignItems(ui.Center).Children(func() {
			ui.Icon(c, nativeIcon("circle-check")).FontSize(18).TextColor(palette.Success)
			ui.Column(c).Grow(1).MinWidth(0).Gap(2).Children(func() {
				ui.Text(c, "解析完成 · "+detail).FontSize(13.5).FontWeight(600).TextColor(palette.SuccessInk)
				ui.Text(c, "完成于 "+nativeDisplay(str(parse, "finished_at"))+"。可逐节核对解析出的段落和表格，再进行要求抽取。").FontSize(12.5).TextColor(palette.Muted)
			})
			if !p.document.opened {
				nativeIconButton(c, "scroll-text", "查看解析结果", true).Shrink(0).OnClick(func() { p.document.Open(a, p.selected, 1, "") })
			}
		})
		for _, warning := range warnings {
			ui.Row(c).FillWidth().Gap(8).AlignItems(ui.Start).Padding(0, 0, 0, 28).Children(func() {
				ui.Icon(c, nativeIcon("triangle-alert")).FontSize(14).TextColor(palette.Warning).Margin(2, 0, 0, 0)
				ui.Text(c, warning).Grow(1).MinWidth(0).FontSize(12.5).LineHeight(1.45).TextColor(palette.WarningInk)
			})
		}
	})
}
