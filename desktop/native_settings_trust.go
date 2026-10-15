package main

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/egoist/mygo/ui"
)

type trustAnchorPage struct {
	managementState
	reads             managementReads
	rows              []Object
	table             [][]string
	selected, preview Object
	label, file       string
	generation        uint64
}

func newTrustAnchorPage(route Route) *trustAnchorPage {
	return &trustAnchorPage{managementState: managementState{route: route, title: "信任根证书"}}
}
func (p *trustAnchorPage) Open(a *NativeApp) { p.load(a) }
func (p *trustAnchorPage) Close() {
	p.managementState.Close()
	p.reads.close()
	p.invalidate()
	p.rows, p.table = nil, nil
	p.selected = nil
	p.label, p.file = "", ""
}
func (p *trustAnchorPage) invalidate() {
	p.generation++
	p.preview = nil
	if slot := p.reads.slots["trust-preview"]; slot != nil {
		slot.serial++
		slot.cancel()
	}
}
func (p *trustAnchorPage) failed(a *NativeApp, err error) {
	p.invalidate()
	if mgDenied(err) {
		p.Close()
	}
	a.Error = settingsErrorText(err)
}
func settingsTrustAnchor(value Object, kind string) bool {
	keys := []string{"label", "fingerprint_sha256", "subject", "issuer", "not_before", "not_after", "is_ca"}
	if kind != "preview" {
		keys = append(keys, "id", "enabled", "revision", "created_at", "disabled_at")
	}
	if kind == "add" {
		keys = append(keys, "created")
	}
	if !settingsKeys(value, keys...) || !settingsString(value["label"], 1, 100) || !settingsSHA256Pattern.MatchString(str(value, "fingerprint_sha256")) || !settingsString(value["subject"], 0, 2000) || !settingsString(value["issuer"], 0, 2000) || !settingsTime(value["not_before"]) || !settingsTime(value["not_after"]) || value["is_ca"] != true {
		return false
	}
	if kind == "preview" {
		return true
	}
	return settingsUUID(str(value, "id")) && settingsBoolean(value["enabled"]) && settingsInteger(value["revision"], 1, 1<<30) && settingsTime(value["created_at"]) && (value["disabled_at"] == nil || settingsTime(value["disabled_at"])) && (kind != "add" || settingsBoolean(value["created"]))
}
func (p *trustAnchorPage) load(a *NativeApp) {
	p.rows, p.table = nil, nil
	p.selected = nil
	settingsRead(a, &p.reads, "trust-list", func(ctx context.Context) (Result, error) {
		return a.Client.Request(ctx, "GET", "/platform/trust-anchors", nil)
	}, func(r Result) {
		for _, row := range r.Items {
			if !settingsTrustAnchor(row, "view") {
				a.Error = settingsFailures["invalid_response"]
				return
			}
		}
		p.rows = r.Items
		p.table = make([][]string, len(r.Items))
		for i, row := range r.Items {
			state := "已停用"
			if mgBool(row["enabled"]) {
				state = "已启用"
			}
			p.table[i] = []string{str(row, "label"), str(row, "fingerprint_sha256"), str(row, "subject"), str(row, "issuer"), str(row, "not_before") + " 至 " + str(row, "not_after"), state, fmt.Sprintf("第 %d 版 · %s", integer(row, "revision"), str(row, "created_at"))}
		}
	}, func(err error) { p.failed(a, err) })
}
func (p *trustAnchorPage) choose(a *NativeApp) {
	a.PickFiles(false, func(paths []string) {
		p.invalidate()
		p.file = ""
		if len(paths) != 1 {
			return
		}
		info, err := os.Stat(paths[0])
		if err != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > 65536 {
			a.Error = "请选择非空且不超过 64 KiB 的 PEM/DER CA 证书。"
			return
		}
		p.file = paths[0]
		p.dirty = true
	})
}
func (p *trustAnchorPage) upload(a *NativeApp, preview bool) {
	if a.Busy || p.file == "" || strings.TrimSpace(p.label) == "" || !preview && p.preview == nil {
		return
	}
	info, err := os.Stat(p.file)
	if err != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > 65536 {
		p.invalidate()
		a.Error = "证书文件不可读或超过 64 KiB，请重新选择。"
		return
	}
	file, label := p.file, strings.TrimSpace(p.label)
	generation := p.generation
	path := "/platform/trust-anchors"
	kind := "add"
	expectedFingerprint := ""
	if preview {
		p.preview = nil
		path += "/preview"
		kind = "preview"
	} else {
		expectedFingerprint = str(p.preview, "fingerprint_sha256")
	}
	work := func(ctx context.Context) (Result, error) {
		return a.Client.Upload(ctx, path, map[string]string{"label": label}, []UploadFile{{Field: "certificate", Path: file}})
	}
	done := func(r Result) {
		if p.closed || generation != p.generation {
			return
		}
		if !settingsTrustAnchor(r.Data, kind) || str(r.Data, "label") != label || !preview && str(r.Data, "fingerprint_sha256") != expectedFingerprint {
			p.invalidate()
			a.Error = settingsFailures["invalid_response"]
			return
		}
		if preview {
			p.preview = r.Data
			return
		}
		if mgBool(r.Data["created"]) {
			a.Notice = "信任根已添加，后续准备将使用新的信任快照。"
		} else {
			a.Notice = "此证书已存在，保留原有启停状态。"
		}
		p.invalidate()
		p.label, p.file = "", ""
		p.dirty = false
		p.load(a)
	}
	if preview {
		settingsRead(a, &p.reads, "trust-preview", work, done, func(err error) { p.failed(a, err) })
	} else {
		settingsWrite(a, work, done, func(err error) { p.failed(a, err) })
	}
}
func (p *trustAnchorPage) disable(a *NativeApp, row Object) {
	id, label := str(row, "id"), str(row, "label")
	a.Confirm("停用信任根证书", "停用 "+label+" 后，后续准备不会使用此证书。已有准备结果保留原有固定信任快照。", func() {
		settingsWrite(a, func(ctx context.Context) (Result, error) {
			return a.Client.Request(ctx, "POST", "/platform/trust-anchors/"+url.PathEscape(id)+"/disable", Object{})
		}, func(r Result) {
			if !settingsTrustAnchor(r.Data, "view") || str(r.Data, "id") != id || mgBool(r.Data["enabled"]) {
				a.Error = settingsFailures["invalid_response"]
				return
			}
			a.Notice = "信任根已停用；已有准备结果仍引用原有固定快照。"
			p.load(a)
		}, func(err error) { p.failed(a, err) })
	})
}
func (p *trustAnchorPage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "信任根权限或会话已变化，请重新打开。")
		return
	}
	nativeText(c, "本地 PDF 签名验证使用启用的 CA 证书；每次准备固定信任快照。")
	nativeButton(c, "刷新信任根").Disabled(a.Busy).OnClick(func() { p.load(a) })
	nativeHeading(c, "添加信任根")
	ui.Column(c.Key("trust-form")).Gap(8).Disabled(a.Busy).Children(func() {
		if nativeField(c, "信任根标签", &p.label, false) {
			p.invalidate()
			p.dirty = true
		}
		nativeButton(c, "选择 PEM 或 DER 证书").OnClick(func() { p.choose(a) })
		if p.file != "" {
			mgValue(c, "所选 CA 证书", filepath.Base(p.file))
		}
		nativeText(c, "支持 PEM/DER，最多 64 KiB。解析预览不保存证书；添加前请与 CA 官方公布的 SHA-256 指纹比对。")
		nativeButton(c, "解析证书预览").Disabled(p.file == "" || strings.TrimSpace(p.label) == "").OnClick(func() { p.upload(a, true) })
	})
	if p.preview != nil {
		nativeHeading(c, "证书预览")
		for _, field := range []struct{ key, label string }{{"label", "标签"}, {"fingerprint_sha256", "SHA-256 指纹"}, {"subject", "主体"}, {"issuer", "颁发者"}, {"not_before", "有效期开始"}, {"not_after", "有效期结束"}} {
			mgValue(c, field.label, str(p.preview, field.key))
		}
		mgValue(c, "CA 属性", "是")
		nativePrimaryButton(c, "添加此信任根").Disabled(a.Busy).OnClick(func() { p.upload(a, false) })
	}
	nativeTable(c, []string{"证书", "SHA-256 指纹", "主体", "颁发者", "有效期", "状态", "管理记录"}, p.table, func(i int) { p.selected = p.rows[i] })
	if p.selected != nil {
		if p.selected["disabled_at"] != nil {
			mgValue(c, "停用时间", str(p.selected, "disabled_at"))
		}
		if mgBool(p.selected["enabled"]) {
			row := p.selected
			nativeDangerButton(c, "停用所选信任根").Disabled(a.Busy).OnClick(func() { p.disable(a, row) })
		}
	}
}
