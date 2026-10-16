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

// memberGrants are the grants an administrator may give a member, in display
// order.
var memberGrants = []struct{ code, label string }{
	{"library:edit", "编辑资料库"},
	{"memory:edit", "编辑单位记忆"},
	{"confidential:reveal", "查看保密字段"},
}

var memberRoles = map[string]string{"admin": "管理员", "member": "成员"}

var memberLogin = regexp.MustCompile(`^[a-z0-9._-]{3,64}$`)

// memberFailure turns a refused member or project change into what the user
// can do about it. A refused change keeps the page open.
func memberFailure(err error) error {
	var api *APIError
	if !errors.As(err, &api) {
		return err
	}
	switch api.Code {
	case "last_admin":
		return errors.New("至少需要保留一名启用的管理员")
	case "member_login_taken":
		return errors.New("登录名已被使用")
	case "member_disabled":
		return errors.New("已停用的成员不能加入或负责项目")
	case "owner_not_removable":
		return errors.New("请先把项目交给其他负责人，再移出原负责人")
	case "project_member_required":
		return errors.New("新负责人必须先是项目成员")
	case "project_owner_required":
		return errors.New("只有项目负责人或管理员可以管理项目成员")
	case "task_owner_required":
		return errors.New("项目必须有且只有一名负责人")
	}
	return err
}

// heldGrants reads a member view's grants.
func heldGrants(item Object) map[string]bool {
	held := map[string]bool{}
	list, _ := item["grants"].([]any)
	for _, grant := range list {
		if code, ok := grant.(string); ok {
			held[code] = true
		}
	}
	return held
}

func memberGrantLabels(item Object) string {
	held := heldGrants(item)
	labels := []string{}
	for _, grant := range memberGrants {
		if held[grant.code] {
			labels = append(labels, grant.label)
		}
	}
	return strings.Join(labels, "、")
}

// oneTimePassword is an initial password the gateway returns once. It lives
// only until the administrator dismisses it or leaves the page.
type oneTimePassword struct{ login, name, password string }

// memberEditor is the editable state of one member.
type memberEditor struct {
	name, role, quota string
	grants            map[string]bool
	disabled          bool
}

func newMemberEditor(item Object, usage Object) memberEditor {
	e := memberEditor{name: str(item, "display_name"), role: str(item, "role"), grants: heldGrants(item), disabled: str(item, "status") == "disabled"}
	if cap, ok := usage["monthly_company_tokens"].(float64); ok {
		e.quota = strconv.FormatInt(int64(cap), 10)
	}
	return e
}

func (e memberEditor) grantList() []string {
	list := []string{}
	for _, grant := range memberGrants {
		if e.grants[grant.code] {
			list = append(list, grant.code)
		}
	}
	return list
}

func (e memberEditor) same(other memberEditor) bool {
	return e.name == other.name && e.role == other.role && strings.TrimSpace(e.quota) == strings.TrimSpace(other.quota) && e.disabled == other.disabled && slices.Equal(e.grantList(), other.grantList())
}

// quotaValue is nil for no cap, or the cap; ok is false for text that is not
// a whole number of tokens.
func quotaValue(text string) (any, bool) {
	text = strings.TrimSpace(text)
	if text == "" {
		return nil, true
	}
	n, err := strconv.ParseInt(text, 10, 64)
	return n, err == nil && n >= 0 && n <= 1_000_000_000_000_000
}

// membersPage is the administrator's list of company members: create one,
// change their role, grants, status and monthly company-model quota, reset a
// password, or sign them out everywhere.
type membersPage struct {
	items    []Object
	selected string
	usage    Object
	loaded   memberEditor
	edit     memberEditor

	creating                   bool
	newLogin, newName, newRole string
	newGrants                  map[string]bool

	oneTime *oneTimePassword
}

func newMembersPage() *membersPage {
	return &membersPage{newRole: "member", newGrants: map[string]bool{}}
}

func (*membersPage) Title() string { return "成员与权限" }
func (p *membersPage) Dirty() bool {
	return p.oneTime != nil || p.selected != "" && !p.edit.same(p.loaded) || p.creating && (p.newLogin != "" || p.newName != "")
}
func (p *membersPage) Close() { *p = *newMembersPage() }
func (p *membersPage) Open(a *NativeApp) {
	a.Request("GET", "/members", nil, func(r Result) {
		p.items = r.Items
		if p.selected != "" && p.find(p.selected) == nil {
			p.selected = ""
		}
	})
}

