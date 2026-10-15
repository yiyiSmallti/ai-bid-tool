package main

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"
)

// nativeColors extends the MyGo theme with the canvas, card and tone colors
// the console layout needs. Soft fills carry tone badges and callouts; ink
// is text on them.
type nativeColors struct {
	Canvas, Card, Sidebar, SidebarHover, Border, BorderStrong ui.Color
	Text, Muted, Subtle                                       ui.Color
	Accent, AccentSoft, AccentInk                             ui.Color
	Success, SuccessSoft, SuccessInk                          ui.Color
	Warning, WarningSoft, WarningInk                          ui.Color
	Danger, DangerSoft, DangerInk                             ui.Color
	NeutralSoft, NeutralInk                                   ui.Color
	Shadow                                                    ui.Color
}

var nativeLight = nativeColors{
	Canvas: ui.Hex("#F5F6F8"), Card: ui.Hex("#FFFFFF"), Sidebar: ui.Hex("#EDF0F4"), SidebarHover: ui.Hex("#E2E6EC"),
	Border: ui.Hex("#E2E5EA"), BorderStrong: ui.Hex("#CDD3DC"),
	Text: ui.Hex("#151A24"), Muted: ui.Hex("#5A6374"), Subtle: ui.Hex("#8A93A3"),
	Accent: ui.Hex("#2F5BD8"), AccentSoft: ui.Hex("#EAF0FD"), AccentInk: ui.Hex("#2448B5"),
	Success: ui.Hex("#1E8E4F"), SuccessSoft: ui.Hex("#E6F5EC"), SuccessInk: ui.Hex("#17703E"),
	Warning: ui.Hex("#C27A0E"), WarningSoft: ui.Hex("#FCF2DF"), WarningInk: ui.Hex("#8F5808"),
	Danger: ui.Hex("#CF3A33"), DangerSoft: ui.Hex("#FCEBEA"), DangerInk: ui.Hex("#AF2C26"),
	NeutralSoft: ui.Hex("#EEF0F3"), NeutralInk: ui.Hex("#4A5263"),
	Shadow: ui.RGBA(16, 24, 40, 0.05),
}

var nativeDark = nativeColors{
	Canvas: ui.Hex("#111317"), Card: ui.Hex("#1A1D22"), Sidebar: ui.Hex("#15171B"), SidebarHover: ui.Hex("#22252B"),
	Border: ui.Hex("#2A2E35"), BorderStrong: ui.Hex("#3A3F48"),
	Text: ui.Hex("#E8EAEE"), Muted: ui.Hex("#A2A9B4"), Subtle: ui.Hex("#737B88"),
	Accent: ui.Hex("#5B7FF0"), AccentSoft: ui.Hex("#1E2742"), AccentInk: ui.Hex("#A3B8FF"),
	Success: ui.Hex("#3FB872"), SuccessSoft: ui.Hex("#15291E"), SuccessInk: ui.Hex("#74D59F"),
	Warning: ui.Hex("#E0A33A"), WarningSoft: ui.Hex("#2E2414"), WarningInk: ui.Hex("#F0C46F"),
	Danger: ui.Hex("#F0625A"), DangerSoft: ui.Hex("#331A1A"), DangerInk: ui.Hex("#FF928B"),
	NeutralSoft: ui.Hex("#252930"), NeutralInk: ui.Hex("#B6BCC6"),
	Shadow: ui.RGBA(0, 0, 0, 0.25),
}

func nativePalette(c *ui.Context) *nativeColors {
	if c.Theme().Dark {
		return &nativeDark
	}
	return &nativeLight
}

