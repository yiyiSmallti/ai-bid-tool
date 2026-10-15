package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"
)

type assessmentCitation struct {
	task     string
	query    url.Values
	data     Object
	serial   uint64
	document documentPanel
}

func (c *assessmentCitation) Close() { c.serial++; c.data = nil; c.document.Close() }
func (c *assessmentCitation) Open(a *NativeApp, task, parent, parentID, part, entry, origin string, index int) {
	c.Close()
	c.task = task
	c.query = url.Values{"parent_kind": {parent}, "parent_id": {parentID}, "part": {part}, "entry_id": {entry}, "origin": {origin}, "citation_index": {strconv.Itoa(index)}, "limit": {"4000"}}
	c.load(a, 0)
}
func (c *assessmentCitation) load(a *NativeApp, offset int) {
	c.serial++
	serial := c.serial
	c.query.Set("offset", strconv.Itoa(offset))
	a.Request("GET", "/tasks/"+c.task+"/assessment-citation?"+c.query.Encode(), nil, func(r Result) {
		if serial == c.serial {
			c.data = r.Data
		}
	})
}
func (s *assessmentCitation) View(c *ui.Context, a *NativeApp) {
	if s.data == nil {
		return
	}
	nativeHeading(c, "固定引用原文")
	nativeText(c, str(object(s.data["window"]), "text"))
	window := object(s.data["window"])
	nativeText(c, fmt.Sprintf("窗口起点 %d / 全文 %d 字符", integer(window, "offset"), integer(window, "total_characters")))
	if window["next_offset"] != nil {
		nativeButton(c, "继续展开原文").OnClick(func() { s.load(a, integer(window, "next_offset")) })
	}
	if str(s.data, "kind") == "tender" {
		nativeButton(c, "按原位置预览招标文件").OnClick(func() {
			s.document.Open(a, str(s.data, "document_id"), integer(s.data, "page"), str(object(s.data["location"]), "block_id"))
		})
	}
	fix := object(s.data["fix"])
	nativeButton(c, "前往响应卡修改").OnClick(func() {
		a.Navigate("/org/tasks/" + s.task + "/review?job=" + str(fix, "extraction_job_id") + "&requirement=" + str(fix, "requirement_id"))
	})
	nativeButton(c, "关闭引用原文").OnClick(s.Close)
	s.document.View(c, a)
}

type rubricReplacement struct {
	task, id, part, source string
	form, baseline         Object
	graph                  map[string][]Object
	index                  int
	valid, opened          bool
	serial                 uint64
}

