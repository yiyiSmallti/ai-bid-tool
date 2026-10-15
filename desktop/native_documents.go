package main

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"
)

// documentPanel owns each read, including same-page paging and zoom changes.
// Block-cited (Word) documents show the parsed structure one section at a
// time; page-cited (PDF) documents show the page image beside its parsed text.
type documentPanel struct {
	id, block, title, pageInput, zoom string
	page, count, section              int
	opened, blockMode                 bool
	bitmap                            *ui.Bitmap
	chunks                            []Object
	cancel                            context.CancelFunc
	generation                        uint64
	error                             string
}

func (p *documentPanel) Close() {
	p.generation++
	if p.cancel != nil {
		p.cancel()
	}
	p.opened = false
	p.bitmap = nil
	p.chunks = nil
	p.error = ""
}
func (p *documentPanel) Open(a *NativeApp, id string, page int, block string) {
	p.Close()
	p.opened = true
	p.id = id
	p.block = block
	p.page = max(1, page)
	p.section = 0
	p.zoom = "1"
	p.load(a)
}
func (p *documentPanel) load(a *NativeApp) {
	if p.cancel != nil {
		p.cancel()
	}
	p.generation++
	p.bitmap = nil
	p.error = ""
	p.pageInput = strconv.Itoa(p.page)
	ctx, cancel := context.WithCancel(a.ctx)
	p.cancel = cancel
	generation, epoch := p.generation, a.epoch
	id, page, zoom, block := p.id, p.page, p.zoom, p.block
	// Paging and zoom only redraw the image; the parsed text is read once.
	chunks := p.chunks
	go func() {
		r, err := a.Client.Request(ctx, "GET", "/documents/"+id, nil)
		var bitmap *ui.Bitmap
		blockMode := str(r.Data, "citation_mode") == "block"
		if err == nil && chunks == nil {
			var result Result
			result, err = a.Client.Request(ctx, "GET", "/documents/"+id+"/chunks", nil)
			chunks = result.Items
			if chunks == nil {
				chunks = []Object{}
			}
		}
		if err == nil && blockMode && block != "" && documentBlockSection(chunks, block) < 0 {
			err = fmt.Errorf("所指原文结构块不可访问")
		}
		if err == nil && !blockMode {
			var data []byte
			data, err = a.Client.BinaryImage(ctx, fmt.Sprintf("/documents/%s/pages/%d/preview?zoom=%s", id, page, zoom), "image/png", nil)
			if err == nil {
				bitmap, err = ui.DecodeBitmap(data)
			}
		}
		a.update(func() {
			if ctx.Err() != nil || generation != p.generation || epoch != a.epoch {
				return
			}
			if err != nil {
				p.error = err.Error()
				a.handleError(err)
				return
			}
			p.title = str(r.Data, "name")
			p.count = integer(r.Data, "page_count")
			p.blockMode = blockMode
			if p.chunks == nil && blockMode && block != "" {
				p.section = documentBlockSection(chunks, block)
			}
			p.chunks = chunks
			p.bitmap = bitmap
		})
	}()
}

func documentBlockSection(chunks []Object, block string) int {
	for i, chunk := range chunks {
		for _, entry := range objects(chunk["blocks"]) {
			if str(entry, "block_id") == block {
				return i
			}
		}
	}
	return -1
}

// documentSectionPath is the heading trail of a parsed section; the cover
// and table of contents come before the first heading.
func documentSectionPath(chunk Object) []string {
	for _, entry := range objects(chunk["blocks"]) {
		var path []string
		for _, part := range anyList(entry["section_path"]) {
			if text, ok := part.(string); ok {
				path = append(path, text)
			}
		}
		return path
	}
	return nil
}
func documentSectionTitle(chunk Object) string {
	path := documentSectionPath(chunk)
	if len(path) == 0 {
		return "封面与目录"
	}
	return path[len(path)-1]
}
func anyList(value any) []any {
	list, _ := value.([]any)
	return list
}

// documentStats counts paragraphs, tables and cells across the parsed sections.
func documentStats(chunks []Object) (paragraphs, tables, cells int) {
	seen := map[string]bool{}
	for _, chunk := range chunks {
		for _, entry := range objects(chunk["blocks"]) {
			if str(entry, "kind") == "cell" {
				cells++
				if table := fmt.Sprint(entry["table"]); !seen[table] {
					seen[table] = true
					tables++
				}
			} else {
				paragraphs++
			}
		}
	}
	return
}

