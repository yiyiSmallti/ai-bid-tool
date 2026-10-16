package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"
)

func managementPage(route Route) NativePage {
	parts := strings.Split(strings.Trim(route.Path, "/"), "/")
	if len(parts) >= 4 && parts[0] == "org" && parts[1] == "tasks" {
		switch parts[3] {
		case "attachments":
			if len(parts) == 4 {
				return newTaskAttachmentPage(route)
			}
		case "memory-feedback":
			if len(parts) == 4 {
				return newMemoryFeedbackPage(route)
			}
		}
	}
	if len(parts) < 2 || parts[0] != "org" {
		return nil
	}
	if parts[1] == "memory" {
		if len(parts) <= 3 {
			return newMemoryPage(route)
		}
		return nil
	}
	if parts[1] == "attachments" {
		if len(parts) <= 3 {
			return newAttachmentPage(route)
		}
		return nil
	}
	if parts[1] == "templates" && len(parts) == 6 && parts[3] == "revisions" && parts[5] == "bindings" {
		return newBindingPage(route)
	}
	switch parts[1] {
	case "products", "features", "templates":
		if len(parts) <= 3 {
			return newResourcePage(route, parts[1])
		}
	case "profiles":
		if len(parts) == 2 {
			return newQualificationHub(route)
		}
		if len(parts) == 3 {
			return newResourcePage(route, "profiles")
		}
	case "certificates":
		if len(parts) == 3 {
			return newResourcePage(route, "certificates")
		}
	}
	return nil
}

type managementState struct {
	route  Route
	title  string
	dirty  bool
	closed bool
}

func (s *managementState) Title() string { return s.title }
func (s *managementState) Dirty() bool   { return s.dirty }
func (s *managementState) Close()        { s.closed = true; s.dirty = false }

func mgNull(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return strings.TrimSpace(value)
}
func mgBool(value any) bool { result, _ := value.(bool); return result }
func mgCopy(value Object) Object {
	result := make(Object, len(value))
	for key, value := range value {
		result[key] = value
	}
	return result
}
func mgJSON(value any) string { data, _ := json.Marshal(value); return string(data) }
func mgAllowed(data Object, action string) bool {
	for _, hint := range objects(data["actions"]) {
		if str(hint, "action") == action {
			return mgBool(hint["allowed"])
		}
	}
	return false
}
func mgDenied(err error) bool {
	var api *APIError
	return errors.As(err, &api) && (api.Status == 401 || api.Status == 403 || api.Status == 404)
}
func mgConflict(err error) bool {
	var api *APIError
	return !errors.As(err, &api) || api.Status == 409 || api.Status >= 500
}
func mgError(err error) string {
	var api *APIError
	if !errors.As(err, &api) {
		return "操作结果尚未确定，请重新读取后核对；不会自动重试。"
	}
	messages := map[string]string{
		"revision_conflict":           "修订已变化，草稿已保留；请核对当前服务器内容后再主动保存。",
		"lifecycle_conflict":          "生命周期已变化，请核对当前状态后重新决定。",
		"resource_inactive":           "资源或所属产品已停用；已有任务固定版本保留，不能新建或替换选择。",
		"product_inactive":            "所属产品已停用，不能建立新的关联或固定版本。",
		"management_cursor_expired":   "分页已过期，请从第一页重新读取。",
		"management_cursor_invalid":   "分页已失效，请从第一页重新读取。",
		"management_result_too_large": "结果超过显示边界，请缩小查询范围。",
		"memory_conflict_key":         "已有同类、同冲突键的有效记忆，请核对并决定保留哪一项。",
		"memory_sensitive_value":      "记忆含受保护内容，请移除敏感信息后重新修改提案。",
		"memory_revision_conflict":    "记忆修订已变化，审核勾选已清除；请核对精确修订。",
		"confidential_value_conflict": "当前值已变化，输入已清空；请核对最新版本并重新填写。",
		"confidential_field_archived": "字段已归档，不能填写新的值。",
		"attachment_state_changed":    "附件状态已变化，请重新读取精确修订后再决定。",
		"invalid_input":               "填写内容不符合要求，请核对字段和所选文件。",
		"invalid_response":            "服务响应的范围或完整性不符合契约，请重新读取。",
		"forbidden":                   "当前身份无权执行此操作。", "human_required": "需要安装本人操作。",
		"not_found": "记录不存在或不可访问。",
	}
	if api.Status == 401 {
		return "会话已失效，请重试。"
	}
	if api.Status == 403 {
		return "当前身份无权操作，页面内容已清空。"
	}
	if api.Status == 404 {
		return "记录不存在或不可访问。"
	}
	if message, ok := messages[api.Code]; ok {
		return message
	}
	return "操作未完成，请核对当前状态（" + api.Code + "）。"
}

