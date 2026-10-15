package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"
)

type annotationRect struct {
	x, y, width, height string
	pixels              [4]float32
}

func newAnnotationRect(w, h int) annotationRect {
	return annotationRect{x: "0", y: "0", width: strconv.Itoa(w), height: strconv.Itoa(h), pixels: [4]float32{0, 0, float32(w), float32(h)}}
}
func (r annotationRect) object() (Object, error) {
	out := Object{}
	for key, text := range map[string]string{"x": r.x, "y": r.y, "width": r.width, "height": r.height} {
		n, err := strconv.Atoi(text)
		if err != nil || n < 0 || (key == "width" || key == "height") && n == 0 {
			return nil, errors.New("裁剪与框选须使用原页内的整数像素")
		}
		out[key] = n
	}
	return out, nil
}

type annotationPage struct {
	task, id, jobID, observation, request, fingerprint string
	card, row, source, candidate, current, preview     Object
	sources, candidates, releases, releaseJobs         []Object
	sourceCursor, candidateCursor, releaseCursor       string
	sourceNext, candidateNext, releaseNext             string
	sourcePrior, candidatePrior, releasePrior          []string
	crop                                               annotationRect
	boxes                                              []annotationRect
	reviewed, editorOpen, added                        bool
	serial                                             uint64
	sourceImage, candidateImage, releaseImage          signedImagePanel
	document                                           documentPanel
	job                                                jobPanel
	editor                                             responseEditor
}

