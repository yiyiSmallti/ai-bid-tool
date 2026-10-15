package main

import (
	"context"
	"errors"
	"strings"

	"github.com/egoist/mygo/ui"
)

type bidPresencePanel struct {
	id, review, reason string
	preview            Object
	selected           map[string]bool
	pixels             map[string]*ui.Bitmap
	consent            bool
	serial             uint64
	changed            func(string)
}

func (p *bidPresencePanel) Dirty() bool { return p.reason != "" }
func (p *bidPresencePanel) Close() {
	p.serial++
	p.preview = nil
	p.pixels = nil
	p.selected = nil
	p.reason = ""
	p.consent = false
}
func (p *bidPresencePanel) Load(a *NativeApp, id, review string, changed func(string)) {
	p.Close()
	p.id = id
	p.review = review
	p.changed = changed
	p.fetch(a, false)
}
func (p *bidPresencePanel) fetch(a *NativeApp, prepare bool) {
	p.Close()
	p.selected = map[string]bool{}
	serial, id, review := p.serial, p.id, p.review
	a.Do(func(ctx context.Context) (func(), error) {
		method, path := "GET", "/bid-submissions/"+id+"/presence-preview"
		var body any
		if prepare {
			method = "POST"
			path = "/bid-submissions/" + id + "/presence-preparations"
			body = Object{"review_id": review}
		}
		result, err := a.Client.Request(ctx, method, path, body)
		if err != nil {
			return nil, err
		}
		data := result.Data
		images := objects(data["images"])
		if str(data, "submission_id") != id || str(data, "purpose") != "bid_review_presence" || len(images) > 40 {
			return nil, errors.New("初筛图片范围不符合契约")
		}
		pixels := map[string]*ui.Bitmap{}
		for _, image := range images {
			id := str(image, "id")
			width, height := integer(image, "width_px"), integer(image, "height_px")
			if id == "" || pixels[id] != nil || width <= 0 || height <= 0 || width > 8192 || height > 8192 || width*height > 16000000 || integer(image, "size_bytes") <= 0 || integer(image, "size_bytes") > 4<<20 || len(str(image, "sha256")) != 64 || len(str(image, "source_sha256")) != 64 {
				return nil, errors.New("初筛图片描述无效")
			}
			encoded, err := a.Client.BinaryImage(ctx, "/bid-presence-images/"+id+"/content", "image/jpeg", image)
			if err != nil {
				return nil, err
			}
			bitmap, err := ui.DecodeBitmap(encoded)
			if err != nil {
				return nil, err
			}
			pixels[id] = bitmap
		}
		return func() {
			if serial != p.serial {
				return
			}
			p.preview = data
			p.pixels = pixels
			if p.changed != nil {
				id := ""
				if data["current"] == true && !prepare {
					id = str(data, "authorization_id")
				}
				p.changed(id)
			}
		}, nil
	})
}
func (p *bidPresencePanel) authorize(a *NativeApp, allow bool) {
	fixed := p.preview
	receipt := fixed
	if !allow {
		receipt = object(fixed["revocable_authorization"])
	}
	if receipt == nil {
		return
	}
	ids := []string{}
	if allow {
		for _, image := range objects(fixed["images"]) {
			id := str(image, "id")
			if p.selected[id] && p.pixels[id] != nil {
				ids = append(ids, id)
			}
		}
	} else {
		ids = stringList(receipt["image_ids"])
	}
	if allow && (!p.consent || len(ids) == 0 || len(p.pixels) != len(objects(fixed["images"])) || str(fixed, "source_review_id") != p.review || len(stringList(fixed["blockers"])) > 0) {
		return
	}
	body := Object{"request_id": requestID(), "expected_revision": fixed["expected_revision"], "expected_authorization_id": fixed["expected_authorization_id"], "manifest_sha256": receipt["manifest_sha256"], "image_ids": ids, "privacy_reviewed": true, "allow_external": allow, "reason": strings.TrimSpace(p.reason)}
	p.consent = false
	a.Request("POST", "/bid-submissions/"+p.id+"/presence-authorizations", body, func(r Result) {
		if str(r.Data, "purpose") != "bid_review_presence" || str(r.Data, "manifest_sha256") != str(receipt, "manifest_sha256") || r.Data["allow_external"] != allow {
			a.Error = "图片授权回执不匹配"
			return
		}
		p.fetch(a, false)
	})
}
func (p *bidPresencePanel) View(c *ui.Context, a *NativeApp) {
	nativeHeading(c, "签章初筛图片授权")
	nativeText(c, "逐页核对下方实际外发 JPEG，确认文字、印章名称和身份信息均不可读。报价页和未确认目标页禁止外发。此授权与文本授权分开，初筛结果仍需人工确认。")
	nativeButton(c, "生成本次目标页模糊图片").Disabled(a.Busy).OnClick(func() { p.fetch(a, true) })
	nativeButton(c, "刷新图片授权").Disabled(a.Busy).OnClick(func() { p.fetch(a, false) })
	if p.preview == nil {
		return
	}
	fixed := p.preview
	nativeText(c, "当前授权有效："+str(fixed, "current"))
	for _, code := range stringList(fixed["blockers"]) {
		nativeText(c, code)
	}
	matches := str(fixed, "source_review_id") == p.review
	if !matches {
		nativeText(c, "尚无本次检验目标页图片，请先生成")
	}
	count := 0
	for _, image := range objects(fixed["images"]) {
		id := str(image, "id")
		checked := p.selected[id]
		if checked {
			count++
		}
		if ui.Checkbox(c.Key(id), &checked, "投标第 "+str(image, "page")+" 页初筛图片").Disabled(a.Busy || !matches || p.pixels[id] == nil).Changed() {
			p.selected[id] = checked
			p.consent = false
		}
		if bitmap := p.pixels[id]; bitmap != nil {
			ui.Image(c, bitmap).FillWidth().Height(500).Fit(ui.Contain).Label("实际外发模糊图片")
		}
		nativeText(c, "外发 JPEG SHA-256 "+str(image, "sha256"))
		nativeText(c, "源页 SHA-256 "+str(image, "source_sha256"))
	}
	if nativeField(c, "图片授权或撤销理由", &p.reason, true) {
		p.consent = false
	}
	ui.Checkbox(c, &p.consent, "已逐页核对所选模糊图片，同意仅用于签章存在性初筛")
	nativePrimaryButton(c, "授权所选初筛图片").Disabled(a.Busy || !p.consent || count == 0 || strings.TrimSpace(p.reason) == "" || !matches || len(stringList(fixed["blockers"])) > 0).OnClick(func() { p.authorize(a, true) })
	if fixed["revocable_authorization"] != nil {
		nativeDangerButton(c, "撤销图片外发授权").Disabled(a.Busy || strings.TrimSpace(p.reason) == "").OnClick(func() { p.authorize(a, false) })
	}
}