// nativeTheme maps the palette onto MyGo's theme. Surface equals the card so
// that secondary buttons and inputs read as outlined controls on cards.
func nativeTheme(c *ui.Context) {
	t := *c.Theme()
	p := &nativeLight
	if t.Dark {
		p = &nativeDark
	}
	t.FontSize, t.Radius, t.Spacing = 14, 8, 4
	t.Background = p.Card
	t.Text, t.Accent = p.Text, p.Accent
	t.AccentText = ui.Hex("#FFFFFF")
	t.Danger, t.Warning, t.Success = p.Danger, p.Warning, p.Success
	if t.Dark {
		t.Surface, t.SurfaceHover, t.SurfacePressed = ui.Hex("#22262C"), ui.Hex("#2A2F37"), ui.Hex("#323843")
		t.AccentHover, t.AccentPressed = ui.Hex("#6E8EF3"), ui.Hex("#4D6FDB")
	} else {
		t.Surface, t.SurfaceHover, t.SurfacePressed = p.Card, ui.Hex("#F3F4F7"), ui.Hex("#E8EBF0")
		t.AccentHover, t.AccentPressed = ui.Hex("#284FC0"), ui.Hex("#2244A6")
	}
	if !c.Preferences().HighContrast {
		t.Border, t.TextMuted = p.Border, p.Muted
	}
	t.Selection = p.Accent.Alpha(0.2)
	t.Focus = p.Accent.Alpha(0.6)
	t.Scrollbar = p.BorderStrong
	c.SetTheme(&t)
}

func nativeButton(c *ui.Context, label string) ui.Element {
	return ui.Button(c, label).AlignSelf(ui.Start).MinHeight(34).Padding(7, 14).FontWeight(500)
}
func nativeDangerButton(c *ui.Context, label string) ui.Element {
	return nativeButton(c, label).TextColor(c.Theme().Danger)
}
func nativePrimaryButton(c *ui.Context, label string) ui.Element {
	return ui.PrimaryButton(c, label).AlignSelf(ui.Start).MinHeight(34).Padding(8, 16).FontWeight(600).
		Shadow(0, 1, 2, 0, nativePalette(c).Accent.Alpha(0.25))
}

// nativeIconButton is a secondary or primary button led by an icon.
func nativeIconButton(c *ui.Context, icon, label string, primary bool) ui.Element {
	var b ui.Element
	if primary {
		b = nativePrimaryButton(c, "")
	} else {
		b = nativeButton(c, "")
	}
	return b.Label(label).Gap(7).Children(func() {
		ui.Icon(c, nativeIcon(icon)).FontSize(15)
		ui.Text(c, label).SingleLine()
	})
}

// nativeGhostButton has no face until hovered, for tertiary actions.
func nativeGhostButton(c *ui.Context, icon, label string, disabled bool) ui.Element {
	p := nativePalette(c)
	b := ui.ButtonBase(c).AlignSelf(ui.Start).MinHeight(32).Padding(6, 10).Gap(6).Radius(8).Label(label).Disabled(disabled)
	color := p.Muted
	if disabled {
		color = p.Subtle
		b.Opacity(0.6)
	} else if b.Hovered() {
		b.Background(c.Theme().SurfaceHover)
		color = p.Text
	}
	b.TextColor(color).Children(func() {
		if icon != "" {
			ui.Icon(c, nativeIcon(icon)).FontSize(15)
		}
		if label != "" {
			ui.Text(c, label).FontSize(13).FontWeight(500).SingleLine()
		}
	})
	return b
}

// nativeCard is the white container pages group related content in.
func nativeCard(c *ui.Context) ui.Element {
	p := nativePalette(c)
	return ui.Column(c).FillWidth().Shrink(0).Padding(20, 22).Gap(14).Radius(12).Background(p.Card).Border(1, p.Border).Shadow(0, 1, 2, 0, p.Shadow)
}