func (p *documentPanel) View(c *ui.Context, a *NativeApp) {
	if !p.opened {
		return
	}
	palette := nativePalette(c)
	ui.Row(c).FillWidth().Gap(10).AlignItems(ui.Center).Children(func() {
		ui.Icon(c, nativeIcon("scroll-text")).FontSize(16).TextColor(palette.AccentInk)
		ui.Column(c).Grow(1).MinWidth(0).Gap(2).Children(func() {
			ui.Text(c, "解析结果 · "+p.title).FontSize(14.5).FontWeight(600).TextColor(palette.Text).SingleLine()
			if p.chunks != nil {
				ui.Text(c, p.summary()).FontSize(12.5).TextColor(palette.Muted)
			}
		})
		nativeGhostButton(c, "x", "关闭", false).Label("关闭原文预览").OnClick(p.Close)
	})
	if p.error != "" {
		nativeCallout(c, toneDanger, p.error)
	}
	if p.chunks == nil {
		if p.error == "" {
			nativeHint(c, "正在读取解析结果…")
		}
		return
	}
	if p.blockMode {
		p.sectionsView(c)
	} else {
		p.pagesView(c, a)
	}
}

func (p *documentPanel) summary() string {
	if !p.blockMode {
		ocr := 0
		for _, chunk := range p.chunks {
			if chunk["ocr"] == true {
				ocr++
			}
		}
		text := fmt.Sprintf("共 %d 页，已提取 %d 页文字", p.count, len(p.chunks))
		if ocr > 0 {
			text += fmt.Sprintf("，其中 %d 页经 OCR 识别", ocr)
		}
		return text
	}
	paragraphs, tables, cells := documentStats(p.chunks)
	return fmt.Sprintf("共 %d 节 · %d 个段落 · %d 个表格（%d 个单元格）", len(p.chunks), paragraphs, tables, cells)
}

const documentPaneHeight = 620

func (p *documentPanel) sectionsView(c *ui.Context) {
	palette := nativePalette(c)
	if len(p.chunks) == 0 {
		nativeEmpty(c, "file-text", "解析结果为空，请重新解析该文档。", nil)
		return
	}
	p.section = min(max(p.section, 0), len(p.chunks)-1)
	ui.Row(c).FillWidth().Gap(12).AlignItems(ui.Stretch).Children(func() {
		ui.Scroll(c.Key("document-outline")).Width(280).Shrink(0).Height(documentPaneHeight).Padding(6).Gap(2).Radius(10).Background(palette.Card).Border(1, palette.Border).Children(func() {
			for i, chunk := range p.chunks {
				depth := max(len(documentSectionPath(chunk))-1, 0)
				title := documentSectionTitle(chunk)
				if i > 0 && strings.Join(documentSectionPath(p.chunks[i-1]), "\x00") == strings.Join(documentSectionPath(chunk), "\x00") {
					title += "（续）"
				}
				item := ui.ButtonBase(c.Key(fmt.Sprintf("section-%d", i))).FillWidth().Padding(6, 10, 6, 10+float32(min(depth, 3))*14).Radius(6).AlignItems(ui.Start).Justify(ui.Start).Label("解析章节 " + title)
				if i == p.section {
					item.Background(palette.AccentSoft)
				} else if item.Hovered() {
					item.Background(palette.SidebarHover)
				}
				index := i
				item.Children(func() {
					text := ui.Text(c, title).FillWidth().FontSize(12.5).LineHeight(1.35).MaxLines(2)
					if index == p.section {
						text.TextColor(palette.AccentInk).FontWeight(600)
					} else if depth == 0 {
						text.TextColor(palette.Text).FontWeight(500)
					} else {
						text.TextColor(palette.Muted)
					}
				}).OnClick(func() { p.section = index })
			}
		})
		chunk := p.chunks[p.section]
		ui.Column(c).Grow(1).MinWidth(0).Height(documentPaneHeight).Gap(10).Children(func() {
			ui.Row(c).FillWidth().Gap(8).AlignItems(ui.Center).Children(func() {
				trail := strings.Join(documentSectionPath(chunk), " › ")
				if trail == "" {
					trail = documentSectionTitle(chunk)
				}
				ui.Text(c, trail).Grow(1).MinWidth(0).FontSize(12.5).TextColor(palette.Muted).MaxLines(2)
				ui.Text(c, fmt.Sprintf("%d / %d", p.section+1, len(p.chunks))).FontSize(12).TextColor(palette.Subtle).Shrink(0)
				nativeGhostButton(c, "chevron-left", "", p.section == 0).Label("上一节").OnClick(func() { p.section-- })
				nativeGhostButton(c, "chevron-right", "", p.section >= len(p.chunks)-1).Label("下一节").OnClick(func() { p.section++ })
			})
			ui.Scroll(c.Key(fmt.Sprintf("document-section-%d", p.section))).Grow(1).FillWidth().Padding(18, 22).Gap(10).Radius(10).Background(palette.Card).Border(1, palette.Border).Children(func() {
				p.blocksView(c, objects(chunk["blocks"]), documentSectionPath(chunk))
			})
		})
	})
}

