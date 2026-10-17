package main

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/egoist/mygo/ui"
)

// solutionPage drafts, reviews and confirms the solution chapters of one tender
// document and exports the assembled whole bid.
type solutionPage struct {
	task, document   string
	documents, items []Object
	chapters         []Object
	picked           map[string]bool
	selected         string
	detail           Object
	editing          bool
	text, original   string
	generation       uint64
	jobPanel         jobPanel
	// The outline's scoring summary: where the items came from and why they may be incomplete.
	scoring     Object
	reviewPanel jobPanel
	figurePanel jobPanel
	// Figure images by SHA-256, verified against the revision that names them.
	figures map[string]*ui.Bitmap
}

func newSolutionPage(r Route) NativePage {
	return &solutionPage{task: r.Params["taskId"], document: r.Query.Get("document"), picked: map[string]bool{}, figures: map[string]*ui.Bitmap{}}
}
func (*solutionPage) Title() string { return "整本标书" }
func (p *solutionPage) Dirty() bool { return p.editing && p.text != p.original }
func (p *solutionPage) Close() {
	p.generation++
	p.jobPanel.Close()
	p.reviewPanel.Close()
	p.figurePanel.Close()
}
func (p *solutionPage) Open(a *NativeApp) {
	a.Request("GET", "/tasks/"+p.task+"/documents", nil, func(r Result) {
		p.documents = nil
		for _, document := range r.Items {
			if str(document, "status") == "parsed" {
				p.documents = append(p.documents, document)
			}
		}
		if p.document == "" && len(p.documents) > 0 {
			p.document = str(p.documents[0], "id")
		}
		p.load(a)
	})
}

func (p *solutionPage) load(a *NativeApp) {
	if p.document == "" {
		return
	}
	document := p.document
	a.Request("GET", "/documents/"+document+"/outline", nil, func(r Result) {
		if document != p.document {
			return
		}
		p.items = nil
		p.scoring = r.Data
		for _, item := range objects(r.Data["scoring_items"]) {
			if item["subjective"] == true {
				p.items = append(p.items, item)
			}
		}
		p.loadChapters(a)
	})
}

func (p *solutionPage) loadChapters(a *NativeApp) {
	document := p.document
	a.Request("GET", "/tasks/"+p.task+"/solutions?document="+document, nil, func(r Result) {
		if document != p.document {
			return
		}
		// Chapters follow the scoring chapter's order, as the file does.
		order := map[string]int{}
		for position, item := range p.items {
			order[str(item, "title")] = position
		}
		p.chapters = append([]Object{}, r.Items...)
		sort.SliceStable(p.chapters, func(i, j int) bool {
			return order[str(p.chapters[i], "title")] < order[str(p.chapters[j], "title")]
		})
		drafted := map[string]bool{}
		for _, chapter := range r.Items {
			drafted[str(chapter, "title")] = true
		}
		// Items without a chapter are picked by default; redrafting is a choice.
		for _, item := range p.items {
			title := str(item, "title")
			if _, seen := p.picked[title]; !seen {
				p.picked[title] = !drafted[title]
			}
		}
		if p.selected != "" {
			p.show(a, p.selected)
		}
	})
}

func (p *solutionPage) show(a *NativeApp, id string) {
	p.selected = id
	p.editing = false
	p.generation++
	generation := p.generation
	a.Request("GET", "/solutions/"+id, nil, func(r Result) {
		if generation != p.generation {
			return
		}
		p.detail = r.Data
		p.loadFigures(a, id, generation)
	})
}

// loadFigures fetches each figure of the current revision not yet shown, checking its digest.
func (p *solutionPage) loadFigures(a *NativeApp, id string, generation uint64) {
	for _, section := range objects(object(p.detail["current"])["sections"]) {
		for _, sub := range objects(section["subsections"]) {
			figure := object(sub["figure"])
			hash := str(figure, "sha256")
			if figure == nil || p.figures[hash] != nil {
				continue
			}
			a.Do(func(ctx context.Context) (func(), error) {
				data, err := a.Client.BinaryImage(ctx, "/solutions/"+id+"/figures/"+hash, str(figure, "media_type"), Object{"sha256": hash})
				if err != nil {
					return nil, err
				}
				bitmap, err := ui.DecodeBitmap(data)
				return func() {
					if generation == p.generation {
						p.figures[hash] = bitmap
					}
				}, err
			})
		}
	}
}