// nativeSection is a card with a titled header, optional header actions and
// a body.
func nativeSection(c *ui.Context, icon, title, hint string, actions func(), body func()) {
	p := nativePalette(c)
	nativeCard(c.Key(title)).Children(func() {
		ui.Row(c).FillWidth().Gap(12).AlignItems(ui.Center).Children(func() {
			if icon != "" {
				ui.Row(c).Size(32, 32).Shrink(0).Radius(8).Background(p.AccentSoft).Justify(ui.Center).AlignItems(ui.Center).Children(func() {
					ui.Icon(c, nativeIcon(icon)).FontSize(16).TextColor(p.AccentInk)
				})
			}
			ui.Column(c).Grow(1).MinWidth(0).Gap(2).Children(func() {
				ui.Text(c, title).FontSize(15.5).FontWeight(600).TextColor(p.Text)
				if hint != "" {
					ui.Text(c, hint).FontSize(12.5).TextColor(p.Muted)
				}
			})
			if actions != nil {
				ui.Row(c).Shrink(0).Gap(8).AlignItems(ui.Center).Children(actions)
			}
		})
		body()
	})
}

func nativeHeading(c *ui.Context, text string) {
	ui.Text(c, text).FontSize(15.5).FontWeight(600).TextColor(c.Theme().Text).Margin(12, 0, 0, 0)
}
func nativeText(c *ui.Context, text string) {
	if text != "" {
		ui.Text(c, text).FontSize(14).LineHeight(1.5).TextColor(c.Theme().Text).MaxWidth(920)
	}
}
func nativeHint(c *ui.Context, text string) {
	ui.Text(c, text).FontSize(12.5).LineHeight(1.45).TextColor(c.Theme().TextMuted).MaxWidth(820)
}
func nativeActions(c *ui.Context) ui.Element {
	return ui.Row(c).FillWidth().Wrap().Gap(8).Padding(14, 0, 2, 0).AlignItems(ui.Center).BorderWidth(1, 0, 0, 0).BorderColor(c.Theme().Border)
}
func nativeDisclosure(c *ui.Context, label string, expanded bool, content func()) {
	p := nativePalette(c)
	box := ui.Column(c.Key(label)).FillWidth().Gap(12).Padding(6, 14).Radius(10).Border(1, p.Border)
	open := ui.Local(box, "expanded", func() bool { return expanded })
	box.Children(func() { ui.Collapsible(c, label, open, content).FillWidth() })
}
func nativeField(c *ui.Context, label string, value *string, multiline bool) bool {
	changed := false
	ui.Column(c.Key(label)).FillWidth().MaxWidth(760).AlignSelf(ui.Start).Gap(6).Children(func() {
		ui.Text(c, label).FontSize(12.5).TextColor(c.Theme().TextMuted)
		if multiline {
			changed = ui.TextArea(c, value).Label(label).Height(112).FillWidth().Radius(8).Background(nativePalette(c).Card).Changed()
		} else {
			changed = ui.TextInput(c, value).Label(label).Height(36).FillWidth().Radius(8).Background(nativePalette(c).Card).Changed()
		}
	})
	return changed
}
func nativeChoice(c *ui.Context, value *string, options []string, labels ...map[string]string) ui.Element {
	p := nativePalette(c)
	choice := ui.SelectBase(c, value)
	label := func(code string) string {
		if len(labels) > 0 {
			if text, ok := labels[0][code]; ok {
				return text
			}
		}
		if code == "" {
			return "未选择"
		}
		return nativeLabel(code)
	}
	trigger := choice.Trigger.Padding(8, 12).Gap(10).MinHeight(36).Radius(8).Border(1, p.BorderStrong).Background(p.Card).Justify(ui.SpaceBetween).AlignItems(ui.Center)
	if trigger.Hovered() {
		trigger.Background(c.Theme().SurfaceHover)
	}
	trigger.Children(func() {
		ui.Text(c, nativeDisplay(label(*value))).SingleLine().Grow(1).MinWidth(0)
		ui.Icon(c, nativeIcon("chevron-down")).FontSize(14).TextColor(p.Muted)
	})
	choice.Popup(func(panel ui.Element) {
		panel.Margin(4, 0, 0, 0).Padding(4).Radius(10).Background(p.Card).Border(1, p.Border).Shadow(0, 8, 24, 0, p.Shadow.Alpha(0.16))
		for _, option := range options {
			item := choice.Item(option).Role(ui.RoleListItem).Label(label(option)).Padding(8, 10).Radius(6)
			if item.Highlighted() {
				item.Background(p.AccentSoft).TextColor(p.AccentInk)
			}
			item.Children(func() { ui.Text(c, nativeDisplay(label(option))) })
		}
	})
	return choice.Trigger
}
func nativeSelect(c *ui.Context, label string, value *string, options []string) bool {
	changed := false
	ui.Column(c.Key(label)).FillWidth().MaxWidth(760).AlignSelf(ui.Start).Gap(6).Children(func() {
		ui.Text(c, label).FontSize(12.5).TextColor(c.Theme().TextMuted)
		if label == "报告章节" {
			changed = nativeChoice(c, value, options, nativeReportLabels).Label(label).FillWidth().Changed()
		} else {
			changed = nativeChoice(c, value, options).Label(label).FillWidth().Changed()
		}
	})
	return changed
}