// blocksView renders paragraphs as text and runs of table cells as grids.
func (p *documentPanel) blocksView(c *ui.Context, blocks []Object, path []string) {
	palette := nativePalette(c)
	heading := ""
	if len(path) > 0 {
		heading = path[len(path)-1]
	}
	for i := 0; i < len(blocks); {
		block := blocks[i]
		if str(block, "kind") != "cell" {
			text := ui.Text(c.Key(str(block, "block_id")), str(block, "text")).FillWidth().FontSize(14).LineHeight(1.65).TextColor(palette.Text).Selectable()
			if str(block, "text") == heading {
				text.FontSize(15.5).FontWeight(600)
			}
			if str(block, "block_id") == p.block {
				text.Background(c.Theme().Selection).ScrollIntoView()
			}
			i++
			continue
		}
		table := fmt.Sprint(block["table"])
		end := i
		for end < len(blocks) && str(blocks[end], "kind") == "cell" && fmt.Sprint(blocks[end]["table"]) == table {
			end++
		}
		p.tableView(c, blocks[i:end])
		i = end
	}
}
func (p *documentPanel) tableView(c *ui.Context, cells []Object) {
	palette := nativePalette(c)
	var rows [][]Object
	last := ""
	for _, cell := range cells {
		if row := fmt.Sprint(cell["row"]); row != last || len(rows) == 0 {
			rows = append(rows, nil)
			last = row
		}
		rows[len(rows)-1] = append(rows[len(rows)-1], cell)
	}
	ui.Column(c.Key("table-"+str(cells[0], "block_id"))).FillWidth().Radius(8).Border(1, palette.BorderStrong).Children(func() {
		for r, row := range rows {
			line := ui.Row(c).FillWidth().AlignItems(ui.Stretch)
			if r > 0 {
				line.BorderWidth(1, 0, 0, 0).BorderColor(palette.Border)
			}
			line.Children(func() {
				for k, cell := range row {
					box := ui.Column(c.Key(str(cell, "block_id"))).Grow(1).Basis(0).MinWidth(0).Padding(7, 10)
					if k > 0 {
						box.BorderWidth(0, 0, 0, 1).BorderColor(palette.Border)
					}
					if r == 0 {
						box.Background(palette.Canvas)
					}
					if str(cell, "block_id") == p.block {
						box.Background(c.Theme().Selection).ScrollIntoView()
					}
					box.Children(func() {
						ui.Text(c, str(cell, "text")).FontSize(13).LineHeight(1.5).TextColor(palette.Text).Selectable()
					})
				}
			})
		}
	})
}

