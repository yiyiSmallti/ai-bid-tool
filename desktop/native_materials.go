package main

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"
)

type materialPanel struct {
	task, chosen, field, quote, pageSource, pageQuote, certificate, page, kind, libraryChoice, lot string
	selections, sources, files, library                                                            []Object
	image                                                                                          *ui.Bitmap
	imageTitle                                                                                     string
	generation                                                                                     uint64
	cancel                                                                                         context.CancelFunc
}

var materialFields = map[string][]string{
	"product":     {"name", "vendor", "model", "model_version", "official_url", "whitepaper_url"},
	"feature":     {"product_id", "name", "description", "status"},
	"certificate": {"kind", "name", "number", "valid_from", "valid_until"},
	"org_profile": {"name", "address", "phone", "legal_representative", "credit_code", "registration_details", "performance_summary", "standard_wording"},
}
var materialPaths = map[string]string{"product": "products", "feature": "features", "certificate": "certificates", "org_profile": "profiles"}
var materialIDs = map[string]string{"product": "product_id", "feature": "feature_id", "certificate": "certificate_id", "org_profile": "profile_id"}

func (m *materialPanel) Close() {
	m.generation++
	if m.cancel != nil {
		m.cancel()
	}
	m.image = nil
	m.quote = ""
	m.pageQuote = ""
}
func (m *materialPanel) Load(a *NativeApp, task string) {
	m.task = task
	m.page = "1"
	if m.kind == "" {
		m.kind = "product"
	}
	a.Do(func(ctx context.Context) (func(), error) {
		var selections []Object
		for _, kind := range []string{"product", "feature", "certificate", "org_profile"} {
			r, err := a.Client.Request(ctx, "GET", "/tasks/"+task+"/"+materialPaths[kind]+"?history=true", nil)
			if err != nil {
				return nil, err
			}
			active := stringList(r.Data["active_snapshot_ids"])
			for _, item := range r.Items {
				item["kind"] = kind
				item["active"] = containsString(active, str(item, "id"))
				selections = append(selections, item)
			}
		}
		sources, err := a.Client.Request(ctx, "GET", "/tasks/"+task+"/evidence-sources?history=true", nil)
		if err != nil {
			return nil, err
		}
		files, err := a.Client.Request(ctx, "GET", "/tasks/"+task+"/certificate-files", nil)
		return func() { m.selections = selections; m.sources = sources.Items; m.files = files.Items }, err
	})
}
func (m *materialPanel) preview(a *NativeApp, path, title string) {
	m.Close()
	m.imageTitle = title
	ctx, cancel := context.WithCancel(a.ctx)
	m.cancel = cancel
	generation, epoch := m.generation, a.epoch
	go func() {
		link, err := a.Client.Request(ctx, "GET", path, nil)
		var image *ui.Bitmap
		if err == nil {
			var data []byte
			data, err = a.Client.Binary(ctx, str(link.Data, "url"))
			if err == nil {
				image, err = ui.DecodeBitmap(data)
			}
		}
		a.Window.Update(func() {
			if ctx.Err() != nil || generation != m.generation || epoch != a.epoch {
				return
			}
			if err != nil {
				a.Error = err.Error()
				return
			}
			m.image = image
		})
	}()
}
func (m *materialPanel) View(c *ui.Context, a *NativeApp, editable bool, add func(Object), changed func()) {
	if m.task == "" {
		return
	}
	nativeHeading(c, "任务固定材料")
	nativeText(c, "资源字段是声明；产品网址不是网页取证，功能状态与业绩文字不是交付或合同证明。")
	nativeSelect(c, "资源种类", &m.kind, []string{"product", "feature", "certificate", "org_profile"})
	nativeField(c, "分包（可选）", &m.lot, false)
	nativeButton(c, "读取资源库").OnClick(func() {
		m.library = nil
		m.libraryChoice = ""
		a.Request("GET", "/resources/"+materialPaths[m.kind], nil, func(r Result) { m.library = r.Items })
	})
	choices := []string{""}
	for _, item := range m.library {
		choices = append(choices, str(item, "id"))
	}
	nativeSelect(c, "库中精确修订", &m.libraryChoice, choices)
	nativeButton(c, "固定所选版本到任务").Disabled(a.Busy || m.libraryChoice == "").OnClick(func() {
		for _, item := range m.library {
			if str(item, "id") != m.libraryChoice {
				continue
			}
			a.Confirm("固定资源版本", "替换同一资源和分包的旧选择后，依赖响应可能失效并需要重新审阅。", func() {
				a.Request("POST", "/tasks/"+m.task+"/"+materialPaths[m.kind], Object{materialIDs[m.kind]: item[materialIDs[m.kind]], "revision": item["revision"], "lot": nullable(m.lot)}, func(Result) { m.Load(a, m.task); changed() })
			})
			break
		}
	})
	choices = []string{""}
	var chosen Object
	for _, item := range m.selections {
		choices = append(choices, str(item, "id"))
		if str(item, "id") == m.chosen {
			chosen = item
		}
	}
	if nativeChoice(c, &m.chosen, choices).Label("固定材料选择").Changed() {
		m.field = ""
		m.quote = ""
	}
	if chosen != nil {
		nativeText(c, str(object(chosen["data"]), "name")+" / 修订 "+str(chosen, "revision")+" / 有效选择 "+str(chosen, "active"))
		fields := []string{""}
		for _, field := range materialFields[str(chosen, "kind")] {
			if isString(object(chosen["data"])[field]) {
				fields = append(fields, field)
			}
		}
		if nativeChoice(c, &m.field, fields).Label("可摘录字段").Changed() {
			m.quote = ""
		}
		nativeText(c, str(object(chosen["data"]), m.field))
		nativeField(c, "字段逐字摘录", &m.quote, true)
		nativeButton(c, "添加字段材料").Disabled(!editable || chosen["active"] != true || m.field == "" || strings.TrimSpace(m.quote) == "").OnClick(func() {
			if !strings.Contains(str(object(chosen["data"]), m.field), m.quote) {
				a.Error = "摘录须逐字出现在固定字段中"
				return
			}
			add(Object{"kind": chosen["kind"], "selection_id": chosen["id"], "field_path": m.field, "quote": m.quote})
			m.quote = ""
		})
	}
	nativeHeading(c, "证书原件与页来源")
	choices = []string{""}
	for _, file := range m.files {
		choices = append(choices, str(file, "id"))
		nativeText(c, str(object(file["data"]), "name")+" / 固定修订 "+str(file, "revision"))
		if object(file["file"]) != nil {
			nativeButton(c.Key(str(file, "id")), "下载证书原件").OnClick(func() {
				signedDownload(a, "/resources/certificates/revisions/"+str(file, "certificate_revision_id")+"/file/download-link", str(object(file["file"]), "name"))
			})
		}
	}
	nativeSelect(c, "固定证书", &m.certificate, choices)
	nativeField(c, "真实页码", &m.page, false)
	nativeButton(c, "归档证书页来源").Disabled(a.Busy || m.certificate == "").OnClick(func() {
		page, err := strconv.Atoi(m.page)
		if err != nil || page < 1 {
			a.Error = "请输入真实正整数页码"
			return
		}
		a.Request("POST", "/tasks/"+m.task+"/evidence-sources", Object{"task_certificate_id": m.certificate, "page": page}, func(Result) { m.Load(a, m.task); changed() })
	})
	choices = []string{""}
	for _, source := range m.sources {
		name := str(object(source["original"]), "name") + " 第 " + str(source, "page") + " 页"
		nativeText(c, name+" / 有效选择 "+str(source, "active_selection"))
		nativeButton(c.Key(str(source, "id")), "预览真实证书页").OnClick(func() { m.preview(a, "/evidence-sources/"+str(source, "id")+"/preview/download-link", name) })
		if source["active_selection"] == true {
			choices = append(choices, str(source, "id"))
		}
	}
	nativeSelect(c, "已有证书页来源", &m.pageSource, choices)
	nativeField(c, "证书页逐字摘录", &m.pageQuote, true)
	nativeButton(c, "添加页材料").Disabled(!editable || m.pageSource == "" || strings.TrimSpace(m.pageQuote) == "").OnClick(func() {
		add(Object{"kind": "certificate_pdf_page", "evidence_source_id": m.pageSource, "quote": m.pageQuote})
		m.pageQuote = ""
	})
	if m.image != nil {
		nativeHeading(c, m.imageTitle)
		ui.Image(c, m.image).FillWidth().Height(600).Fit(ui.Contain).Label(m.imageTitle)
		nativeButton(c, "关闭材料预览").OnClick(m.Close)
	}
	nativeText(c, fmt.Sprintf("固定材料 %d 项，证书页来源 %d 项。来源归档不代表证据已获人工确认。", len(m.selections), len(m.sources)))
}