func (p *membersPage) find(id string) Object {
	for _, item := range p.items {
		if str(item, "id") == id {
			return item
		}
	}
	return nil
}

// choose opens a member in the editor with this month's usage and quota.
func (p *membersPage) choose(a *NativeApp, id string) {
	open := func() {
		item := p.find(id)
		if item == nil {
			return
		}
		a.Request("GET", "/members/"+id+"/usage", nil, func(r Result) {
			p.selected, p.usage = id, r.Data
			p.loaded = newMemberEditor(item, r.Data)
			p.edit = newMemberEditor(item, r.Data)
		})
	}
	if p.selected != "" && p.selected != id && !p.edit.same(p.loaded) {
		a.Confirm("切换成员", "当前成员尚有未保存的更改，切换将丢弃更改。", open)
		return
	}
	open()
}

func (p *membersPage) self(a *NativeApp, id string) bool { return str(a.member(), "id") == id }

func (p *membersPage) View(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	nativeHint(c, "管理本公司的成员账号。新成员和被重置密码的成员首次登录时需要设置自己的密码。")
	if p.oneTime != nil {
		p.passwordNotice(c)
	}
	p.createSection(c, a)
	if len(p.items) == 0 {
		return
	}
	items := slices.Clone(p.items)
	ui.Column(c).FillWidth().Gap(8).Children(func() {
		for _, item := range items {
			id := str(item, "id")
			row := ui.ButtonBase(c.Key(id)).FillWidth().Gap(12).Padding(12, 14).Radius(10).Border(1, palette.Border).AlignItems(ui.Center).Justify(ui.Start).Label(str(item, "display_name"))
			if id == p.selected {
				row.Background(palette.AccentSoft).BorderColor(palette.Accent.Alpha(0.5))
			} else if row.Hovered() {
				row.Background(c.Theme().SurfaceHover)
			}
			row.Children(func() {
				ui.Icon(c, nativeIcon("user-round")).FontSize(16).TextColor(palette.Muted)
				ui.Column(c).Grow(1).MinWidth(0).Gap(3).Children(func() {
					ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
						ui.Text(c, str(item, "display_name")).FontSize(13.5).FontWeight(600).SingleLine()
						ui.Text(c, str(item, "login")).FontSize(12.5).TextColor(palette.Muted).SingleLine()
						if p.self(a, id) {
							nativeBadge(c, "本人", toneAccent)
						}
					})
					grants := memberGrantLabels(item)
					if grants == "" {
						grants = "无额外授权"
					}
					ui.Text(c, grants).FontSize(12).TextColor(palette.Muted).SingleLine()
				})
				nativeBadge(c, memberRoles[str(item, "role")], toneNeutral)
				if str(item, "status") == "disabled" {
					nativeBadge(c, "已停用", toneDanger)
				} else {
					nativeBadge(c, "启用", toneSuccess)
				}
			}).OnClick(func() { p.choose(a, id) })
		}
	})
	if item := p.find(p.selected); item != nil {
		p.editor(c, a, item)
	}
}

func (p *membersPage) passwordNotice(c *ui.Context) {
	palette := nativePalette(c)
	once := p.oneTime
	ui.Column(c).FillWidth().Gap(10).Padding(14, 16).Radius(10).Background(palette.AccentSoft).Border(1, palette.Accent.Alpha(0.4)).Children(func() {
		ui.Text(c, once.name+"（"+once.login+"）的初始密码").FontSize(13.5).FontWeight(600).TextColor(palette.AccentInk)
		ui.Text(c, once.password).FontSize(18).FontWeight(600).TextColor(palette.Text).Selectable()
		nativeHint(c, "只显示这一次，企业服务不保存明文。请当面或通过可信渠道交给本人；首次登录后需要改成自己的密码。")
		nativeButton(c, "已交给本人").OnClick(func() { p.oneTime = nil })
	})
}