// Tones color badges and callouts by what a status means to the user.
const (
	toneNeutral = "neutral"
	toneAccent  = "accent"
	toneSuccess = "success"
	toneWarning = "warning"
	toneDanger  = "danger"
)

var nativeLabelTones = map[string]string{
	"已完成": toneSuccess, "已确认": toneSuccess, "当前有效": toneSuccess, "已解析": toneSuccess, "已就绪": toneSuccess, "有效": toneSuccess, "已批准": toneSuccess, "完整": toneSuccess, "已响应": toneSuccess, "无偏离": toneSuccess, "正偏离": toneSuccess, "链到本地信任根": toneSuccess, "已提交": toneSuccess,
	"失败": toneDanger, "已失效": toneDanger, "废标风险": toneDanger, "高风险": toneDanger, "无效": toneDanger, "缺失": toneDanger, "负偏离": toneDanger, "已拒绝": toneDanger, "偏离": toneDanger, "废标与扣分风险": toneDanger, "未链到本地信任根": toneDanger, "已过期": toneDanger,
	"处理中": toneWarning, "排队中": toneWarning, "解析中": toneWarning, "待处理": toneWarning, "待确认": toneWarning, "待审查": toneWarning, "中风险": toneWarning, "部分完成": toneWarning, "扣分风险": toneWarning, "历史未确认": toneWarning, "影响未确定": toneWarning, "候选": toneWarning,
	"已上传": toneAccent, "可用": toneSuccess, "已停用": toneNeutral, "候选，待人工审核": toneWarning, "待审阅": toneWarning, "已接受内部使用": toneSuccess, "已撤回": toneNeutral, "提示": toneAccent, "新建": toneAccent, "草稿": toneAccent, "低风险": toneAccent,
	"已取消": toneNeutral, "已驳回": toneNeutral, "已归档": toneNeutral, "未确定": toneNeutral,
}

func nativeToneColors(p *nativeColors, tone string) (soft, ink, solid ui.Color) {
	switch tone {
	case toneSuccess:
		return p.SuccessSoft, p.SuccessInk, p.Success
	case toneWarning:
		return p.WarningSoft, p.WarningInk, p.Warning
	case toneDanger:
		return p.DangerSoft, p.DangerInk, p.Danger
	case toneAccent:
		return p.AccentSoft, p.AccentInk, p.Accent
	}
	return p.NeutralSoft, p.NeutralInk, p.Subtle
}

// nativeBadge shows a short label in a tone-colored pill with a status dot.
func nativeBadge(c *ui.Context, text, tone string) ui.Element {
	soft, ink, solid := nativeToneColors(nativePalette(c), tone)
	return ui.Row(c).Shrink(0).AlignSelf(ui.Start).Gap(6).Padding(3, 9).Radius(999).Background(soft).AlignItems(ui.Center).Children(func() {
		ui.Box(c).Size(6, 6).Radius(3).Background(solid)
		ui.Text(c, text).FontSize(12).FontWeight(500).TextColor(ink).SingleLine()
	})
}

