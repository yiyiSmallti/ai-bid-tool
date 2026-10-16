package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"mime"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"
)

// packageFailures names the package refusals a member can act on. A refused
// export is a 403 that must stay on the page, not close it as inaccessible.
var packageFailures = map[string]string{
	"package_export_not_allowed":   "本项目未允许导出到个人版，请联系管理员开启",
	"package_task_exists":          "本机已有同一项目，请先删除原项目再导入",
	"package_row_conflict":         "项目包中的记录已存在于本机，不能重复导入",
	"package_library_forbidden":    "项目包会新建资料库条目，需要资料库编辑权限",
	"package_integrity_failed":     "项目包已损坏或被改动，未导入任何内容",
	"package_format_unsupported":   "项目包格式不受支持，请用当前版本重新导出",
	"package_reference_unresolved": "项目包缺少被引用的记录，未导入任何内容",
	"package_citation_unresolved":  "项目包中的引用找不到原文片段，未导入任何内容",
	"package_rejected":             "项目包内容未通过校验，未导入任何内容",
	"package_too_large":            "项目包超过上传上限",
	"package_not_ready":            "项目包尚未生成完成",
}

func packageFailure(err error) error {
	var api *APIError
	if errors.As(err, &api) {
		if message, ok := packageFailures[api.Code]; ok {
			return errors.New(message)
		}
	}
	return err
}

func packageJobError(job Object) string {
	failure := object(job["error"])
	if message, ok := packageFailures[str(failure, "code")]; ok {
		return message
	}
	return nativeJobError(failure)
}

// PackageFile reads an export's archive and checks it against the job report:
// a zip of exactly the reported size and SHA-256.
func (c *APIClient) PackageFile(ctx context.Context, job string, report Object) ([]byte, error) {
	expected, err := hex.DecodeString(str(report, "sha256"))
	size := integer(report, "size")
	if err != nil || len(expected) != sha256.Size || size <= 0 {
		return nil, errors.New("项目包缺少可信摘要或长度")
	}
	response, err := c.transfer(ctx, "GET", "/packages/"+job+"/file", nil, "")
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, err = decodeResult(response)
		return nil, err
	}
	defer response.Body.Close()
	if media, _, err := mime.ParseMediaType(response.Header.Get("Content-Type")); err != nil || media != "application/zip" {
		return nil, errors.New("下载内容不是项目包")
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, int64(size)+1))
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256(data)
	if len(data) != size || response.ContentLength >= 0 && response.ContentLength != int64(size) {
		return nil, errors.New("项目包长度与导出报告不一致")
	}
	if !bytes.Equal(digest[:], expected) || !bytes.HasPrefix(data, []byte("PK\x03\x04")) {
		return nil, errors.New("项目包 SHA-256 与导出报告不一致")
	}
	return data, ctx.Err()
}

// packagePanel follows one export or import job through /packages/{id}, which
// a member reads even for an import that has no task yet.
type packagePanel struct {
	id         string
	data       Object
	generation uint64
	cancel     context.CancelFunc
	finished   func(Object)
}

func (j *packagePanel) Close() {
	j.generation++
	if j.cancel != nil {
		j.cancel()
	}
	j.id, j.data, j.finished = "", nil, nil
}

func (j *packagePanel) status() string { return str(j.data, "status") }
func (j *packagePanel) done() bool {
	return containsString([]string{"succeeded", "failed", "cancelled"}, j.status())
}

// Follow polls the job until it ends. Each check is a plain request: a lost
// gateway or an ended session is handled as on any page.
func (j *packagePanel) Follow(a *NativeApp, job Object, finished func(Object)) {
	j.Close()
	j.id, j.data, j.finished = str(job, "id"), job, finished
	j.poll(a)
}