func (p *membersPage) createSection(c *ui.Context, a *NativeApp) {
	if !p.creating {
		nativeIconButton(c, "user-plus", "新建成员", true).Disabled(a.Busy).OnClick(func() { p.creating = true })
		return
	}
	nativeSection(c, "user-plus", "新建成员", "创建后显示一次初始密码。", nil, func() {
		nativeField(c, "登录名", &p.newLogin, false)
		if p.newLogin != "" && !memberLogin.MatchString(p.newLogin) {
			nativeHint(c, "登录名为 3 到 64 位小写字母、数字、点、下划线或连字符。")
		}
		nativeField(c, "姓名", &p.newName, false)
		memberRoleChoice(c, &p.newRole)
		memberGrantChoices(c, p.newGrants, p.newRole == "admin")
		valid := memberLogin.MatchString(p.newLogin) && strings.TrimSpace(p.newName) != ""
		nativeActions(c).Children(func() {
			nativeIconButton(c, "user-plus", "创建成员", true).Disabled(a.Busy || !valid).OnClick(func() { p.create(a) })
			nativeButton(c, "取消").Disabled(a.Busy).OnClick(func() {
				p.creating, p.newLogin, p.newName, p.newRole, p.newGrants = false, "", "", "member", map[string]bool{}
			})
		})
	})
}

func memberRoleChoice(c *ui.Context, role *string) {
	ui.Column(c.Key("role")).FillWidth().MaxWidth(760).AlignSelf(ui.Start).Gap(6).Children(func() {
		ui.Text(c, "角色").FontSize(12.5).TextColor(c.Theme().TextMuted)
		nativeChoice(c, role, []string{"member", "admin"}, memberRoles).Label("角色").MinWidth(200)
	})
}

// memberGrantChoices edits grants; an administrator holds every permission,
// so their grants are moot and shown as such.
func memberGrantChoices(c *ui.Context, grants map[string]bool, admin bool) {
	ui.Column(c.Key("grants")).FillWidth().Gap(8).Children(func() {
		ui.Text(c, "授权").FontSize(12.5).TextColor(c.Theme().TextMuted)
		if admin {
			nativeHint(c, "管理员拥有全部权限，无需单独授权。")
			return
		}
		ui.Row(c).FillWidth().Wrap().Gap(16).Children(func() {
			for _, grant := range memberGrants {
				held := grants[grant.code]
				if ui.Checkbox(c.Key(grant.code), &held, grant.label).Changed() {
					grants[grant.code] = held
				}
			}
		})
	})
}

func (p *membersPage) create(a *NativeApp) {
	login, name := p.newLogin, strings.TrimSpace(p.newName)
	body := Object{"login": login, "display_name": name, "role": p.newRole, "grants": memberEditor{grants: p.newGrants}.grantList()}
	a.Do(func(ctx context.Context) (func(), error) {
		r, err := a.Client.Request(ctx, "POST", "/members", body)
		if err != nil {
			return nil, memberFailure(err)
		}
		return func() {
			p.creating, p.newLogin, p.newName, p.newRole, p.newGrants = false, "", "", "member", map[string]bool{}
			p.oneTime = &oneTimePassword{login: login, name: name, password: str(r.Data, "initial_password")}
			p.Open(a)
		}, nil
	})
}

func (p *membersPage) editor(c *ui.Context, a *NativeApp, item Object) {
	id := str(item, "id")
	self := p.self(a, id)
	nativeSection(c, "user-cog", str(item, "display_name"), "登录名 "+str(item, "login"), nil, func() {
		nativeField(c, "姓名", &p.edit.name, false)
		memberRoleChoice(c, &p.edit.role)
		memberGrantChoices(c, p.edit.grants, p.edit.role == "admin")
		if self {
			nativeHint(c, "不能停用自己的账号；需要时请由另一名管理员操作。")
		} else {
			ui.Checkbox(c, &p.edit.disabled, "停用该成员（立即退出其全部登录）")
		}
		nativeField(c, "每月公司模型 token 上限", &p.edit.quota, false)
		used := integer(p.usage, "company_tokens")
		usage := "本月（UTC）已用公司模型 " + strconv.Itoa(used) + " tokens；留空表示不限。个人模型不计入上限。"
		nativeHint(c, usage)
		_, quotaOK := quotaValue(p.edit.quota)
		if !quotaOK {
			nativeCallout(c, toneWarning, "上限需为不小于 0 的整数")
		}
		changed := !p.edit.same(p.loaded)
		valid := strings.TrimSpace(p.edit.name) != "" && quotaOK
		nativeActions(c).Children(func() {
			nativeIconButton(c, "check", "保存", true).Disabled(a.Busy || !changed || !valid).OnClick(func() { p.save(a, id) })
			if changed {
				nativeButton(c, "放弃更改").Disabled(a.Busy).OnClick(func() { p.edit = p.loaded })
			}
			ui.Row(c).Grow(1)
			if !self {
				nativeButton(c, "重置密码").Disabled(a.Busy).OnClick(func() { p.resetPassword(a, item) })
				nativeDangerButton(c, "退出全部登录").Disabled(a.Busy).OnClick(func() { p.revokeSessions(a, item) })
			}
		})
	})
}