// nativeStatusBadge colors a translated status label by its known tone.
func nativeStatusBadge(c *ui.Context, label string) ui.Element {
	tone, ok := nativeLabelTones[label]
	if !ok {
		tone = toneNeutral
	}
	return nativeBadge(c, label, tone)
}

// nativeCallout is a tinted message line for errors, warnings and notices.
func nativeCallout(c *ui.Context, tone, text string) ui.Element {
	soft, ink, solid := nativeToneColors(nativePalette(c), tone)
	icon := "info"
	switch tone {
	case toneDanger:
		icon = "circle-alert"
	case toneWarning:
		icon = "triangle-alert"
	case toneSuccess:
		icon = "circle-check"
	}
	return ui.Row(c).FillWidth().Gap(10).Padding(10, 14).Radius(10).Background(soft).Border(1, solid.Alpha(0.25)).AlignItems(ui.Start).Children(func() {
		ui.Icon(c, nativeIcon(icon)).FontSize(16).TextColor(solid).Margin(1, 0, 0, 0)
		ui.Text(c, text).Grow(1).MinWidth(0).FontSize(13.5).LineHeight(1.45).TextColor(ink)
	})
}

// nativeEmpty is the placeholder for a list without records.
func nativeEmpty(c *ui.Context, icon, message string, action func()) {
	p := nativePalette(c)
	ui.Column(c).FillWidth().Padding(28, 20).Gap(10).Radius(10).Border(1, p.BorderStrong).BorderStyle(ui.BorderDashed).AlignItems(ui.Center).Children(func() {
		ui.Row(c).Size(40, 40).Radius(20).Background(p.NeutralSoft).Justify(ui.Center).AlignItems(ui.Center).Children(func() {
			ui.Icon(c, nativeIcon(icon)).FontSize(18).TextColor(p.Muted)
		})
		ui.Text(c, message).FontSize(13).TextColor(p.Muted).TextAlign(ui.Center).MaxWidth(420)
		if action != nil {
			action()
		}
	})
}

// nativeTime shows an RFC 3339 timestamp in the machine's time zone.
func nativeTime(value string) (string, bool) {
	if !strings.Contains(value, "T") {
		return value, false
	}
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil {
		return value, false
	}
	return parsed.Local().Format("2006-01-02 15:04"), true
}

// nativeDeadline describes the time left before a deadline and its urgency.
func nativeDeadline(value any, now time.Time) (string, string) {
	text, _ := value.(string)
	if text == "" {
		return "未设截止", toneNeutral
	}
	deadline, err := time.Parse(time.RFC3339, text)
	if err != nil {
		return "", toneNeutral
	}
	left := deadline.Sub(now)
	switch {
	case left <= 0:
		return "已截止", toneNeutral
	case left < 24*time.Hour:
		return fmt.Sprintf("剩 %d 小时", max(1, int(left.Hours()))), toneDanger
	case left < 72*time.Hour:
		return fmt.Sprintf("剩 %d 天", int(left.Hours()/24)), toneDanger
	case left < 7*24*time.Hour:
		return fmt.Sprintf("剩 %d 天", int(left.Hours()/24)), toneWarning
	}
	return fmt.Sprintf("剩 %d 天", int(left.Hours()/24)), toneAccent
}

func nativeBadgeColumn(header string) bool {
	if strings.Contains(header, "状态") && !strings.Contains(header, "/") {
		return true
	}
	switch header {
	case "完成情况", "决定", "严重程度", "风险", "偏离", "适用性", "评估结果", "生命周期":
		return true
	}
	return false
}

