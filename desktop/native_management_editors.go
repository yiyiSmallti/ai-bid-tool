package main

import (
	"fmt"
	"net/url"
	"regexp"
	"strings"

	"github.com/egoist/mygo/ui"
)

type managementProductPicker struct {
	search, selectedID, selectedName string
	list                             managementListing
	open                             bool
}

func (p *managementProductPicker) load(a *NativeApp, reads *managementReads, channel, cursor string, optional bool, denied func(error)) {
	state := "active"
	if optional {
		state = "all"
	}
	p.list.Items, p.list.Rows, p.list.Meta = nil, nil, nil
	reads.call(a, channel, "POST", "/management/resources/products/query", Object{"q": mgNull(p.search), "state": state, "cursor": mgNull(cursor), "limit": 25}, func(r Result) {
		for _, row := range r.Items {
			if str(object(row["ref"]), "kind") != "products" || str(object(row["ref"]), "resource_id") == "" || integer(row, "revision") < 1 || str(row, "name") == "" {
				a.Error = "产品候选范围不符合契约"
				return
			}
		}
		if len(r.Items) > 25 || !p.list.install(r, cursor, func(row Object) []string {
			return []string{str(row, "name"), fmt.Sprintf("第 %d 版", integer(row, "revision")), mgStatus(str(object(row["lifecycle"]), "state"))}
		}) {
			a.Error = "产品候选分页范围不符合契约"
		}
	}, func(err error) {
		p.list.Previous = nil
		if mgDenied(err) {
			denied(err)
		}
	})
}

func (p *managementProductPicker) filter(c *ui.Context, a *NativeApp, reads *managementReads, channel string, selected *string, changed func(), denied func(error)) {
	label := "按所属产品筛选"
	title := "全部产品"
	if *selected != "" {
		title = nativeShortID(*selected)
		if p.selectedID == *selected {
			title = p.selectedName
		}
		for _, row := range p.list.Items {
			if str(object(row["ref"]), "resource_id") == *selected {
				title = str(row, "name")
				break
			}
		}
	}
	palette := nativePalette(c)
	trigger := nativeButton(c.Key(channel), "").Label(label).Tooltip(title).Width(180).Height(36).Padding(8, 12).Gap(10).AlignItems(ui.Center).Disabled(a.Busy)
	trigger.Children(func() {
		ui.Text(c, "产品："+title).SingleLine().Grow(1).MinWidth(0).FontSize(13.5)
		ui.Icon(c, nativeIcon("chevron-down")).FontSize(14).Shrink(0).TextColor(palette.Muted)
	}).OnClick(func() { p.open = !p.open })
	ui.PopoverBase(c.Key(channel+"-popup"), trigger, &p.open, func(panel ui.Element) {
		panel.Width(480).Padding(14).Gap(10).Margin(4, 0, 0, 0).Radius(10).Background(palette.Card).Border(1, palette.Border).Shadow(0, 8, 24, 0, palette.Shadow.Alpha(0.16))
		query := func() { p.list.Previous = nil; p.load(a, reads, channel, "", true, denied) }
		nativeSearch(c, &p.search, label+"搜索", "搜索产品名称，按 Enter 查询", query).FillWidth()
		ui.Row(c).Wrap().Gap(8).Children(func() {
			nativeButton(c, "读取产品候选").Disabled(a.Busy).OnClick(query)
			nativeButton(c, "全部产品").Disabled(a.Busy || *selected == "").OnClick(func() {
				*selected = ""
				p.selectedID, p.selectedName, p.open = "", "", false
				changed()
			})
			if *selected != "" {
				nativeButton(c, "查看所选产品").Disabled(a.Busy).OnClick(func() { a.Navigate("/org/products/" + url.PathEscape(*selected)) })
			}
		})
		if len(p.list.Items) > 0 {
			ui.Scroll(c.Key("product-candidates")).FillWidth().Height(float32(min(300, len(p.list.Items)*88))).Gap(4).Children(func() {
				for index, row := range p.list.Items {
					id, name := str(object(row["ref"]), "resource_id"), str(row, "name")
					title := fmt.Sprintf("%s · 修订 %d · %s", name, integer(row, "revision"), mgStatus(str(object(row["lifecycle"]), "state")))
					button := ui.ButtonBase(c.Key(fmt.Sprintf("product-%d", index))).Label(title).FillWidth().Padding(10).Gap(4).Radius(6).Disabled(a.Busy)
					if id == *selected {
						button.Background(palette.AccentSoft)
					} else if button.Hovered() {
						button.Background(c.Theme().SurfaceHover)
					}
					button.Children(func() {
						ui.Column(c).FillWidth().Gap(4).Children(func() {
							ui.Text(c, name).FontSize(13.5).FontWeight(500)
							ui.Text(c, fmt.Sprintf("修订 %d · %s", integer(row, "revision"), mgStatus(str(object(row["lifecycle"]), "state")))).FontSize(12).TextColor(palette.Muted)
						})
					}).OnClick(func() {
						*selected = id
						p.selectedID, p.selectedName, p.open = id, name, false
						changed()
					})
				}
			})
		} else if p.list.Meta != nil {
			nativeHint(c, "没有符合条件的产品")
		}
		p.list.controls(c, a, label+"候选", func(cursor string) { p.load(a, reads, channel, cursor, true, denied) })
	})
}