// uploadFigure attaches a real screenshot to subsection `number`, captioned with its heading.
func (p *solutionPage) uploadFigure(a *NativeApp, revision int, number, heading string) {
	caption := heading
	if utf8.RuneCountInString(caption) > 60 {
		caption = string([]rune(caption)[:60])
	}
	a.PickFiles(false, func(paths []string) {
		fields := map[string]string{"expected_revision": fmt.Sprint(revision), "subsection": number, "caption": caption}
		a.Upload("/solutions/"+p.selected+"/figures/upload", fields, []UploadFile{{Field: "file", Path: paths[0]}}, func(Result) {
			p.loadChapters(a)
		})
	})
}

func (p *solutionPage) View(c *ui.Context, a *NativeApp) {
	nativeText(c, "整本标书按招标文件格式章节组装：表单、偏离表和评分索引自动填写，主观评分项由模型起草方案章节，逐章人工确认。未确认章节或仍有【待填】时导出为审阅稿。")
	if len(p.documents) == 0 {
		nativeEmpty(c, "file-text", "没有已解析的招标文件。先在“文件与解析”中上传并解析。", nil)
		return
	}
	options := []string{}
	labels := map[string]string{}
	for _, document := range p.documents {
		options = append(options, str(document, "id"))
		labels[str(document, "id")] = str(document, "name")
	}
	ui.Column(c.Key("solution-document")).FillWidth().MaxWidth(760).AlignSelf(ui.Start).Gap(6).Children(func() {
		ui.Text(c, "招标文件").FontSize(12.5).TextColor(c.Theme().TextMuted)
		if nativeChoice(c, &p.document, options, labels).Label("招标文件").FillWidth().Changed() {
			p.selected, p.detail, p.chapters, p.items = "", nil, nil, nil
			p.picked = map[string]bool{}
			p.load(a)
		}
	})

	nativeHeading(c, "起草方案章节")
	p.scoringView(c, a)
	if len(p.items) == 0 {
		nativeText(c, "评分章节中没有识别到方案类评分项。")
	}
	titles := []string{}
	for _, item := range p.items {
		title := str(item, "title")
		checked := p.picked[title]
		if ui.Checkbox(c.Key("pick-"+title), &checked, title+"（"+str(item, "score_range")+" 分）").Disabled(a.Busy).Changed() {
			p.picked[title] = checked
		}
		if p.picked[title] {
			titles = append(titles, title)
		}
	}
	nativePrimaryButton(c, fmt.Sprintf("起草所选 %d 章", len(titles))).Disabled(a.Busy || len(titles) == 0).OnClick(func() {
		a.Request("POST", "/tasks/"+p.task+"/solutions/draft", Object{"document_id": p.document, "titles": titles}, func(r Result) {
			p.jobPanel.Load(a, str(r.Data, "job_id"), func() { p.loadChapters(a) })
		})
	})
	p.jobPanel.View(c, a)

	nativeHeading(c, "方案章节")
	rows := [][]string{}
	unconfirmed := 0
	for _, chapter := range p.chapters {
		current := object(chapter["current"])
		if str(current, "state") != "confirmed" {
			unconfirmed++
		}
		rows = append(rows, []string{str(chapter, "title"), str(chapter, "score_range"), str(current, "revision"), nativeLabel(str(current, "state")), nativeLabel(str(current, "origin")), str(chapter, "placeholder_count")})
	}
	nativeTable(c, []string{"评分项", "分值", "修订", "状态", "来源", "待填"}, rows, func(i int) { p.show(a, str(p.chapters[i], "id")) })

	nativeHeading(c, "导出")
	nativeText(c, fmt.Sprintf("已起草 %d / %d 章，待确认 %d 章。", len(p.chapters), len(p.items), unconfirmed))
	nativeIconButton(c, "download", "导出整本标书", false).Disabled(a.Busy).OnClick(func() {
		a.DownloadVerified("/documents/"+p.document+"/whole-bid-link", "/documents/"+p.document+"/whole-bid", "投标文件.docx", nil, nil)
	})

	if p.detail != nil {
		p.chapterView(c, a)
	}
}

// scoringWarnings explains why the scoring items may be incomplete.
var scoringWarnings = map[string]string{
	"no_items":          "没有识别到评分项",
	"no_total":          "原文未写明总分，无法核对",
	"sum_mismatch":      "各项分值之和与总分不一致",
	"multiple_packages": "招标文件有多个包件，只列出第一个包件的评分项",
	"region_truncated":  "评分表过长，只读取了前一部分",
}