func nativeTable(c *ui.Context, headers []string, rows [][]string, onSelect func(int)) {
	p := nativePalette(c)
	box := ui.Column(c).FillWidth().Shrink(0).Gap(8)
	state := ui.Local(box, "selection", func() int { return -1 })
	list := ui.Local(box, "list", func() ui.ListState { return ui.ListState{} })
	// Pages replace cached rows on refresh, filtering or paging. A numeric
	// selection must not move to a new snapshot with identical display text.
	previous := ui.Local(box, "rows", func() [][]string { return nil })
	if len(*previous) != len(rows) || len(rows) > 0 && &(*previous)[0] != &rows[0] {
		*state = -1
		*previous = rows
	}
	list.Selected = state
	list.RoundRows = true
	box.Children(func() {
		if len(rows) == 0 {
			message, icon := "暂无记录。", "inbox"
			if len(headers) > 0 {
				switch headers[0] {
				case "任务名称":
					message, icon = "还没有招标任务。新建任务后，上传招标文件开始准备。", "briefcase-business"
				case "文档":
					message, icon = "还没有招标文件。选择 PDF 或 DOCX 后上传，再解析原文。", "file-up"
				case "抽取记录":
					message, icon = "还没有抽取记录。解析招标文件后，先预检再开始抽取。", "list-checks"
				case "文件":
					message, icon = "还没有导出文件。完成人工审阅后，到「初稿与导出」组表。", "file-output"

				}
			}
			nativeEmpty(c, icon, message, nil)
			return
		}
		cols := make([]ui.TableColumn, len(headers))
		badges := make([]bool, len(headers))
		for i, title := range headers {
			width := float32(166)
			switch title {
			case "招标条款", "响应正文", "要求正文", "已确认材料", "具体说明", "摘要":
				width = 340
			case "状态", "修订", "版本", "页数", "保存", "拒绝", "类型", "生命周期", "来源声明", "有效状态", "类别", "要求状态", "来源", "资格":
				width = 108
			case "星标":
				width = 64
			case "名称", "任务名称", "文档", "文件":
				width = 270
			}
			cols[i] = ui.TableColumn{Title: title, Width: width, MinWidth: min(80, width)}
			badges[i] = nativeBadgeColumn(title)
		}
		// Estimate a small viewport from content; MyGo still measures every visible
		// row dynamically and keeps full text accessible by vertical scrolling.
		height := float32(40)
		for _, row := range rows {
			lines := 1
			for i, cell := range row {
				if i < len(cols) && !badges[i] {
					cell = nativeDisplay(cell)
					// Latin text and digits take about half a CJK glyph.
					var units float32
					for _, r := range cell {
						if r < 0x2E80 {
							units += 0.55
						} else {
							units++
						}
					}
					capacity := max(1, (cols[i].Width-24)/14)
					lines = max(lines, int((units+capacity-0.01)/capacity))
				}
			}
			height += float32(max(37, lines*20+16))
			if height >= 360 {
				break
			}
		}
		table := ui.Table(c, list, cols, len(rows), func(row, col int) {
			if col >= len(rows[row]) {
				return
			}
			cell := nativeDisplay(rows[row][col])
			if badges[col] {
				cell = nativeLabel(cell)
				if _, known := nativeLabelTones[cell]; known {
					nativeStatusBadge(c, cell)
					return
				}
			}
			cell = nativeDisplay(cell)
			text := ui.Text(c, cell).FontSize(13.5)
			if col == 0 {
				text.FontWeight(500)
			}
		}).Height(min(360, height)).FillWidth().Shrink(0).Border(1, p.Border).Radius(10).Background(p.Card)
		if table.Submitted() && onSelect != nil && *state >= 0 && *state < len(rows) {
			onSelect(*state)
		}
		ui.Row(c).FillWidth().Wrap().Gap(10).AlignItems(ui.Center).Children(func() {
			ui.Text(c, fmt.Sprintf("共 %d 条", len(rows))).FontSize(12).TextColor(p.Subtle)
			if onSelect != nil {
				ui.Text(c, "双击或按 Enter 打开").FontSize(12).TextColor(p.Subtle).Grow(1)
				nativeGhostButton(c, "external-link", "打开所选记录", *state < 0 || *state >= len(rows)).OnClick(func() { onSelect(*state) })
			}
		})
	})
}
func nativePairs(c *ui.Context, labels []string, values []string) {
	width := float32(182)
	for _, label := range labels {
		measured, _ := c.MeasureText(0, ui.Span{Text: label, Size: 13})
		width = max(width, measured)
	}
	ui.Column(c).FillWidth().Gap(9).Children(func() {
		for i, label := range labels {
			value := ""
			if i < len(values) {
				value = values[i]
			}
			nativePair(c, label, value, width)
		}
	})
}
func nativePair(c *ui.Context, label, value string, width float32) {
	ui.Row(c.Key(label)).FillWidth().AlignItems(ui.Start).Gap(16).Children(func() {
		ui.Text(c, label).Width(width).Shrink(0).SingleLine().FontSize(13).TextColor(c.Theme().TextMuted)
		value = nativeDisplayValue(value)
		if _, known := nativeLabelTones[value]; known {
			nativeStatusBadge(c, value)
		} else {
			ui.Text(c, value).Grow(1).MinWidth(0).FontSize(14).TextColor(c.Theme().Text)
		}
	})
}

