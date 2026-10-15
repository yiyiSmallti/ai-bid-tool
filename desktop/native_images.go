package main

import (
	"context"
	"errors"

	"github.com/egoist/mygo/ui"
)

type signedImagePanel struct {
	title  string
	bitmap *ui.Bitmap
	serial uint64
	opened bool
}

func (p *signedImagePanel) Close() { p.serial++; p.bitmap = nil; p.opened = false }
func (p *signedImagePanel) Load(a *NativeApp, title, method, link, path string, metadata Object) {
	p.Close()
	p.title = title
	p.opened = true
	serial := p.serial
	a.Do(func(ctx context.Context) (func(), error) {
		if !sha256Text(str(metadata, "sha256")) {
			return nil, errors.New("图片缺少固定来源摘要")
		}
		fixed := Object{"sha256": metadata["sha256"], "media_type": "image/png"}
		if metadata["size_bytes"] != nil {
			fixed["size_bytes"] = metadata["size_bytes"]
		}
		data, err := a.Client.verifiedDownload(ctx, method, link, path, fixed)
		if err != nil {
			return nil, err
		}
		if len(data) > 40<<20 {
			return nil, errors.New("预览图片超过上限")
		}
		bitmap, err := ui.DecodeBitmap(data)
		return func() {
			if serial == p.serial {
				p.bitmap = bitmap
			}
		}, err
	})
}
func (p *signedImagePanel) View(c *ui.Context, a *NativeApp) {
	if !p.opened {
		return
	}
	nativeHeading(c, p.title)
	nativeButton(c, "关闭图片").OnClick(p.Close)
	if p.bitmap == nil {
		nativeText(c, "正在读取并核验固定图片")
		return
	}
	ui.Image(c, p.bitmap).FillWidth().Height(650).Fit(ui.Contain).Label(p.title)
}