func (p *documentPanel) pagesView(c *ui.Context, a *NativeApp) {
	palette := nativePalette(c)
	ui.Row(c).FillWidth().Wrap().Gap(8).AlignItems(ui.End).Children(func() {
		nativePager(c, p.page, p.page < p.count, false, "原文上一页", "原文下一页",
			func() { p.page--; p.load(a) },
			func() { p.page++; p.load(a) })
		ui.Column(c.Key("document-page-input")).Width(112).Children(func() {
			nativeField(c, "原文页码", &p.pageInput, false)
		})
		ui.Text(c, fmt.Sprintf("/ %d 页", p.count)).FontSize(13).TextColor(palette.Muted).Margin(0, 0, 9, 0)
		nativeButton(c, "跳转原文页码").AlignSelf(ui.End).OnClick(func() {
			page, err := strconv.Atoi(p.pageInput)
			if err != nil || page < 1 || page > p.count {
				p.error = "页码不在文档范围内"
				return
			}
			p.page = page
			p.load(a)
		})
		ui.Column(c.Key("document-zoom")).Width(160).Children(func() {
			if nativeSelect(c, "原文缩放", &p.zoom, []string{"1", "2"}) {
				p.load(a)
			}
		})
	})
	ui.Row(c).FillWidth().Gap(12).AlignItems(ui.Stretch).Children(func() {
		ui.Column(c).Grow(1).Basis(0).MinWidth(0).Height(documentPaneHeight).Radius(10).Background(palette.Card).Border(1, palette.Border).Children(func() {
			if p.bitmap != nil {
				ui.Image(c, p.bitmap).FillWidth().Grow(1).Fit(ui.Contain).Label("招标原件页面")
			}
		})
		ui.Scroll(c.Key(fmt.Sprintf("document-page-text-%d", p.page))).Grow(1).Basis(0).MinWidth(0).Height(documentPaneHeight).Padding(16, 18).Gap(8).Radius(10).Background(palette.Card).Border(1, palette.Border).Children(func() {
			ui.Text(c, fmt.Sprintf("第 %d 页解析文字", p.page)).FontSize(12.5).FontWeight(600).TextColor(palette.Muted)
			found := false
			for _, chunk := range p.chunks {
				if integer(chunk, "page") != p.page {
					continue
				}
				found = true
				if chunk["ocr"] == true {
					nativeBadge(c, "OCR 识别", toneAccent)
				}
				ui.Text(c, str(chunk, "text")).FillWidth().FontSize(13.5).LineHeight(1.65).TextColor(palette.Text).Selectable()
			}
			if !found {
				nativeHint(c, "该页没有提取到文字。")
			}
		})
	})
}

var (
	parseWarningPage    = regexp.MustCompile(`^Page (\d+) `)
	parseWarningSkipped = regexp.MustCompile(`^Word content not parsed for citations: (.+)\.$`)
	parseWarningItem    = regexp.MustCompile(`^(\d+) (.+)$`)
	parseWarningDPI     = regexp.MustCompile(`reduced to (\d+) DPI`)
	parseSkippedLabels  = map[string]string{"headers/footers": "页眉页脚", "text boxes": "文本框", "images": "图片", "footnotes": "脚注", "endnotes": "尾注", "comments": "批注"}
)

// nativeParseWarning shows a parse warning from the server in Chinese.
func nativeParseWarning(warning string) string {
	if match := parseWarningSkipped.FindStringSubmatch(warning); match != nil {
		var items []string
		for _, item := range strings.Split(match[1], ", ") {
			count := ""
			if parts := parseWarningItem.FindStringSubmatch(item); parts != nil {
				count, item = parts[1]+" 处", parts[2]
			}
			if label, ok := parseSkippedLabels[item]; ok {
				item = label
			}
			items = append(items, item+count)
		}
		return "Word 中的" + strings.Join(items, "、") + "未纳入可引用原文，抽取不会使用这些内容。"
	}
	if strings.HasPrefix(warning, "No heading styles found") {
		return "文档未使用标题样式，章节按「第X章」「一、」等编号推断，请核对目录结构。"
	}
	if warning == "The Word document contains no text." {
		return "Word 文档中没有文字内容。"
	}
	if match := parseWarningPage.FindStringSubmatch(warning); match != nil {
		page := "第 " + match[1] + " 页"
		switch {
		case strings.Contains(warning, "OCR returned no additional text"):
			return page + "图片内容未识别出文字，可能不完整，需要人工核对。"
		case strings.Contains(warning, "below the OCR area threshold"):
			return page + "含有未识别的小图片区域，其中文字未解析，需要人工核对。"
		case parseWarningDPI.MatchString(warning):
			return page + "识别分辨率降至 " + parseWarningDPI.FindStringSubmatch(warning)[1] + " DPI，请核对小字。"
		}
	}
	return warning
}