func newAnnotationPage(r Route) NativePage {
	return &annotationPage{task: r.Params["taskId"], id: r.Params["cardId"], jobID: r.Query.Get("job"), request: requestID()}
}
func (*annotationPage) Title() string { return "证据标注" }
func (p *annotationPage) Dirty() bool { return p.editor.Dirty() || p.observation != "" }
func (p *annotationPage) Close() {
	p.serial++
	p.sourceImage.Close()
	p.candidateImage.Close()
	p.releaseImage.Close()
	p.document.Close()
	p.editor.Close()
	p.job.Close()
	p.preview = nil
	p.card = nil
	p.row = nil
}
func (p *annotationPage) editable() bool {
	return p.card != nil && containsString([]string{"draft", "rejected", "needs_material"}, str(p.card, "state")) && str(p.card, "disposition") != "comply_only"
}
func (p *annotationPage) invalidate() { p.serial++; p.preview = nil; p.reviewed = false }
func (p *annotationPage) Open(a *NativeApp) {
	if p.jobID == "" {
		a.Error = "必须明确指定抽取集合"
		return
	}
	p.readCard(a)
	p.page(a, "source")
	p.page(a, "candidate")
}
func (p *annotationPage) readCard(a *NativeApp) {
	p.invalidate()
	a.Request("GET", "/cards/"+p.id, nil, func(r Result) {
		if str(r.Data, "id") != p.id || str(r.Data, "task_id") != p.task || str(r.Data, "extraction_job_id") != p.jobID {
			a.Error = "响应卡范围不符合契约"
			return
		}
		p.card = r.Data
		a.Request("GET", "/requirements/"+str(r.Data, "requirement_id")+"/review", nil, func(result Result) {
			entry := object(result.Data["requirement"])
			if str(entry, "task_id") != p.task || str(entry, "extraction_job_id") != p.jobID {
				a.Error = "要求范围不符合契约"
				return
			}
			p.row = Object{}
			for key, value := range object(entry["content"]) {
				p.row[key] = value
			}
			p.row["id"] = entry["requirement_id"]
			p.row["review_state"] = entry["state"]
			p.row["card"] = p.card
		})
	})
}
func (p *annotationPage) page(a *NativeApp, kind string) {
	q := url.Values{"limit": {"50"}}
	path := ""
	cursor := ""
	serial := p.serial
	candidateID := str(p.candidate, "id")
	switch kind {
	case "source":
		path = "/tasks/" + p.task + "/evidence-sources"
		q.Set("history", "true")
		cursor = p.sourceCursor
	case "candidate":
		path = "/tasks/" + p.task + "/annotations"
		q.Set("card_id", p.id)
		cursor = p.candidateCursor
	case "release":
		if candidateID == "" {
			return
		}
		path = "/annotations/" + candidateID + "/releases"
		cursor = p.releaseCursor
	}
	if cursor != "" {
		q.Set("cursor", cursor)
	}
	a.Request("GET", path+"?"+q.Encode(), nil, func(r Result) {
		if serial != p.serial {
			return
		}
		if len(r.Items) > 50 {
			a.Error = "标注分页超过上限"
			return
		}
		switch kind {
		case "source":
			if r.Data["history"] != true || integer(r.Data, "returned") != len(r.Items) {
				a.Error = "来源分页不符合契约"
				return
			}
			for _, item := range r.Items {
				if str(item, "task_id") != p.task {
					a.Error = "来源任务不符合契约"
					return
				}
			}
			p.sources = r.Items
			p.sourceNext = str(r.Data, "next_cursor")
		case "candidate":
			if str(r.Data, "task_id") != p.task {
				a.Error = "候选任务不匹配"
				return
			}
			for _, item := range r.Items {
				if str(item, "task_id") != p.task || str(object(object(item["manifest"])["target"]), "card_id") != p.id {
					a.Error = "候选卡片不匹配"
					return
				}
			}
			p.candidates = r.Items
			p.candidateNext = str(r.Data, "next_cursor")
		case "release":
			if str(r.Data, "annotation_id") != candidateID || str(p.candidate, "id") != candidateID {
				a.Error = "确认图范围不匹配"
				return
			}
			p.releases = r.Items
			p.releaseNext = str(r.Data, "next_cursor")
			a.Request("GET", "/tasks/"+p.task+"/jobs?kind=annotation_release&annotation_id="+candidateID+"&limit=20", nil, func(result Result) {
				if str(p.candidate, "id") == candidateID {
					p.releaseJobs = result.Items
				}
			})
		}
	})
}
func (p *annotationPage) turn(a *NativeApp, kind string, forward bool) {
	var cursor, next *string
	var prior *[]string
	switch kind {
	case "source":
		cursor = &p.sourceCursor
		next = &p.sourceNext
		prior = &p.sourcePrior
	case "candidate":
		cursor = &p.candidateCursor
		next = &p.candidateNext
		prior = &p.candidatePrior
	case "release":
		cursor = &p.releaseCursor
		next = &p.releaseNext
		prior = &p.releasePrior
	}
	if forward {
		*prior = append(*prior, *cursor)
		*cursor = *next
	} else {
		*cursor = (*prior)[len(*prior)-1]
		*prior = (*prior)[:len(*prior)-1]
	}
	p.page(a, kind)
}
func (p *annotationPage) choose(a *NativeApp, source Object) {
	p.invalidate()
	p.source = source
	p.candidate = nil
	p.releases = nil
	p.candidateImage.Close()
	p.crop = newAnnotationRect(integer(object(source["preview"]), "width_px"), integer(object(source["preview"]), "height_px"))
	p.boxes = nil
	id := str(source, "id")
	p.sourceImage.Load(a, "证书来源原页", "GET", "/evidence-sources/"+id+"/preview/download-link", "/evidence-sources/"+id+"/preview/download", object(source["preview"]))
}
func (p *annotationPage) input() (Object, error) {
	if p.source == nil || p.card == nil {
		return nil, errors.New("请先读取原页与当前卡片")
	}
	crop, err := p.crop.object()
	if err != nil {
		return nil, err
	}
	rects := []Object{crop}
	boxes := []Object{}
	for _, box := range p.boxes {
		value, err := box.object()
		if err != nil {
			return nil, err
		}
		rects = append(rects, value)
		boxes = append(boxes, value)
	}
	width, height := integer(object(p.source["preview"]), "width_px"), integer(object(p.source["preview"]), "height_px")
	for _, rect := range rects {
		if integer(rect, "x")+integer(rect, "width") > width || integer(rect, "y")+integer(rect, "height") > height {
			return nil, errors.New("裁剪与框选超出原页")
		}
	}
	for _, box := range boxes {
		if integer(box, "x") < integer(crop, "x") || integer(box, "y") < integer(crop, "y") || integer(box, "x")+integer(box, "width") > integer(crop, "x")+integer(crop, "width") || integer(box, "y")+integer(box, "height") > integer(crop, "y")+integer(crop, "height") {
			return nil, errors.New("框选必须在裁剪范围内")
		}
	}
	return Object{"extraction_job_id": p.jobID, "card_id": p.id, "expected_card_revision": p.card["revision"], "source": Object{"kind": "certificate_page", "evidence_source_id": p.source["id"]}, "plan": Object{"crop": crop, "boxes": boxes}}, nil
}
func (p *annotationPage) inspect(a *NativeApp, id string) {
	p.invalidate()
	serial := p.serial
	p.candidate = nil
	p.candidateImage.Close()
	p.releases = nil
	p.releaseJobs = nil
	a.Request("GET", "/annotations/"+id, nil, func(r Result) {
		if serial != p.serial {
			return
		}
		candidate := object(r.Data["candidate"])
		target := object(object(candidate["manifest"])["target"])
		if str(candidate, "task_id") != p.task || str(target, "card_id") != p.id || str(target, "extraction_job_id") != p.jobID {
			a.Error = "候选图范围不符合契约"
			return
		}
		p.candidate = candidate
		p.current = object(r.Data["current"])
		p.candidateImage.Load(a, "实际标注候选图", "GET", "/annotations/"+id+"/preview", "/annotations/"+id+"/content", object(object(candidate["rendering"])["image"]))
		p.page(a, "release")
	})
}
func (p *annotationPage) View(c *ui.Context, a *NativeApp) {

	if p.card == nil || p.row == nil {
		return
	}
	editable := p.editable()
	active := containsString([]string{"queued", "running"}, str(p.job.data, "status"))
	nativeText(c, "当前仅支持真实证书页标注。资料／合同附件标注与厂商来源尚未启用。")
	nativeButton(c, "查看任务附件准备状态").OnClick(func() { a.Navigate("/org/tasks/" + p.task + "/attachments") })
	nativeText(c, "审阅职责 "+str(p.card, "review_domain")+" / 要求状态 "+str(p.row, "review_state")+" / 卡片修订 "+str(p.card, "revision"))
	nativeText(c, "要求确认、响应审阅与标注生成各自独立。")
	ui.Row(c).FillWidth().Wrap().Gap(8).Children(func() {
		nativeButton(c, "重新核对卡片修订").Disabled(a.Busy || active).OnClick(func() { p.readCard(a) })
		nativeButton(c, "核对要求确认").OnClick(func() {
			a.Navigate("/org/tasks/" + p.task + "/requirements?job=" + p.jobID + "&requirement=" + str(p.row, "id"))
		})
		nativeButton(c, "核对招标原文").OnClick(func() {
			source := object(p.card["source"])
			p.document.Open(a, str(source, "document_id"), integer(source, "page"), str(object(source["location"]), "block_id"))
		})
	})
	p.document.View(c, a)
	nativeHeading(c, "已有证书页来源")
	for _, source := range p.sources {
		nativeButton(c.Key(str(source, "id")), str(object(source["original"]), "name")+" 第 "+str(source, "page")+" 页").Disabled(a.Busy || active || source["active_selection"] != true).OnClick(func() { p.choose(a, source) })
	}
	p.pager(c, a, "source", len(p.sourcePrior), p.sourceNext)
	if p.source != nil {
		nativeText(c, "固定来源 SHA-256 "+str(object(p.source["preview"]), "sha256"))
		nativeText(c, "原件 SHA-256 "+str(object(p.source["original"]), "sha256")+" / 原页渲染时间 "+str(p.source, "rendered_at"))
		p.sourceCanvas(c)
		if annotationRectFields(c, "裁剪", &p.crop, !editable || a.Busy || active) {
			p.invalidate()
		}
		for i := range p.boxes {
			if annotationRectFields(c, fmt.Sprintf("框选 %d", i+1), &p.boxes[i], !editable || a.Busy || active) {
				p.invalidate()
			}
			nativeButton(c.Key(fmt.Sprint(i)), "移除框选").Disabled(!editable || a.Busy || active).OnClick(func() { p.boxes = append(p.boxes[:i], p.boxes[i+1:]...); p.invalidate() })
		}
		nativeButton(c, "添加框选").Disabled(!editable || a.Busy || active || len(p.boxes) >= 20).OnClick(func() { p.boxes = append(p.boxes, p.crop); p.invalidate() })
		nativeButton(c, "重置范围").Disabled(!editable || a.Busy || active).OnClick(func() {
			p.crop = newAnnotationRect(integer(object(p.source["preview"]), "width_px"), integer(object(p.source["preview"]), "height_px"))
			p.boxes = nil
			p.invalidate()
		})
		nativeButton(c, "预览标注").Disabled(!editable || a.Busy || active || p.sourceImage.bitmap == nil).OnClick(func() {
			input, err := p.input()
			if err != nil {
				a.Error = err.Error()
				return
			}
			p.invalidate()
			serial := p.serial
			a.Request("POST", "/tasks/"+p.task+"/annotations", Object{"input": input, "dry_run": true}, func(r Result) {
				if serial != p.serial {
					return
				}
				manifest := object(r.Data["manifest"])
				if str(object(manifest["target"]), "card_id") != p.id || str(object(object(manifest["source"])["source_png"]), "sha256") != str(object(p.source["preview"]), "sha256") {
					a.Error = "预览来源与当前选择不符"
					return
				}
				p.preview = r.Data
			})
		})
		if p.preview != nil {
			nativeText(c, "预览已绑定当前来源与卡片修订；不调用模型。输出 "+str(object(p.preview["predicted_output"]), "width_px")+" × "+str(object(p.preview["predicted_output"]), "height_px"))
			for _, code := range stringList(p.preview["blocker_codes"]) {
				nativeText(c, code)
			}
			ui.Checkbox(c, &p.reviewed, "已核对原页与标注范围")
		}
		nativePrimaryButton(c, "生成标注材料").Disabled(!editable || a.Busy || active || p.sourceImage.bitmap == nil || !p.reviewed || p.preview == nil || len(stringList(p.preview["blocker_codes"])) > 0).OnClick(func() {
			input, err := p.input()
			if err != nil {
				a.Error = err.Error()
				return
			}
			fingerprint, err := json.Marshal(Object{"input": input, "hash": p.preview["input_hash"]})
			if err != nil {
				a.Error = err.Error()
				return
			}
			if p.fingerprint != string(fingerprint) {
				p.request = requestID()
				p.fingerprint = string(fingerprint)
			}
			body := Object{"input": input, "dry_run": false, "expected_input_hash": p.preview["input_hash"], "reviewed_source_png_sha256": object(p.source["preview"])["sha256"], "request_id": p.request, "retry": false}
			p.invalidate()
			a.Request("POST", "/tasks/"+p.task+"/annotations", body, func(r Result) {
				p.job.Load(a, str(r.Data, "job_id"), func() {
					if id := str(object(p.job.data["result"]), "annotation_id"); id != "" {
						p.inspect(a, id)
					}
					p.page(a, "candidate")
				})
			})
		})
	}
	p.job.View(c, a)
	if p.candidate != nil {
		p.candidateImage.View(c, a)
		nativeText(c, "未确认材料 / "+str(p.current, "validity"))
		for _, code := range stringList(p.current["warning_codes"]) {
			nativeText(c, code)
		}
		nativeButton(c, "用于此响应卡").Disabled(!editable || p.candidateImage.bitmap == nil || str(p.current, "validity") != "current").OnClick(func() {
			p.editorOpen = true
			p.added = false
			p.editor.Open(a, p.task, p.jobID, p.row, func() { p.readCard(a); p.page(a, "release") })
		})
		for _, job := range p.releaseJobs {
			nativeText(c, "确认图作业 "+str(job, "status")+" / "+str(job, "id"))
			if containsString([]string{"failed", "cancelled"}, str(job, "status")) {
				nativeButton(c.Key(str(job, "id")), "重试确认图生成").Disabled(a.Busy || str(p.current, "validity") != "current").OnClick(func() { p.retryRelease(a, job) })
			}
		}
		for _, release := range p.releases {
			nativeText(c, str(release, "decision_validity")+" / 可用确认图 "+str(release, "releasable"))
			if release["releasable"] == true {
				nativeButton(c.Key(str(release, "id")), "核对确认图").OnClick(func() {
					id := str(release, "id")
					p.releaseImage.Load(a, "已确认标注图", "GET", "/annotation-releases/"+id+"/preview", "/annotation-releases/"+id+"/content", object(object(release["rendering"])["image"]))
				})
			} else {
				for _, code := range stringList(release["blocker_codes"]) {
					nativeText(c, code)
				}
			}
		}
		p.pager(c, a, "release", len(p.releasePrior), p.releaseNext)
		nativeButton(c, "重新核对确认图").OnClick(func() { p.page(a, "release") })
		p.releaseImage.View(c, a)
	}
	nativeHeading(c, "已有标注材料")
	for _, candidate := range p.candidates {
		nativeButton(c.Key(str(candidate, "id")), "核对标注图 "+str(candidate, "id")).Disabled(a.Busy || active).OnClick(func() { p.inspect(a, str(candidate, "id")) })
		nativeText(c, "未确认材料 / "+nativeDisplay(str(candidate, "created_at")))
	}
	p.pager(c, a, "candidate", len(p.candidatePrior), p.candidateNext)
	if p.editorOpen {
		nativeField(c, "视觉观察说明", &p.observation, true)
		nativeButton(c, "加入本次响应编辑").Disabled(p.added || !p.editor.editable() || strings.TrimSpace(p.observation) == "" || len(p.editor.candidates) >= 100).OnClick(func() {
			candidate := p.candidate
			mapping := object(object(object(candidate["rendering"])["canvas"])["mapping"])
			p.editor.kind = "evidence"
			p.editor.candidates = append(p.editor.candidates, Object{"kind": "image_region", "asset_id": candidate["asset_id"], "rendition_id": candidate["rendition_id"], "expected_image_sha256": object(object(candidate["rendering"])["image"])["sha256"], "region": Object{"x": 0, "y": 0, "width": mapping["content_width"], "height": mapping["content_height"]}, "claim_scope": "document_excerpt", "visual_observation": strings.TrimSpace(p.observation)})
			p.editor.markEdited()
			p.added = true
		})
		p.editor.View(c, a)
	}
}
func annotationRectFields(c *ui.Context, label string, r *annotationRect, disabled bool) bool {
	changed := false
	nativeHeading(c, label+"（原页像素）")
	for i, field := range []struct {
		name  string
		value *string
	}{{"横坐标", &r.x}, {"纵坐标", &r.y}, {"宽度", &r.width}, {"高度", &r.height}} {
		ui.Text(c, label+field.name)
		if ui.TextInput(c, field.value).Label(label + field.name).ReadOnly(disabled).Changed() {
			changed = true
			n, err := strconv.Atoi(*field.value)
			if err != nil {
				r.pixels[i] = 0
			} else {
				r.pixels[i] = float32(n)
			}
		}
	}
	return changed
}
func (p *annotationPage) pager(c *ui.Context, a *NativeApp, kind string, prior int, next string) {
	nativePager(c.Key(kind+"pager"), prior+1, next != "", a.Busy, "上一页 "+kind, "下一页 "+kind,
		func() { p.turn(a, kind, false) },
		func() { p.turn(a, kind, true) })
}
func (p *annotationPage) retryRelease(a *NativeApp, job Object) {
	id := str(p.candidate, "id")
	a.Request("GET", "/jobs/"+str(job, "id"), nil, func(r Result) {
		pin := object(r.Data["result"])
		if !containsString([]string{"failed", "cancelled"}, str(r.Data, "status")) || str(r.Data, "kind") != "annotation_release" || str(pin, "annotation_id") != id || str(pin, "card_id") != p.id || str(pin, "expected_card_revision") != str(p.card, "revision") || str(pin, "expected_approval_binding_hash") == "" {
			a.Error = "没有当前完整审批绑定的失败确认图作业，请重新核对审批"
			return
		}
		a.Request("POST", "/annotations/"+id+"/releases", Object{"evidence_id": pin["evidence_id"], "card_id": pin["card_id"], "expected_card_revision": pin["expected_card_revision"], "expected_approval_binding_hash": pin["expected_approval_binding_hash"], "job_id": job["id"], "request_id": requestID(), "retry": true}, func(Result) { p.page(a, "release") })
	})
}