// Domain pages receive failures on the UI thread so conflicts retain only the
// nonsecret draft and authority loss can clear the whole page.
func mgCall(a *NativeApp, method, path string, body any, done func(Result), failed func(error)) {
	a.Do(func(ctx context.Context) (func(), error) {
		result, err := a.Client.Request(ctx, method, path, body)
		var api *APIError
		if errors.As(err, &api) && api.Status == 401 {
			return nil, err
		}
		return func() {
			if err != nil {
				a.Error = mgError(err)
				if failed != nil {
					failed(err)
				}
				return
			}
			if len(result.Warnings) > 0 {
				a.Notice = nativeWarnings(result.Warnings)
			}
			if done != nil {
				done(result)
			}
		}, nil
	})
}

type managementReadSlot struct {
	serial uint64
	cancel context.CancelFunc
}

type managementReads struct {
	slots  map[string]*managementReadSlot
	active chan struct{}
}

func (r *managementReads) close() {
	for _, slot := range r.slots {
		slot.serial++
		slot.cancel()
	}
}

func (r *managementReads) run(a *NativeApp, channel string, work func(context.Context) (func(), error), failed func(error)) {
	if r.slots == nil {
		r.slots = make(map[string]*managementReadSlot)
	}
	if r.active == nil {
		r.active = make(chan struct{}, 2)
	}
	slot := r.slots[channel]
	if slot == nil {
		slot = &managementReadSlot{}
		r.slots[channel] = slot
	} else {
		slot.cancel()
	}
	slot.serial++
	serial := slot.serial
	ctx, cancel := context.WithCancel(a.ctx)
	slot.cancel = cancel
	a.Do(func(context.Context) (func(), error) {
		select {
		case r.active <- struct{}{}:
		case <-ctx.Done():
			cancel()
			return nil, nil
		}
		if ctx.Err() != nil {
			<-r.active
			cancel()
			return nil, nil
		}
		apply, err := work(ctx)
		<-r.active
		cancel()
		if errors.Is(err, context.Canceled) {
			return nil, nil
		}
		var api *APIError
		if errors.As(err, &api) && api.Status == 401 {
			return nil, err
		}
		return func() {
			if slot.serial != serial {
				return
			}
			if err != nil {
				a.Error = mgError(err)
				if failed != nil {
					failed(err)
				}
				return
			}
			if apply != nil {
				apply()
			}
		}, nil
	})
}

func (r *managementReads) call(a *NativeApp, channel, method, path string, body any, done func(Result), failed func(error)) {
	r.run(a, channel, func(ctx context.Context) (func(), error) {
		result, err := a.Client.Request(ctx, method, path, body)
		return func() {
			if len(result.Warnings) > 0 {
				a.Notice = nativeWarnings(result.Warnings)
			}
			if done != nil {
				done(result)
			}
		}, err
	}, failed)
}

func (r *managementReads) image(a *NativeApp, channel, linkPath, expectedPath string, metadata Object, done func(*ui.Bitmap), failed func(error)) {
	r.imageMethod(a, channel, "GET", linkPath, expectedPath, metadata, done, failed)
}

func (r *managementReads) imageMethod(a *NativeApp, channel, method, linkPath, expectedPath string, metadata Object, done func(*ui.Bitmap), failed func(error)) {
	r.run(a, channel, func(ctx context.Context) (func(), error) {
		var data []byte
		var err error
		if linkPath == "" {
			data, err = a.Client.BinaryImage(ctx, expectedPath, "image/png", metadata)
		} else {
			data, err = a.Client.verifiedDownload(ctx, method, linkPath, expectedPath, metadata)
		}
		if err != nil {
			return nil, err
		}
		bitmap, err := ui.DecodeBitmap(data)
		if err != nil {
			return nil, err
		}
		return func() { done(bitmap) }, nil
	}, failed)
}