func (p *managementProductPicker) view(c *ui.Context, a *NativeApp, reads *managementReads, channel string, selected *string, optional bool, changed func(), denied func(error)) {
	label := "关联产品"
	if optional {
		label = "按所属产品筛选"
	}
	ui.Column(c.Key(channel)).Gap(6).Children(func() {
		nativeHeading(c, label)
		if *selected != "" {
			mgValue(c, "当前所选产品", *selected)
			nativeButton(c, "查看所选产品").Disabled(a.Busy).OnClick(func() { a.Navigate("/org/products/" + url.PathEscape(*selected)) })
		} else if optional {
			nativeText(c, "全部产品")
		} else {
			nativeText(c, "请选择可用产品；已有的产品关联不会自动替换。")
		}
		nativeField(c, label+"搜索", &p.search, false)
		ui.Row(c).Wrap().Gap(8).Children(func() {
			nativeButton(c, "读取产品候选").Disabled(a.Busy).OnClick(func() { p.list.Previous = nil; p.load(a, reads, channel, "", optional, denied) })
			if optional {
				nativeButton(c, "全部产品").Disabled(a.Busy || *selected == "").OnClick(func() { *selected = ""; changed() })
			}
		})
		for index, row := range p.list.Items {
			id := str(object(row["ref"]), "resource_id")
			title := fmt.Sprintf("%s · 修订 %d · %s", str(row, "name"), integer(row, "revision"), mgStatus(str(object(row["lifecycle"]), "state")))
			nativeButton(c.Key(fmt.Sprintf("product-%d", index)), title).Disabled(a.Busy || !optional && str(object(row["lifecycle"]), "state") != "active").OnClick(func() { *selected = id; changed() })
		}
		if p.list.Meta != nil && len(p.list.Items) == 0 {
			nativeText(c, "没有符合条件的产品")
		}
		p.list.controls(c, a, label+"候选", func(cursor string) { p.load(a, reads, channel, cursor, optional, denied) })
	})
}

var managementSecretPattern = regexp.MustCompile(`\{\{secret\.([a-z][a-z0-9_]{1,47})\}\}`)

type managementSecretToken struct{ Key string }
type managementSecretRange struct{ start, end int }
type managementSecretPart struct{ text, key, label string }
type managementSecretEditorState struct {
	canonical, display, previous, error string
	labels                              map[string]string
	parts                               []managementSecretPart
}

func mgSecretParts(value string, labels map[string]string) []managementSecretPart {
	matches := managementSecretPattern.FindAllStringSubmatchIndex(value, -1)
	parts := make([]managementSecretPart, 0, len(matches)*2+1)
	previous := 0
	for _, match := range matches {
		if match[0] > previous {
			parts = append(parts, managementSecretPart{text: value[previous:match[0]]})
		}
		key := value[match[2]:match[3]]
		label := labels[key]
		if label == "" {
			label = "未登记：" + key
		}
		parts = append(parts, managementSecretPart{key: key, label: label})
		previous = match[1]
	}
	if previous < len(value) {
		parts = append(parts, managementSecretPart{text: value[previous:]})
	}
	return parts
}

func (s *managementSecretEditorState) hydrate(value string, fields []Object) {
	changed := s.canonical != value || s.labels == nil || len(s.labels) != len(fields)
	if !changed {
		for _, field := range fields {
			if s.labels[str(field, "key")] != str(field, "label") {
				changed = true
				break
			}
		}
	}
	if !changed {
		return
	}
	s.labels = make(map[string]string, len(fields))
	for _, field := range fields {
		s.labels[str(field, "key")] = str(field, "label")
	}
	s.canonical = value
	s.parts = mgSecretParts(value, s.labels)
	var display strings.Builder
	for _, part := range s.parts {
		if part.key != "" {
			display.WriteString(mgSecretLabel(part.label, part.key))
		} else {
			display.WriteString(part.text)
		}
	}
	s.display = display.String()
	s.previous = s.display
}

func mgSecretLabel(label, key string) string { return "⟦" + label + " · " + key + "⟧" }