func (p *annotationPage) sourceCanvas(c *ui.Context) {
	if p.sourceImage.bitmap == nil {
		nativeText(c, "正在读取并核验原页")
		return
	}
	width, height := float32(integer(object(p.source["preview"]), "width_px")), float32(integer(object(p.source["preview"]), "height_px"))
	if width <= 0 || height <= 0 {
		return
	}
	scale := float32(600) / width
	ui.Column(c).Width(600).Height(height * scale).Children(func() {
		ui.Image(c, p.sourceImage.bitmap).Width(600).Height(height * scale).Fit(ui.Contain).Label("证书原页与拟标注范围")
		draw := func(rect annotationRect, crop bool) {
			v := rect.pixels
			if v[2] <= 0 || v[3] <= 0 {
				return
			}
			color := ui.Hex("#DC2626")
			if crop {
				color = ui.Hex("#245DD6")
			}
			box := ui.Column(c).Absolute().Left(v[0]*scale).Top(v[1]*scale).Width(v[2]*scale).Height(v[3]*scale).Border(2, color)
			if crop {
				box.BorderStyle(ui.BorderDashed)
			}
		}
		if p.preview != nil {
			draw(p.crop, true)
		}
		for _, box := range p.boxes {
			draw(box, false)
		}
	})
}