var nativeUUIDPattern = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

func nativeShortID(value string) string {
	if len(value) > 8 {
		return value[:8]
	}
	return value
}
func nativeDisplayValue(value string) string {
	if strings.TrimSpace(value) == "" {
		return "—"
	}
	value, _ = nativeTime(value)
	return value
}
func nativeDisplay(value string) string {
	if nativeUUIDPattern.MatchString(value) {
		return nativeShortID(value)
	}
	return nativeDisplayValue(value)
}

func nativePager(c *ui.Context, page int, hasNext, busy bool, previousLabel, nextLabel string, previous, next func()) {
	if page <= 1 && !hasNext {
		return
	}
	ui.Row(c.Key(previousLabel)).FillWidth().Wrap().Gap(8).AlignItems(ui.Center).Children(func() {
		ui.Text(c, fmt.Sprintf("第 %d 页", page)).FontSize(12.5).TextColor(c.Theme().TextMuted).Grow(1)
		nativeGhostButton(c, "chevron-left", "", busy || page <= 1 || previous == nil).Label(previousLabel).OnClick(previous)
		nativeGhostButton(c, "chevron-right", "", busy || !hasNext || next == nil).Label(nextLabel).OnClick(next)
	})
}

// nativeSearch is a search field with a Chinese placeholder; MyGo's
// SearchField always shows "Search".
// submit, when set, runs on Enter.
func nativeSearch(c *ui.Context, query *string, label, placeholder string, submit func()) ui.Element {
	p := nativePalette(c)
	box := ui.Row(c).Height(36).Padding(0, 10).Gap(8).Radius(8).AlignItems(ui.Center).Background(p.Card).Border(1, p.Border)
	box.Children(func() {
		ui.Icon(c, nativeIcon("search")).FontSize(15).TextColor(p.Subtle)
		if ui.TextInputBase(c, query).Label(label).Placeholder(placeholder).Grow(1).MinWidth(0).FontSize(13.5).Submitted() && submit != nil {
			submit()
		}
		if *query != "" {
			clear := ui.ButtonBase(c).Size(20, 20).Radius(10).Justify(ui.Center).AlignItems(ui.Center).Label("清除搜索")
			if clear.Hovered() {
				clear.Background(p.SidebarHover)
			}
			clear.Children(func() { ui.Icon(c, nativeIcon("x")).FontSize(13).TextColor(p.Muted) }).OnClick(func() { *query = "" })
		}
	})
	return box
}