func (s *managementSecretEditorState) ranges() []managementSecretRange {
	var ranges []managementSecretRange
	cursor := 0
	for _, part := range s.parts {
		text := part.text
		if part.key != "" {
			text = mgSecretLabel(part.label, part.key)
		}
		length := len([]rune(text))
		if part.key != "" {
			ranges = append(ranges, managementSecretRange{cursor, cursor + length})
		}
		cursor += length
	}
	return ranges
}

func (s *managementSecretEditorState) expand(start, end int) (int, int) {
	for _, part := range s.ranges() {
		if start > part.start && start < part.end {
			start = part.start
		}
		if end > part.start && end < part.end {
			end = part.end
		}
	}
	return start, end
}

func mgSecretLength(value string) int {
	length := 0
	for _, r := range value {
		length++
		if r > 0xffff {
			length++
		}
	}
	return length
}

func (s *managementSecretEditorState) accept(display string, value *string, maximum int, dirty *bool) bool {
	canonical := display
	for key, label := range s.labels {
		canonical = strings.ReplaceAll(canonical, mgSecretLabel(label, key), "{{secret."+key+"}}")
	}
	for _, part := range s.parts {
		if part.key != "" {
			canonical = strings.ReplaceAll(canonical, mgSecretLabel(part.label, part.key), "{{secret."+part.key+"}}")
		}
	}
	if mgSecretLength(canonical) > maximum {
		s.display = s.previous
		s.error = fmt.Sprintf("正文最多 %d 字符，保密字段标签按其完整占位符计数。", maximum)
		return false
	}
	s.error = ""
	*value = canonical
	s.canonical = "\x00"
	fields := make([]Object, 0, len(s.labels))
	for key, label := range s.labels {
		fields = append(fields, Object{"key": key, "label": label})
	}
	s.hydrate(canonical, fields)
	*dirty = true
	return true
}

func mgSecretEditor(c *ui.Context, label string, value *string, fields []Object, dirty *bool) {
	state := ui.Local(ui.Text(c, label).FontSize(13).FontWeight(500).TextColor(c.Theme().TextMuted), "confidential-placeholder-editor", func() managementSecretEditorState { return managementSecretEditorState{} })
	state.hydrate(*value, fields)
	maximum := 20000
	if label == "登记信息" || label == "注册信息" {
		maximum = 10000
	}
	editor := ui.TextArea(c, &state.display).Label(label + "，保密字段显示为标签").Height(126).FillWidth().Background(c.Theme().Background)
	if editor.Changed() {
		before, after := []rune(state.previous), []rune(state.display)
		start := 0
		for start < len(before) && start < len(after) && before[start] == after[start] {
			start++
		}
		oldEnd, newEnd := len(before), len(after)
		for oldEnd > start && newEnd > start && before[oldEnd-1] == after[newEnd-1] {
			oldEnd--
			newEnd--
		}
		expandedStart, expandedEnd := state.expand(start, oldEnd)
		changed := string(before[:expandedStart]) + string(after[start:newEnd]) + string(before[expandedEnd:])
		state.accept(changed, value, maximum, dirty)
	}
	insert := func(key string) {
		label, registered := state.labels[key]
		if !registered {
			return
		}
		start, end := editor.TextSelection()
		runes := []rune(state.display)
		start = max(0, min(start, len(runes)))
		end = max(start, min(end, len(runes)))
		start, end = state.expand(start, end)
		token := mgSecretLabel(label, key)
		if state.accept(string(runes[:start])+token+string(runes[end:]), value, maximum, dirty) {
			caret := start + len([]rune(token))
			editor.Focus().SetTextSelection(caret, caret)
		}
	}
	if token, dropped := ui.Drop[managementSecretToken](editor); dropped {
		insert(token.Key)
	}
	nativeHint(c, "点击或拖入字段标签，仅保存引用；真实值在导出时解析。")
	for _, field := range fields {
		key, label := str(field, "key"), str(field, "label")
		nativeButton(c, "插入「"+label+"」").Drag(managementSecretToken{Key: key}).OnClick(func() { insert(key) })
	}
	mgSecretPartView(c, state.parts)
	if state.error != "" {
		ui.Text(c, state.error).TextColor(c.Theme().Danger)
	}
}

func mgSecretPartView(c *ui.Context, parts []managementSecretPart) {
	for index, part := range parts {
		if part.key != "" {
			ui.Text(c.Key(fmt.Sprintf("secret-chip-%d", index)), part.label).Padding(3, 8).Radius(5).Background(c.Theme().Selection).TextColor(c.Theme().Text).Label("保密字段：" + part.label)
		} else if part.text != "" {
			nativeText(c, part.text)
		}
	}
}

func mgSecretRead(c *ui.Context, label, value string, fields []Object) {
	state := ui.Local(ui.Text(c, label).FontSize(13), "confidential-placeholder-read", func() managementSecretEditorState { return managementSecretEditorState{} })
	state.hydrate(value, fields)
	mgSecretPartView(c, state.parts)
}