func (p *membersPage) save(a *NativeApp, id string) {
	edit, loaded := p.edit, p.loaded
	body := Object{"display_name": strings.TrimSpace(edit.name), "role": edit.role, "grants": edit.grantList()}
	if edit.disabled != loaded.disabled {
		body["status"] = map[bool]string{true: "disabled", false: "active"}[edit.disabled]
	}
	quota, _ := quotaValue(edit.quota)
	quotaChanged := strings.TrimSpace(edit.quota) != strings.TrimSpace(loaded.quota)
	self := p.self(a, id)
	workspace := *a.Workspace
	runner := a.Runner
	a.Do(func(ctx context.Context) (func(), error) {
		if _, err := a.Client.Request(ctx, "PATCH", "/members/"+id, body); err != nil {
			return nil, memberFailure(err)
		}
		if quotaChanged {
			if _, err := a.Client.Request(ctx, "PUT", "/members/"+id+"/quota", Object{"monthly_company_tokens": quota}); err != nil {
				return nil, memberFailure(err)
			}
		}
		// The administrator's own role is part of their session.
		if self && runner != nil {
			if err := runner.Resume(ctx, &workspace); err != nil {
				return nil, err
			}
		}
		return func() {
			if self {
				*a.Workspace = workspace
				if !a.admin() {
					a.navigate("/org/tasks")
					a.Notice = "你已不是管理员"
					return
				}
			}
			p.loaded = p.edit
			a.Notice = "成员已保存"
			p.Open(a)
		}, nil
	})
}

func (p *membersPage) resetPassword(a *NativeApp, item Object) {
	id, login, name := str(item, "id"), str(item, "login"), str(item, "display_name")
	a.Confirm("重置密码", "将为 "+name+" 生成新的初始密码，并退出其全部登录。", func() {
		a.Do(func(ctx context.Context) (func(), error) {
			r, err := a.Client.Request(ctx, "POST", "/members/"+id+"/password-reset", nil)
			if err != nil {
				return nil, memberFailure(err)
			}
			return func() {
				p.oneTime = &oneTimePassword{login: login, name: name, password: str(r.Data, "initial_password")}
			}, nil
		})
	})
}

func (p *membersPage) revokeSessions(a *NativeApp, item Object) {
	id, name := str(item, "id"), str(item, "display_name")
	a.Confirm("退出全部登录", name+" 的全部设备需要重新登录。", func() {
		a.Request("DELETE", "/members/"+id+"/sessions", nil, func(r Result) {
			a.Notice = name + " 已退出 " + strconv.Itoa(integer(r.Data, "revoked_sessions")) + " 处登录"
		})
	})
}

// projectMembersPage lists a tender task's members and its owner. The owner or
// an administrator removes members and hands the project over; adding members
// needs the member list, which only administrators read.
type projectMembersPage struct {
	task      string
	items     []Object
	directory []Object
	add       string
	// exportable is the project's export_to_personal_allowed, which only an
	// administrator changes.
	exportable, sharingLoaded bool
}

func (*projectMembersPage) Title() string { return "项目成员" }
func (*projectMembersPage) Dirty() bool   { return false }
func (p *projectMembersPage) Close() {
	p.items, p.directory, p.add, p.exportable, p.sharingLoaded = nil, nil, "", false, false
}
func (p *projectMembersPage) Open(a *NativeApp) {
	a.Request("GET", "/tasks/"+p.task+"/members", nil, func(r Result) { p.items = r.Items })
	if a.admin() {
		a.Request("GET", "/members", nil, func(r Result) { p.directory = r.Items })
		a.Request("GET", "/tasks/"+p.task, nil, func(r Result) {
			p.exportable, p.sharingLoaded = r.Data["export_to_personal_allowed"] == true, true
		})
	}
}

// share sets whether the project may leave the gateway as a package.
func (p *projectMembersPage) share(a *NativeApp, allowed bool) {
	a.Request("PATCH", "/tasks/"+p.task+"/sharing", Object{"export_to_personal_allowed": allowed}, func(r Result) {
		p.exportable = r.Data["export_to_personal_allowed"] == true
		a.Notice = "已禁止导出到个人版"
		if p.exportable {
			a.Notice = "已允许导出到个人版"
		}
	})
}