func (r *rubricReplacement) Dirty() bool {
	if r.form == nil {
		return false
	}
	a, _ := json.Marshal(r.form)
	b, _ := json.Marshal(r.baseline)
	return string(a) != string(b)
}
func pickFields(row Object, fields []string) Object {
	value := Object{}
	for _, key := range fields {
		value[key] = row[key]
	}
	return value
}
func cloneObject(value Object) Object {
	data, _ := json.Marshal(value)
	var copy Object
	_ = json.Unmarshal(data, &copy)
	return copy
}
func (r *rubricReplacement) Load(a *NativeApp, task, id string) {
	if r.Dirty() {
		a.Confirm("重新载入完整规则", "将丢弃当前未保存的完整修订，继续？", func() { r.form = nil; r.Load(a, task, id) })
		return
	}
	r.task = task
	r.id = id
	r.opened = true
	r.valid = false
	r.part = "sections"
	r.index = 0
	r.serial++
	serial := r.serial
	base := "/tasks/" + task + "/score-rubrics/" + id
	a.Do(func(ctx context.Context) (func(), error) {
		header, err := a.Client.Request(ctx, "GET", base+"?view=console&part=summary", nil)
		if err != nil {
			return nil, err
		}
		graph := map[string][]Object{}
		snapshot := ""
		for _, part := range []string{"sections", "items", "coverage"} {
			cursor := ""
			seen := map[string]bool{}
			total := -1
			for {
				q := url.Values{"view": {"console"}, "part": {part}, "limit": {"100"}}
				if cursor != "" {
					q.Set("cursor", cursor)
				}
				page, err := a.Client.Request(ctx, "GET", base+"?"+q.Encode(), nil)
				if err != nil {
					return nil, err
				}
				if page.Data["parent_revision"] != header.Data["revision"] || str(page.Data, "validity") != "current" || str(page.Data, "parent_id") != id || str(page.Data, "task_id") != task || integer(page.Data, "returned") != len(page.Items) {
					return nil, errors.New("评分规则快照已变化")
				}
				if snapshot == "" {
					snapshot = str(page.Data, "snapshot")
				}
				if snapshot != str(page.Data, "snapshot") {
					return nil, errors.New("评分规则跨页快照不一致")
				}
				if total >= 0 && total != integer(page.Data, "total") {
					return nil, errors.New("评分规则总数已变化")
				}
				total = integer(page.Data, "total")
				graph[part] = append(graph[part], page.Items...)
				cursor = str(page.Data, "next_cursor")
				if cursor == "" {
					break
				}
				if seen[cursor] {
					return nil, errors.New("重复分页游标")
				}
				seen[cursor] = true
			}
			if len(graph[part]) != total {
				return nil, errors.New("评分规则快照不完整")
			}
		}
		latest, err := a.Client.Request(ctx, "GET", base+"?view=console&part=summary", nil)
		if err != nil {
			return nil, err
		}
		if latest.Data["revision"] != header.Data["revision"] || latest.Data["input_hash"] != header.Data["input_hash"] || str(latest.Data, "validity") != "current" || str(latest.Data, "state") == "superseded" {
			return nil, errors.New("评分规则已失效或被替代")
		}
		form := pickFields(header.Data, []string{"overall_aggregation", "overall_rule_text", "overall_score_range", "overall_cap"})
		form["expected_revision"] = header.Data["revision"]
		form["expected_input_hash"] = header.Data["input_hash"]
		form["reason"] = ""
		sections := []Object{}
		items := []Object{}
		coverage := []Object{}
		sectionKeys, itemKeys := map[string]string{}, map[string]string{}
		for _, row := range graph["sections"] {
			sectionKeys[str(row, "id")] = str(row, "key")
			value := pickFields(row, []string{"key", "title", "order", "aggregation", "aggregation_rule_text", "score_range", "weight", "cap", "included_in_overall_total", "ambiguity_reason"})
			value["source_section_id"] = row["id"]
			sources := []Object{}
			for _, source := range objects(row["sources"]) {
				sources = append(sources, pickFields(source, []string{"requirement_id", "quote"}))
			}
			value["sources"] = sources
			sections = append(sections, value)
		}
		for _, row := range graph["items"] {
			itemKeys[str(row, "id")] = str(row, "key")
			value := pickFields(row, []string{"requirement_id", "key", "title", "rule_text", "order", "assessment_mode", "score_range", "weight", "ambiguity_reason"})
			value["source_item_id"] = row["id"]
			value["section_key"] = sectionKeys[str(row, "section_id")]
			items = append(items, value)
		}
		for _, row := range graph["coverage"] {
			value := pickFields(row, []string{"requirement_id", "disposition", "canonical_requirement_id", "reason"})
			keys := []string{}
			for _, id := range stringList(row["rubric_item_ids"]) {
				keys = append(keys, itemKeys[id])
			}
			value["rubric_item_keys"] = keys
			coverage = append(coverage, value)
		}
		form["sections"] = sections
		form["items"] = items
		form["coverage"] = coverage
		return func() {
			if serial != r.serial {
				return
			}
			r.graph = graph
			r.form = form
			r.baseline = cloneObject(form)
			r.valid = true
		}, nil
	})
}
func objectField(c *ui.Context, label string, row Object, key string, multiline bool) {
	value := str(row, key)
	ui.Text(c, label)
	var field ui.Element
	if multiline {
		field = ui.TextArea(c, &value).Label(label).Height(80)
	} else {
		field = ui.TextInput(c, &value).Label(label)
	}
	if field.Changed() {
		row[key] = nullable(value)
	}
}
func objectSelect(c *ui.Context, label string, row Object, key string, options []string) {
	value := str(row, key)
	if nativeChoice(c, &value, options).Label(label).Changed() {
		row[key] = value
	}
}
func scoreRange(c *ui.Context, row Object, key, label string) {
	rangeValue := object(row[key])
	enabled := rangeValue != nil
	if ui.Checkbox(c, &enabled, label).Changed() {
		if enabled {
			row[key] = Object{"minimum": "0", "maximum": "0"}
		} else {
			row[key] = nil
		}
		rangeValue = object(row[key])
	}
	if rangeValue != nil {
		objectField(c, label+"最低值", rangeValue, "minimum", false)
		objectField(c, label+"最高值", rangeValue, "maximum", false)
	}
}
func (r *rubricReplacement) View(c *ui.Context, a *NativeApp, saved func(string)) {
	if !r.opened {
		return
	}
	nativeHeading(c, "完整修订评分规则")
	nativeText(c, "创建新版本后，职责分类、条目确认和覆盖决定需要重新完成。固定招标原文不可编辑。")
	nativeButton(c, "取消完整修订").Disabled(a.Busy).OnClick(func() {
		close := func() { r.serial++; *r = rubricReplacement{} }
		if r.Dirty() {
			a.Confirm("关闭修订", "丢弃未保存的完整修订？", close)
		} else {
			close()
		}
	})
	nativeButton(c, "重新读取完整评分规则快照").Disabled(a.Busy).OnClick(func() { r.Load(a, r.task, r.id) })
	if !r.valid {
		nativeText(c, "当前完整快照尚未读取或已经失效。编辑内容保留；重新读取并核对后才能保存。")
	}
	if r.form == nil {
		return
	}
	ui.Column(c).Gap(10).FillWidth().Height(440).Disabled(a.Busy).Children(func() {
		ui.Scroll(c.Key("rubric-fields")).Grow(1).FillWidth().Padding(0, 12, 12, 0).Gap(14).Children(func() {
			if nativeChoice(c, &r.part, []string{"sections", "items", "coverage", "overall"}).Label("完整修订分区").Changed() {
				r.index = 0
				r.source = ""
			}
			aggregations := []string{"sum", "weighted_sum", "capped_sum", "formula", "non_additive"}
			if r.part == "overall" {
				objectSelect(c, "总分合计规则", r.form, "overall_aggregation", aggregations)
				objectField(c, "总分规则原文", r.form, "overall_rule_text", true)
				objectField(c, "总分封顶", r.form, "overall_cap", false)
				scoreRange(c, r.form, "overall_score_range", "设置总分范围")
			} else {
				entries := objects(r.form[r.part])
				if r.index >= len(entries) {
					r.index = max(0, len(entries)-1)
				}
				ui.Row(c).Wrap().Gap(8).Children(func() {
					nativeButton(c, "上一条修订").Disabled(r.index == 0).OnClick(func() { r.index-- })
					nativeText(c, fmt.Sprintf("%d / %d", r.index+1, len(entries)))
					nativeButton(c, "下一条修订").Disabled(r.index+1 >= len(entries)).OnClick(func() { r.index++ })
					if r.part != "coverage" {
						nativeButton(c, "新增分节或条目").OnClick(func() { r.add() })
						nativeDangerButton(c, "移除当前项").Disabled(len(entries) == 0).OnClick(func() {
							if r.part == "sections" {
								for _, item := range objects(r.form["items"]) {
									if str(item, "section_key") == str(entries[r.index], "key") {
										a.Error = "此分节仍有关联条目，请先调整所属分节"
										return
									}
								}
							}
							r.form[r.part] = append(entries[:r.index], entries[r.index+1:]...)
							r.index = max(0, r.index-1)
						})
					}
				})
				if len(entries) > 0 {
					row := entries[r.index]
					if r.part == "sections" {
						for i, source := range objects(row["sources"]) {
							nativeText(c, str(source, "quote"))
							nativeDangerButton(c.Key(fmt.Sprint(i)), "移除此分节原文").Disabled(len(objects(row["sources"])) <= 1).OnClick(func() { sources := objects(row["sources"]); row["sources"] = append(sources[:i], sources[i+1:]...) })
						}
						choices := []string{""}
						for _, entry := range r.graph["coverage"] {
							choices = append(choices, str(entry, "requirement_id"))
						}
						nativeSelect(c, "添加固定分节原文", &r.source, choices)
						nativeButton(c, "添加此原文").Disabled(r.source == "").OnClick(func() {
							for _, entry := range r.graph["coverage"] {
								if str(entry, "requirement_id") == r.source {
									sources := objects(row["sources"])
									row["sources"] = append(sources, Object{"requirement_id": r.source, "quote": object(entry["source"])["quote"]})
									r.source = ""
									break
								}
							}
						})
					} else {
						for _, entry := range r.graph["coverage"] {
							if str(entry, "requirement_id") == str(row, "requirement_id") {
								nativeText(c, str(object(entry["source"]), "quote"))
							}
						}
						if r.part == "items" && row["source_item_id"] == nil {
							choices := []string{}
							for _, entry := range r.graph["coverage"] {
								choices = append(choices, str(entry, "requirement_id"))
							}
							objectSelect(c, "固定评分要求", row, "requirement_id", choices)
						}
					}
					if r.part == "coverage" {
						objectSelect(c, "覆盖提议", row, "disposition", []string{"pending", "mapped", "duplicate", "excluded"})
						if str(row, "disposition") == "mapped" {
							keys := stringList(row["rubric_item_keys"])
							for _, item := range objects(r.form["items"]) {
								if item["requirement_id"] != row["requirement_id"] {
									continue
								}
								key := str(item, "key")
								checked := containsString(keys, key)
								if ui.Checkbox(c.Key(key), &checked, str(item, "title")).Changed() {
									if checked {
										keys = append(keys, key)
									} else {
										for i, value := range keys {
											if value == key {
												keys = append(keys[:i], keys[i+1:]...)
												break
											}
										}
									}
									row["rubric_item_keys"] = keys
								}
							}
						} else {
							row["rubric_item_keys"] = []string{}
						}
						if str(row, "disposition") == "duplicate" {
							choices := []string{""}
							for _, entry := range r.graph["coverage"] {
								if entry["requirement_id"] != row["requirement_id"] {
									choices = append(choices, str(entry, "requirement_id"))
								}
							}
							objectSelect(c, "规范要求", row, "canonical_requirement_id", choices)
						} else {
							row["canonical_requirement_id"] = nil
						}
						objectField(c, "覆盖理由", row, "reason", true)
					} else {
						if row["source_item_id"] == nil && row["source_section_id"] == nil {
							objectField(c, "稳定键", row, "key", false)
						} else {
							nativeText(c, "固定稳定键："+str(row, "key"))
						}
						objectField(c, "标题", row, "title", false)
						order := strconv.Itoa(integer(row, "order"))
						if ui.TextInput(c, &order).Label("顺序").Changed() {
							if number, err := strconv.Atoi(order); err == nil {
								row["order"] = number
							}
						}
						if r.part == "sections" {
							objectSelect(c, "分节合计规则", row, "aggregation", aggregations)
							objectField(c, "固定规则原文", row, "aggregation_rule_text", true)
							objectField(c, "分节封顶", row, "cap", false)
							included := row["included_in_overall_total"] == true
							if ui.Checkbox(c, &included, "此分节进入总分").Changed() {
								row["included_in_overall_total"] = included
							}
						} else {
							choices := []string{}
							for _, section := range objects(r.form["sections"]) {
								choices = append(choices, str(section, "key"))
							}
							objectSelect(c, "所属分节", row, "section_key", choices)
							objectField(c, "条目评分规则", row, "rule_text", true)
							objectSelect(c, "评估方式", row, "assessment_mode", []string{"model_assessable", "ambiguous", "price_comparison", "external_comparison", "manual_only", "unsupported_formula"})
						}
						scoreRange(c, row, "score_range", "设置分值范围")
						objectField(c, "权重（留空不设置）", row, "weight", false)
						objectField(c, "歧义或无法评估原因", row, "ambiguity_reason", true)
					}
				}
			}
			objectField(c, "完整修订理由", r.form, "reason", true)
		})
		nativeActions(c).Children(func() {
			nativePrimaryButton(c, "保存完整修订").Disabled(a.Busy || !r.valid || !r.Dirty() || strings.TrimSpace(str(r.form, "reason")) == "").OnClick(func() { r.save(a, saved) })
		})
	})
}
func (r *rubricReplacement) add() {
	if len(r.graph["coverage"]) == 0 {
		return
	}
	first := r.graph["coverage"][0]
	entries := objects(r.form[r.part])
	var row Object
	if r.part == "sections" {
		row = Object{"source_section_id": nil, "sources": []Object{{"requirement_id": first["requirement_id"], "quote": object(first["source"])["quote"]}}, "key": "", "title": "", "order": len(entries) + 1, "aggregation": "sum", "aggregation_rule_text": nil, "score_range": nil, "weight": nil, "cap": nil, "included_in_overall_total": true, "ambiguity_reason": nil}
	} else {
		sections := objects(r.form["sections"])
		key := ""
		if len(sections) > 0 {
			key = str(sections[0], "key")
		}
		row = Object{"source_item_id": nil, "requirement_id": first["requirement_id"], "section_key": key, "key": "", "title": "", "rule_text": "", "order": len(entries) + 1, "assessment_mode": "model_assessable", "score_range": Object{"minimum": "0", "maximum": "0"}, "weight": nil, "ambiguity_reason": nil}
	}
	r.form[r.part] = append(entries, row)
	r.index = len(entries)
}
func (r *rubricReplacement) save(a *NativeApp, saved func(string)) {
	if !r.valid {
		a.Error = "请重新读取并核对完整评分规则快照"
		return
	}
	data, _ := json.Marshal(r.form)
	if len(data) > 512*1024 {
		a.Error = "完整修订超过 512 KiB 编辑上限"
		return
	}
	if len(objects(r.form["sections"])) == 0 || len(objects(r.form["items"])) == 0 || len(objects(r.form["coverage"])) != len(r.graph["coverage"]) {
		a.Error = "完整修订必须保留分节、条目和全部固定评分要求"
		return
	}
	for _, part := range []string{"sections", "items"} {
		seen := map[string]bool{}
		for _, row := range objects(r.form[part]) {
			key := str(row, "key")
			if key == "" || strings.TrimSpace(str(row, "title")) == "" || seen[key] {
				a.Error = "分节和条目键必须唯一，标题不能为空"
				return
			}
			seen[key] = true
			if part == "sections" && len(objects(row["sources"])) == 0 {
				a.Error = "每个分节至少保留一段固定原文"
				return
			}
		}
	}
	task, id := r.task, r.id
	base := "/tasks/" + task + "/score-rubrics/" + id
	var body Object
	_ = json.Unmarshal(data, &body)
	serial := r.serial
	a.Do(func(ctx context.Context) (func(), error) {
		current, err := a.Client.Request(ctx, "GET", base+"?view=console&part=summary", nil)
		if err == nil && (str(current.Data, "task_id") != task || str(current.Data, "id") != id || current.Data["revision"] != body["expected_revision"] || current.Data["input_hash"] != body["expected_input_hash"] || str(current.Data, "validity") != "current") {
			err = errors.New("评分规则修订已变化，修改已保留，请重新读取快照")
		}
		var result Result
		if err == nil {
			result, err = a.Client.Request(ctx, "POST", base+"/revisions?view=console", body)
		}
		return func() {
			if serial != r.serial || !r.opened {
				return
			}
			if err != nil {
				if mgConflict(err) {
					r.valid = false
				}
				a.handleError(err)
				return
			}
			r.form = nil
			r.opened = false
			saved(str(result.Data, "id"))
		}, nil
	})
}