func (j *packagePanel) poll(a *NativeApp) {
	if j.id == "" || j.done() {
		if j.done() && j.finished != nil {
			finished := j.finished
			j.finished = nil
			finished(j.data)
		}
		return
	}
	j.generation++
	generation, epoch, id := j.generation, a.epoch, j.id
	ctx, cancel := context.WithCancel(a.ctx)
	j.cancel = cancel
	go func() {
		timer := time.NewTimer(time.Second)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
		r, err := a.Client.Request(ctx, "GET", "/packages/"+id, nil)
		a.update(func() {
			if generation != j.generation || epoch != a.epoch || ctx.Err() != nil {
				return
			}
			if err != nil {
				a.handleError(packageFailure(err))
				return
			}
			j.data = r.Data
			j.poll(a)
		})
	}()
}

// Save downloads a finished export, verifies it and writes it where the member
// chooses.
func (j *packagePanel) Save(a *NativeApp, filename string) {
	id, report := j.id, object(j.data["report"])
	a.Do(func(ctx context.Context) (func(), error) {
		data, err := a.Client.PackageFile(ctx, id, report)
		if err != nil {
			return nil, packageFailure(err)
		}
		if saved, err := a.saveDownload(ctx, filename, data); !saved || err != nil {
			return nil, err
		}
		return func() { a.Notice = "项目包长度与 SHA-256 已核验，保存完成" }, nil
	})
}

// counts lists a report's per-table counts as "table n", largest first.
func packageCounts(counts Object) string {
	type entry struct {
		name string
		rows int
	}
	var entries []entry
	for name := range counts {
		entries = append(entries, entry{name, integer(counts, name)})
	}
	slices.SortFunc(entries, func(x, y entry) int {
		if x.rows != y.rows {
			return y.rows - x.rows
		}
		if x.name < y.name {
			return -1
		}
		return 1
	})
	text := ""
	for i, item := range entries {
		if i > 0 {
			text += "，"
		}
		text += item.name + " " + strconv.Itoa(item.rows)
	}
	return text
}

func packageTotal(counts Object) int {
	total := 0
	for name := range counts {
		total += integer(counts, name)
	}
	return total
}

// View shows the job's state and, once it ends, its report.
func (j *packagePanel) View(c *ui.Context, a *NativeApp, title string, actions func()) {
	if j.id == "" {
		return
	}
	report := object(j.data["report"])
	ui.Row(c).FillWidth().Wrap().Gap(10).AlignItems(ui.Center).Children(func() {
		ui.Icon(c, nativeIcon("package")).FontSize(17).TextColor(nativePalette(c).AccentInk)
		ui.Text(c, title).FontSize(15.5).FontWeight(600)
		if status := j.status(); status != "" {
			nativeStatusBadge(c, nativeLabel(status))
		}
		ui.Row(c).Grow(1)
		if actions != nil && j.status() == "succeeded" {
			actions()
		}
	})
	switch j.status() {
	case "succeeded":
		tables := object(report["tables"])
		nativeHint(c, "共 "+strconv.Itoa(packageTotal(tables))+" 条记录、"+strconv.Itoa(integer(report, "files"))+" 个文件："+packageCounts(tables))
		if reused := object(report["reused"]); len(reused) > 0 {
			nativeHint(c, "沿用本机已有的资料库条目："+packageCounts(reused))
		}
		if omitted := object(report["omitted_rows"]); len(omitted) > 0 {
			nativeHint(c, "未随项目导出的关联记录："+packageCounts(omitted))
		}
		if cleared := object(report["cleared"]); len(cleared) > 0 {
			nativeHint(c, "已清空指向项目外记录的字段："+packageCounts(cleared))
		}
		if values := stringList(report["needs_value"]); len(values) > 0 {
			nativeCallout(c, toneWarning, "以下保密字段在本机没有取值，请在任务详情的保密字段中补填："+strings.Join(values, "、"))
		}
	case "failed", "cancelled":
		nativeCallout(c, toneDanger, packageJobError(j.data))
	default:
		ui.Progress(c, -1).FillWidth()
	}
}
