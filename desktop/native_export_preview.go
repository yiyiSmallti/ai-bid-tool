package main

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/egoist/mygo/ui"
)

type exportPreviewPanel struct {
	id, title, zoom, pageInput string
	state                      Object
	page                       int
	bitmap                     *ui.Bitmap
	serial                     uint64
	cancel                     context.CancelFunc
	opened                     bool
}

func (p *exportPreviewPanel) Close() {
	p.serial++
	if p.cancel != nil {
		p.cancel()
	}
	p.opened = false
	p.bitmap = nil
	p.state = nil
}
func (p *exportPreviewPanel) Open(a *NativeApp, id, title string) {
	p.Close()
	p.id = id
	p.title = title
	p.page = 1
	p.pageInput = "1"
	p.zoom = "1"
	p.opened = true
	p.poll(a)
}
func (p *exportPreviewPanel) poll(a *NativeApp) {
	if p.cancel != nil {
		p.cancel()
	}
	p.serial++
	serial, epoch, id := p.serial, a.epoch, p.id
	ctx, cancel := context.WithCancel(a.ctx)
	p.cancel = cancel
	go func() {
		for {
			r, err := a.Client.Request(ctx, "GET", "/exports/"+id+"/preview", nil)
			if ctx.Err() != nil {
				return
			}
			pending := err == nil && containsString([]string{"queued", "running"}, str(r.Data, "status"))
			a.update(func() {
				if ctx.Err() != nil || serial != p.serial || epoch != a.epoch {
					return
				}
				if err != nil {
					a.handleError(err)
					return
				}
				p.state = r.Data
				if str(r.Data, "status") == "invalidated" {
					p.bitmap = nil
				}
				if str(r.Data, "status") == "succeeded" {
					p.loadImage(a)
				}
			})
			if !pending || err != nil {
				return
			}
			timer := time.NewTimer(2 * time.Second)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
		}
	}()
}
func (p *exportPreviewPanel) loadImage(a *NativeApp) {
	id, page, zoom, serial := p.id, p.page, p.zoom, p.serial
	p.bitmap = nil
	a.Do(func(ctx context.Context) (func(), error) {
		data, err := a.Client.BinaryImage(ctx, fmt.Sprintf("/exports/%s/preview/pages/%d?zoom=%s", id, page, zoom), "image/png", nil)
		if err != nil {
			return nil, err
		}
		bitmap, err := ui.DecodeBitmap(data)
		return func() {
			if serial == p.serial && id == p.id && page == p.page && zoom == p.zoom {
				p.bitmap = bitmap
			}
		}, err
	})
}
func (p *exportPreviewPanel) View(c *ui.Context, a *NativeApp) {
	if !p.opened {
		return
	}
	nativeHeading(c, "在线预览 · "+p.title)
	nativeButton(c, "关闭导出预览").OnClick(p.Close)
	status := str(p.state, "status")
	ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
		nativeHint(c, "预览状态")
		nativeStatusBadge(c, nativeLabel(status))
	})
	if status == "invalidated" {
		nativeText(c, "导出内容已变化，预览已失效；请重新生成并发布。")
		return
	}
	nativeButton(c, "生成在线预览").Disabled(a.Busy || status == "queued" || status == "running").OnClick(func() {
		a.Request("POST", "/exports/"+p.id+"/preview", nil, func(r Result) { p.state = r.Data; p.poll(a) })
	})
	if status == "failed" {
		nativeText(c, str(object(p.state["error"]), "message"))
		nativeButton(c, "重试导出预览").Disabled(a.Busy).OnClick(func() {
			a.Request("POST", "/exports/"+p.id+"/preview?retry=true", nil, func(r Result) { p.state = r.Data; p.poll(a) })
		})
	}
	if status != "succeeded" {
		return
	}
	count := integer(p.state, "page_count")
	nativePager(c, p.page, p.page < count, a.Busy, "导出上一页", "导出下一页",
		func() { p.page--; p.pageInput = strconv.Itoa(p.page); p.loadImage(a) },
		func() { p.page++; p.pageInput = strconv.Itoa(p.page); p.loadImage(a) })
	ui.Row(c).FillWidth().Wrap().Gap(8).AlignItems(ui.Center).Children(func() {
		nativeText(c, fmt.Sprintf("共 %d 页", count))
		ui.Column(c.Key("export-page-input")).Width(112).Gap(6).Children(func() {
			nativeField(c, "导出预览页码", &p.pageInput, false)
		})
		nativeButton(c, "跳转导出页码").Disabled(a.Busy).OnClick(func() {
			page, err := strconv.Atoi(p.pageInput)
			if err != nil || page < 1 || page > count {
				a.Error = "页码不在导出预览范围内"
				return
			}
			p.page = page
			p.loadImage(a)
		})
		ui.Column(c.Key("export-zoom")).Width(160).Children(func() {
			if nativeSelect(c, "导出缩放", &p.zoom, []string{"1", "2"}) {
				p.loadImage(a)
			}
		})
	})
	if p.bitmap != nil {
		ui.Image(c, p.bitmap).FillWidth().Height(650).Fit(ui.Contain).Label("导出文件页面")
	}
}