func (p *solutionPage) scoringView(c *ui.Context, a *NativeApp) {
	if p.scoring == nil {
		return
	}
	origin := "规则识别"
	if str(p.scoring, "scoring_origin") == "model" {
		origin = "模型识别并逐项回原文核验"
	}
	summary := fmt.Sprintf("评分项 %d 项（%s）", len(objects(p.scoring["scoring_items"])), origin)
	if total, ok := p.scoring["scoring_total"].(float64); ok {
		summary += fmt.Sprintf("，总分 %g", total)
	}
	if sum, ok := p.scoring["scoring_sum"].(float64); ok {
		summary += fmt.Sprintf("，各项合计 %g", sum)
	}
	nativeText(c, summary+"。")
	notes := []string{}
	for _, code := range stringList(p.scoring["scoring_warnings"]) {
		if note, ok := scoringWarnings[code]; ok {
			notes = append(notes, note)
		}
	}
	if len(notes) > 0 {
		nativeText(c, "请核对："+strings.Join(notes, "；")+"。")
	}
	if len(notes) > 0 || str(p.scoring, "scoring_origin") == "model" {
		nativeIconButton(c, "sparkles", "用模型识别评分项", false).Disabled(a.Busy).OnClick(func() {
			a.Request("POST", "/documents/"+p.document+"/outline/review", Object{}, func(r Result) {
				p.reviewPanel.Load(a, str(r.Data, "job_id"), func() { p.load(a) })
			})
		})
	}
	p.reviewPanel.View(c, a)
}

func (p *solutionPage) chapterView(c *ui.Context, a *NativeApp) {
	current := object(p.detail["current"])
	revision := integer(current, "revision")
	nativeHeading(c, str(p.detail, "title"))
	nativeText(c, fmt.Sprintf("修订 %d / %s / %s / 待填 %s 处", revision, nativeLabel(str(current, "state")), nativeLabel(str(current, "origin")), str(p.detail, "placeholder_count")))
	ui.Row(c).FillWidth().Wrap().Gap(8).Children(func() {
		if p.editing {
			nativePrimaryButton(c, "保存修改").Disabled(a.Busy || p.text == p.original).OnClick(func() {
				sections, err := parseSolutionText(p.text)
				if err != nil {
					a.Error = err.Error()
					return
				}
				a.Request("PUT", "/solutions/"+p.selected, Object{"expected_revision": revision, "sections": withFigures(sections, objects(current["sections"]))}, func(Result) {
					p.editing = false
					p.loadChapters(a)
				})
			})
			nativeGhostButton(c, "x", "放弃修改", a.Busy).OnClick(func() { p.editing = false })
			return
		}
		nativeIconButton(c, "pencil", "编辑本章", false).Disabled(a.Busy).OnClick(func() {
			p.text = formatSolutionText(objects(current["sections"]))
			p.original = p.text
			p.editing = true
		})
		nativeIconButton(c, "circle-check", "确认本章", false).Disabled(a.Busy || str(current, "state") == "confirmed").OnClick(func() {
			a.Request("POST", "/solutions/"+p.selected+"/confirm", Object{"expected_revision": revision}, func(Result) { p.loadChapters(a) })
		})
		nativeIconButton(c, "image", "生成系统界面图", false).Disabled(a.Busy).OnClick(func() {
			a.Request("POST", "/solutions/"+p.selected+"/figures", Object{"expected_revision": revision}, func(r Result) {
				p.figurePanel.Load(a, str(r.Data, "job_id"), func() { p.loadChapters(a) })
			})
		})
	})
	p.figurePanel.View(c, a)
	if p.editing {
		nativeText(c, "“# ”开头为节标题，“## ”开头为小节标题，“| ”开头的连续行为小节表格（首行为表头），“![图题](编号)”为小节配图，可改图题或删去该行；其余每行一个段落。保存后本章回到草稿，需要重新确认。")
		ui.TextArea(c, &p.text).Label("章节正文").Height(560).FillWidth().Radius(8).Background(nativePalette(c).Card)
		return
	}
	nativeText(c, "“生成系统界面图”按本章内容为至多 6 个小节绘制系统页面图，替换此前生成的图，保留上传的真实截图；有真实系统时在小节下上传截图。")
	for position, section := range objects(current["sections"]) {
		ui.Text(c, str(section, "heading")).FontSize(16).FontWeight(600).TextColor(c.Theme().Text)
		for _, paragraph := range stringList(section["paragraphs"]) {
			nativeText(c, paragraph)
		}
		for subPosition, sub := range objects(section["subsections"]) {
			number := fmt.Sprintf("%d.%d", position+1, subPosition+1)
			ui.Text(c, str(sub, "heading")).FontSize(14.5).FontWeight(600).TextColor(c.Theme().Text)
			for _, paragraph := range stringList(sub["paragraphs"]) {
				nativeText(c, paragraph)
			}
			if table := object(sub["table"]); table != nil {
				body := [][]string{}
				rows, _ := table["rows"].([]any)
				for _, row := range rows {
					body = append(body, stringList(row))
				}
				nativeTable(c, stringList(table["columns"]), body, nil)
			}
			heading := str(sub, "heading")
			label := "上传真实截图"
			if figure := object(sub["figure"]); figure != nil {
				if bitmap := p.figures[str(figure, "sha256")]; bitmap != nil {
					ui.Image(c.Key("figure-"+number), bitmap).FillWidth().Height(420).Fit(ui.Contain).Label(str(figure, "caption"))
				}
				origin := "生成"
				if str(figure, "origin") == "uploaded" {
					origin = "上传"
				}
				nativeText(c, "图："+str(figure, "caption")+"（"+origin+"）")
				label = "替换为真实截图"
			}
			nativeGhostButton(c.Key("upload-"+number), "upload", label, a.Busy).OnClick(func() {
				p.uploadFigure(a, revision, number, heading)
			})
		}
	}
}