func mgUpload(a *NativeApp, path string, metadata Object, field string, files []managementFile, done func(Result), failed func(error)) {
	parts := make([]UploadFile, len(files))
	for i, file := range files {
		parts[i] = UploadFile{Field: field, Path: file.Path}
	}
	fields := map[string]string{"metadata": mgJSON(metadata)}
	a.Do(func(ctx context.Context) (func(), error) {
		result, err := a.Client.Upload(ctx, path, fields, parts)
		var api *APIError
		if errors.As(err, &api) && api.Status == 401 {
			return nil, err
		}
		return func() {
			if err != nil {
				a.Error = mgError(err)
				if failed != nil {
					failed(err)
				}
				return
			}
			done(result)
		}, nil
	})
}
func mgChoice(c *ui.Context, label string, value *string, choices []string) bool {
	return nativeSelect(c, label, value, choices)
}
func mgValue(c *ui.Context, label string, value string) {
	width, _ := c.MeasureText(0, ui.Span{Text: label, Size: 13})
	nativePair(c, label, value, max(182, width))
}

var managementStatusLabels = map[string]string{
	"active": "可用", "inactive": "已停用", "candidate": "候选，待人工审核", "disabled": "已停用",
	"expired": "已过期", "deleted": "已逻辑删除", "confirmed": "已确认", "pending": "待审阅",
	"approved": "已接受内部使用", "rejected": "已驳回", "revoked": "已撤回", "queued": "排队中",
	"running": "运行中", "succeeded": "已完成", "failed": "失败", "cancelled": "已取消", "abandoned": "已中止",
	"implemented": "已声明实现，仍需真实材料", "developing": "开发中", "planned": "计划中",
	"declared": "人工声明", "simulated": "模拟拟投声明", "qualification": "单位证照", "personnel": "人员证书",
}

func mgStatus(value string) string {
	if label, ok := managementStatusLabels[value]; ok {
		return label
	}
	return value
}

type managementListing struct {
	Items    []Object
	Rows     [][]string
	Meta     Object
	Cursor   string
	Previous []string
	serial   uint64
}

func (l *managementListing) install(r Result, cursor string, row func(Object) []string) bool {
	if len(r.Items) > 100 || len(str(r.Data, "next_cursor")) > 2048 {
		return false
	}
	if _, bounded := r.Data["returned"]; bounded {
		if integer(r.Data, "returned") != len(r.Items) {
			return false
		}
		hasMore, ok := r.Data["has_more"].(bool)
		if !ok || hasMore != (str(r.Data, "next_cursor") != "") {
			return false
		}
	} else {
		limit := integer(r.Data, "limit")
		if limit < 1 || limit > 100 || len(r.Items) > limit {
			return false
		}
	}
	l.Items, l.Meta, l.Cursor = r.Items, r.Data, cursor
	l.Rows = make([][]string, len(r.Items))
	for i, item := range r.Items {
		l.Rows[i] = row(item)
	}
	return true
}

// controls pages through a listing; a listing that fits one page shows none.
func (l *managementListing) controls(c *ui.Context, a *NativeApp, label string, load func(string)) {
	if len(l.Previous) == 0 && str(l.Meta, "next_cursor") == "" {
		return
	}
	ui.Row(c.Key(label)).FillWidth().Wrap().Gap(8).AlignItems(ui.Center).Children(func() {
		nativeGhostButton(c, "arrow-left", "回到第一页", a.Busy || len(l.Previous) == 0).OnClick(func() { l.Previous = nil; load("") })
		ui.Column(c).Grow(1).MinWidth(220).Children(func() {
			nativePager(c, len(l.Previous)+1, str(l.Meta, "next_cursor") != "", a.Busy, "上一页"+label, "下一页"+label, func() {
				last := len(l.Previous) - 1
				cursor := l.Previous[last]
				l.Previous = l.Previous[:last]
				load(cursor)
			}, func() {
				if len(l.Previous) >= 50 {
					l.Previous = l.Previous[1:]
				}
				l.Previous = append(l.Previous, l.Cursor)
				load(str(l.Meta, "next_cursor"))
			})
		})
	})
}

type managementFile struct {
	Path     string
	Rotation int
}

func mgDownload(a *NativeApp, linkPath, expected, filename string, metadata Object, inspected func()) {
	a.DownloadVerified(linkPath, expected, filename, metadata, inspected)
}
func mgTaskLink(route Route, path string) string {
	if task := route.Query.Get("task"); task != "" {
		return path + "?task=" + url.QueryEscape(task)
	}
	return path
}
func mgRevisionLink(route Route, path string, revision int) string {
	query := url.Values{"revision": {strconv.Itoa(revision)}}
	if task := route.Query.Get("task"); task != "" {
		query.Set("task", task)
	}
	return path + "?" + query.Encode()
}