// manages is whether the signed-in member may change this project's members.
func (p *projectMembersPage) manages(a *NativeApp) bool {
	if a.admin() {
		return true
	}
	self := str(a.member(), "id")
	for _, item := range p.items {
		if str(item, "member_id") == self {
			return str(item, "role") == "owner"
		}
	}
	return false
}

// change runs one project change and shows the members it leaves.
func (p *projectMembersPage) change(a *NativeApp, method, path string, body any, notice string) {
	a.Do(func(ctx context.Context) (func(), error) {
		if _, err := a.Client.Request(ctx, method, path, body); err != nil {
			return nil, memberFailure(err)
		}
		return func() {
			a.Notice = notice
			p.add = ""
			p.Open(a)
		}, nil
	})
}

func (p *projectMembersPage) View(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	manages := p.manages(a)
	nativeHint(c, "只有项目成员能看到并处理本任务。负责人和管理员可以调整成员和负责人。")
	if manages && a.admin() {
		present := map[string]bool{}
		for _, item := range p.items {
			present[str(item, "member_id")] = true
		}
		options := []string{""}
		names := map[string]string{"": "选择要加入的成员"}
		for _, member := range p.directory {
			id := str(member, "id")
			if !present[id] && str(member, "status") == "active" {
				options = append(options, id)
				names[id] = str(member, "display_name") + "（" + str(member, "login") + "）"
			}
		}
		ui.Row(c).FillWidth().Gap(8).AlignItems(ui.Center).Children(func() {
			nativeChoice(c, &p.add, options, names).Label("选择要加入的成员").MinWidth(280)
			nativeIconButton(c, "user-plus", "加入项目", true).Disabled(a.Busy || p.add == "").OnClick(func() {
				name := names[p.add]
				p.change(a, "POST", "/tasks/"+p.task+"/members", Object{"member_ids": []string{p.add}}, name+" 已加入项目")
			})
		})
	} else if manages {
		nativeHint(c, "成员名单只对管理员开放；需要加入新成员时，请联系管理员。")
	}
	if a.admin() && p.sharingLoaded {
		allowed := p.exportable
		if ui.Checkbox(c.Key("sharing"), &allowed, "允许项目成员导出到个人版").Disabled(a.Busy).Changed() {
			p.share(a, allowed)
		}
	}
	if len(p.items) == 0 {
		return
	}
	items := slices.Clone(p.items)
	ui.Column(c).FillWidth().Gap(8).Children(func() {
		for _, item := range items {
			id, name := str(item, "member_id"), str(item, "display_name")
			owner := str(item, "role") == "owner"
			ui.Row(c.Key(id)).FillWidth().Gap(12).Padding(12, 14).Radius(10).Border(1, palette.Border).AlignItems(ui.Center).Children(func() {
				ui.Icon(c, nativeIcon("user-round")).FontSize(16).TextColor(palette.Muted)
				ui.Column(c).Grow(1).MinWidth(0).Gap(3).Children(func() {
					ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
						ui.Text(c, name).FontSize(13.5).FontWeight(600).SingleLine()
						ui.Text(c, str(item, "login")).FontSize(12.5).TextColor(palette.Muted).SingleLine()
						if id == str(a.member(), "id") {
							nativeBadge(c, "本人", toneAccent)
						}
					})
					added, _ := nativeTime(str(item, "added_at"))
					ui.Text(c, "加入于 "+added).FontSize(12).TextColor(palette.Muted).SingleLine()
				})
				if str(item, "status") == "disabled" {
					nativeBadge(c, "已停用", toneDanger)
				}
				if owner {
					nativeBadge(c, "负责人", toneSuccess)
				} else if manages {
					nativeButton(c, "设为负责人").Disabled(a.Busy || str(item, "status") == "disabled").OnClick(func() {
						a.Confirm("移交负责人", "将由 "+name+" 负责本项目，原负责人保留为项目成员。", func() {
							p.change(a, "POST", "/tasks/"+p.task+"/owner", Object{"member_id": id}, name+" 已成为负责人")
						})
					})
					nativeDangerButton(c, "移出项目").Disabled(a.Busy).OnClick(func() {
						a.Confirm("移出项目", name+" 将不能再看到本任务。", func() {
							p.change(a, "DELETE", "/tasks/"+p.task+"/members/"+id, nil, name+" 已移出项目")
						})
					})
				}
			})
		}
	})
}