// formatSolutionText writes sections as editable text; parseSolutionText reads it back.
func formatSolutionText(sections []Object) string {
	var b strings.Builder
	for _, section := range sections {
		b.WriteString("# " + str(section, "heading") + "\n")
		for _, paragraph := range stringList(section["paragraphs"]) {
			b.WriteString(paragraph + "\n")
		}
		for _, sub := range objects(section["subsections"]) {
			b.WriteString("## " + str(sub, "heading") + "\n")
			for _, paragraph := range stringList(sub["paragraphs"]) {
				b.WriteString(paragraph + "\n")
			}
			if table := object(sub["table"]); table != nil {
				b.WriteString("| " + strings.Join(stringList(table["columns"]), " | ") + " |\n")
				rows, _ := table["rows"].([]any)
				for _, row := range rows {
					b.WriteString("| " + strings.Join(stringList(row), " | ") + " |\n")
				}
			}
			if figure := object(sub["figure"]); figure != nil {
				b.WriteString("![" + str(figure, "caption") + "](" + str(figure, "sha256") + ")\n")
			}
		}
		b.WriteString("\n")
	}
	return b.String()
}

var figureLine = regexp.MustCompile(`^!\[(.+)\]\(([0-9a-f]{64})\)$`)

// withFigures completes each parsed figure from the revision's figure of the same image.
func withFigures(sections []Object, previous []Object) []Object {
	known := map[string]Object{}
	for _, section := range previous {
		for _, sub := range objects(section["subsections"]) {
			if figure := object(sub["figure"]); figure != nil {
				known[str(figure, "sha256")] = figure
			}
		}
	}
	for _, section := range sections {
		subs, _ := section["subsections"].([]Object)
		for _, sub := range subs {
			if figure, ok := sub["figure"].(Object); ok {
				if found := known[str(figure, "sha256")]; found != nil {
					figure["media_type"], figure["origin"] = found["media_type"], found["origin"]
				}
			}
		}
	}
	return sections
}

func parseSolutionText(text string) ([]Object, error) {
	sections := []Object{}
	var section, sub Object
	var table Object
	for number, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(raw)
		switch {
		case line == "":
			continue
		case strings.HasPrefix(line, "## "):
			if section == nil {
				return nil, fmt.Errorf("第 %d 行：小节标题前需要节标题", number+1)
			}
			sub, table = Object{"heading": strings.TrimSpace(line[3:]), "paragraphs": []string{}}, nil
			section["subsections"] = append(section["subsections"].([]Object), sub)
		case strings.HasPrefix(line, "# "):
			section, sub, table = Object{"heading": strings.TrimSpace(line[2:]), "paragraphs": []string{}, "subsections": []Object{}}, nil, nil
			sections = append(sections, section)
		case figureLine.MatchString(line):
			if sub == nil {
				return nil, fmt.Errorf("第 %d 行：配图只能放在小节中", number+1)
			}
			match := figureLine.FindStringSubmatch(line)
			sub["figure"] = Object{"caption": strings.TrimSpace(match[1]), "sha256": match[2]}
		case strings.HasPrefix(line, "|"):
			if sub == nil {
				return nil, fmt.Errorf("第 %d 行：表格只能放在小节中", number+1)
			}
			cells := []string{}
			for _, cell := range strings.Split(strings.Trim(line, "|"), "|") {
				cells = append(cells, strings.TrimSpace(cell))
			}
			if table == nil {
				table = Object{"columns": cells, "rows": [][]string{}}
				sub["table"] = table
			} else {
				table["rows"] = append(table["rows"].([][]string), cells)
			}
		default:
			if section == nil {
				return nil, fmt.Errorf("第 %d 行：正文前需要“# ”节标题", number+1)
			}
			target := section
			if sub != nil {
				target = sub
			}
			target["paragraphs"] = append(target["paragraphs"].([]string), line)
		}
	}
	if len(sections) == 0 {
		return nil, errors.New("章节至少需要一个“# ”节标题")
	}
	return sections, nil
}
