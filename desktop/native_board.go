package main

import (
	"context"
	"errors"
	"net/url"
	"strconv"
	"time"

	"github.com/egoist/mygo/ui"
)

type taskBoardPage struct {
	id, job, bucket, category, domain, blocker, cursor string
	route                                              Route
	progress, starred, mine                            bool
	history, items, jobs                               []Object
	data                                               Object
	rows, jobRows                                      [][]string
	prior                                              []string
	cancel                                             context.CancelFunc
	live                                               string
	generation                                         uint64
	selected                                           Object
}

func (p *taskBoardPage) Title() string {
	if p.progress {
		return "作业进度"
	}
	return "任务看板"
}
func (*taskBoardPage) Dirty() bool { return false }
func (p *taskBoardPage) Close() {
	p.generation++
	if p.cancel != nil {
		p.cancel()
	}
	p.data = nil
	p.items = nil
	p.jobs = nil
	p.rows = nil
	p.jobRows = nil
	p.selected = nil
}
func (p *taskBoardPage) Open(a *NativeApp) {
	a.Request("GET", "/tasks/"+p.id+"/extractions", nil, func(r Result) { p.history = r.Items; p.load(a) })
}
func boardJobRows(items []Object) [][]string {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		progress := object(item["progress"])
		completed, total := str(progress, "completed"), str(progress, "total")
		work := "—"
		if completed != "" || total != "" {
			work = nativeDisplayValue(completed) + " / " + nativeDisplayValue(total)
		}
		rows = append(rows, []string{nativeShortID(str(item, "job_id")), nativeLabel(str(item, "state")), str(item, "attempts"), work, str(item, "updated_at")})
	}
	return rows
}
func (p *taskBoardPage) load(a *NativeApp) {
	p.Close()
	if !p.progress && p.job == "" {
		return
	}
	if p.job != "" {
		found := false
		for _, item := range p.history {
			if str(item, "job_id") == p.job && str(item, "status") == "succeeded" {
				found = true
			}
		}
		if !found {
			a.Error = "请选择一个成功且可访问的抽取"
			return
		}
	}
	query := url.Values{"limit": {"50"}}
	endpoint := "board"
	if p.progress {
		endpoint = "progress"
		query.Set("limit", "20")
	}
	if p.job != "" {
		query.Set("extraction_job_id", p.job)
		query.Set("view", "requirement-review")
	}
	if p.cursor != "" {
		query.Set("cursor", p.cursor)
	}
	if !p.progress {
		for key, value := range map[string]string{"bucket": p.bucket, "category": p.category, "review_domain": p.domain, "blocker": p.blocker} {
			if value != "" {
				query.Set(key, value)
			}
		}
		if p.starred {
			query.Set("starred", "true")
		}
		if p.mine {
			query.Set("mine", "true")
		}
	}
	ctx, cancel := context.WithCancel(a.ctx)
	p.cancel = cancel
	epoch, generation := a.epoch, p.generation
	job := p.job
	path := "/tasks/" + p.id + "/" + endpoint + "?" + query.Encode()
	p.live = "正在读取当前快照"
	apply := func(fn func()) {
		a.update(func() {
			if ctx.Err() == nil && epoch == a.epoch && generation == p.generation {
				fn()
			}
		})
	}
	snapshot := func() (string, error) {
		r, err := a.Client.Request(ctx, "GET", path, nil)
		if err != nil {
			return "", err
		}
		data := r.Data
		if nested := object(data[endpoint]); nested != nil {
			data = nested
		}
		if str(data, "task_id") != p.id || len(r.Items) > 50 {
			return "", errors.New("任务快照范围不符合契约")
		}
		if job != "" {
			scope := object(r.Data["scope"])
			if str(scope, "task_id") != p.id || str(scope, "extraction_job_id") != job {
				return "", errors.New("任务快照抽取范围不符合契约")
			}
		}
		var rows, jobRows [][]string
		var jobs []Object
		if p.progress {
			jobs = r.Items
			rows = boardJobRows(jobs)
		} else {
			if str(data, "extraction_job_id") != job {
				return "", errors.New("看板抽取范围不匹配")
			}
			for _, item := range r.Items {
				response, requirement := object(item["response"]), object(item["requirement"])
				if str(response, "requirement_id") != str(requirement, "requirement_id") {
					return "", errors.New("看板要求与响应不匹配")
				}
				rows = append(rows, []string{str(response, "title"), str(response, "category"), str(requirement, "state"), str(response, "card_state"), str(response, "bucket")})
			}
			jobs = objects(data["jobs"])
			jobRows = boardJobRows(jobs)
		}
		cursor := str(data, "event_cursor")
		if cursor == "" || len(cursor) > 1024 {
			return "", errors.New("任务快照缺少有效事件游标")
		}
		apply(func() {
			p.data = r.Data
			p.items = r.Items
			p.rows = rows
			p.jobs = jobs
			p.jobRows = jobRows
			a.Error = ""
		})
		return cursor, nil
	}
	go runTaskLive(ctx, a.Client, p.id, snapshot, func(status string) { apply(func() { p.live = status }) }, func(err error) {
		apply(func() {
			p.data = nil
			p.items = nil
			p.rows = nil
			p.jobs = nil
			p.jobRows = nil
			p.selected = nil
			a.handleError(err)
		})
	})
}
func (p *taskBoardPage) View(c *ui.Context, a *NativeApp) {

	nativeText(c, "明确选定抽取集合；已确认不代表没有遗漏。"+p.live)
	options := []string{""}
	for _, h := range p.history {
		if str(h, "status") == "succeeded" {
			options = append(options, str(h, "job_id"))
		}
	}
	ui.Row(c).FillWidth().Wrap().Gap(12).AlignItems(ui.End).Children(func() {
		ui.Column(c).Width(220).Children(func() {
			if nativeSelect(c, "成功的抽取集合", &p.job, options) {
				p.cursor = ""
				p.prior = nil
				p.load(a)
			}
		})
		if !p.progress {
			ui.Column(c).Width(220).Children(func() { nativeField(c, "状态分组", &p.bucket, false) })
			ui.Column(c).Width(220).Children(func() { nativeSelect(c, "分类", &p.category, requirementCategories) })
			ui.Column(c).Width(220).Children(func() { nativeSelect(c, "审阅职责", &p.domain, []string{"", "commercial", "technical"}) })
			ui.Column(c).Width(220).Children(func() { nativeField(c, "阻塞原因", &p.blocker, false) })
		}
	})
	if !p.progress {
		ui.Row(c).FillWidth().Wrap().Gap(12).AlignItems(ui.Center).Children(func() {
			ui.Checkbox(c, &p.starred, "仅 ★ 条款")
			ui.Checkbox(c, &p.mine, "待本人确认")
			nativeButton(c, "应用筛选").OnClick(func() { p.cursor = ""; p.prior = nil; p.load(a) })
		})
	}
	headers := []string{"要求", "分类", "要求确认", "响应状态", "状态分组"}
	if p.progress {
		headers = []string{"作业", "状态", "尝试次数", "已提交工作量", "更新时间"}
	}
	nativeTable(c, headers, p.rows, func(i int) { p.selected = p.items[i] })
	if p.selected != nil && !p.progress {
		response, requirement := object(p.selected["response"]), object(p.selected["requirement"])
		nativeText(c, str(response, "title"))
		for _, code := range stringList(response["blockers"]) {
			nativeText(c, code)
		}
		for _, code := range stringList(requirement["blockers"]) {
			nativeText(c, code)
		}
		if str(requirement, "bucket") == "requirement_review" {
			nativeButton(c, "核对并确认要求").OnClick(func() {
				a.Navigate("/org/tasks/" + p.id + "/requirements?job=" + p.job + "&requirement=" + str(response, "requirement_id"))
			})
		}
		for _, action := range objects(response["next_actions"]) {
			code := str(action, "code")
			nativeButton(c.Key(code), code).OnClick(func() {
				suffix := "/review?job=" + p.job + "&requirement=" + str(response, "requirement_id")
				if code == "view_job" {
					suffix = "/progress"
				} else if containsString([]string{"inspect_annotated_material", "confirm_annotated_material", "retry_annotation_release", "replace_annotation_source"}, code) && str(response, "card_id") != "" {
					suffix = "/cards/" + str(response, "card_id") + "/annotation?job=" + p.job
				}
				a.Navigate("/org/tasks/" + p.id + suffix)
			})
		}
	}
	data := p.data
	if nested := object(data["board"]); nested != nil {
		data = nested
	}
	if nested := object(data["progress"]); nested != nil {
		data = nested
	}
	if p.data != nil {
		mgValue(c, "快照时间", str(data, "as_of"))
		if scope := object(p.data["scope"]); scope != nil {
			summary := object(scope["summary"])
			nativeText(c, "要求确认 "+str(summary, "confirmed")+" / "+str(summary, "total")+" / 待确认 "+str(summary, "unconfirmed")+" / 已失效 "+str(summary, "invalidated"))
		}
	}
	nativePager(c, len(p.prior)+1, str(data, "next_cursor") != "", a.Busy, "上一页", "下一页",
		func() { p.cursor = p.prior[len(p.prior)-1]; p.prior = p.prior[:len(p.prior)-1]; p.load(a) },
		func() { p.prior = append(p.prior, p.cursor); p.cursor = str(data, "next_cursor"); p.load(a) })
	ui.Row(c).FillWidth().Wrap().Gap(8).Children(func() {
		nativeGhostButton(c, "refresh-cw", "重新读取首页", false).OnClick(func() { p.cursor = ""; p.prior = nil; p.load(a) })
	})
	if !p.progress {
		nativeHeading(c, "相关作业进度")
		nativeTable(c, []string{"作业", "状态", "尝试次数", "已提交工作量", "更新时间"}, p.jobRows, nil)
	}
	for _, activity := range objects(data["activity"]) {
		nativeText(c, str(activity, "action")+" / "+nativeDisplay(str(activity, "created_at")))
	}
}

// Events invalidate authenticated snapshots; they never carry business state into the UI.
func runTaskLive(ctx context.Context, client *APIClient, task string, snapshot func() (string, error), status func(string), failed func(error)) {
	var cursor string
	ready := false
	failures := 0
	seen := map[string]bool{}
	order := []string{}
	events := make(chan Object, 128)
	streamDone := make(chan error, 1)
	var streamCancel context.CancelFunc
	streaming := false
	validity := time.NewTicker(25 * time.Second)
	defer validity.Stop()
	retry := time.NewTimer(0)
	defer retry.Stop()
	var debounce *time.Timer
	var changed <-chan time.Time
	defer func() {
		if streamCancel != nil {
			streamCancel()
		}
		if debounce != nil {
			debounce.Stop()
		}
	}()
	terminal := func(err error) bool {
		var api *APIError
		return errors.As(err, &api) && containsInt([]int{401, 403, 404}, api.Status)
	}
	backoff := func(err error) {
		failures++
		delay := min(30*time.Second, time.Second*time.Duration(1<<min(failures-1, 5)))
		if failures >= 3 {
			delay = max(delay, 5*time.Second)
		}
		var api *APIError
		if errors.As(err, &api) {
			if n, e := strconv.Atoi(api.RetryAfter); e == nil {
				delay = max(delay, time.Duration(n)*time.Second)
			}
			if api.Status == 409 || api.Code == "event_cursor_expired" || api.Code == "invalid_event_cursor" {
				ready = false
				cursor = ""
			}
		}
		status("正在恢复任务更新")
		retry.Reset(delay)
	}
	refresh := func() bool {
		next, err := snapshot()
		if err != nil {
			ready = false
			failed(err)
			if terminal(err) {
				return false
			}
			backoff(err)
			return true
		}
		cursor = next
		ready = true
		return true
	}
	schedule := func() {
		if debounce != nil {
			debounce.Stop()
		}
		debounce = time.NewTimer(150 * time.Millisecond)
		changed = debounce.C
	}
	receive := func(event Object) error {
		switch str(event, "type") {
		case "heartbeat":
			if next := str(event, "cursor"); next != "" && len(next) <= 1024 {
				cursor = next
			}
			return nil
		case "reset_required":
			ready = false
			cursor = ""
			schedule()
			return nil
		}
		id, next := str(event, "event_id"), str(event, "cursor")
		if str(event, "task_id") != task || id == "" || next == "" || len(next) > 1024 {
			return errors.New("进度事件范围不符合契约")
		}
		cursor = next
		failures = 0
		if seen[id] {
			return nil
		}
		seen[id] = true
		order = append(order, id)
		if len(order) > 2000 {
			delete(seen, order[0])
			order = order[1:]
		}
		schedule()
		return nil
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-validity.C:
			if !refresh() {
				return
			}
		case <-changed:
			changed = nil
			if !refresh() {
				return
			}
			if !streaming {
				retry.Reset(time.Millisecond)
			}
		case err := <-streamDone:
			streaming = false
			if ctx.Err() != nil {
				return
			}
			if terminal(err) {
				failed(err)
				return
			}
			backoff(err)
		case event := <-events:
			if err := receive(event); err != nil {
				if streamCancel != nil {
					streamCancel()
				}
				backoff(err)
			}
		case <-retry.C:
			if !ready {
				if !refresh() {
					return
				}
				if !ready {
					continue
				}
			}
			if streaming {
				continue
			}
			if failures >= 3 {
				status("轮询更新")
				q := url.Values{"limit": {"100"}, "wait_seconds": {"0"}, "cursor": {cursor}}
				r, err := client.Request(ctx, "GET", "/tasks/"+task+"/events/poll?"+q.Encode(), nil)
				if err != nil {
					if terminal(err) {
						failed(err)
						return
					}
					backoff(err)
					continue
				}
				if r.Data["reset_required"] == true {
					ready = false
					cursor = ""
					if !refresh() {
						return
					}
				} else {
					for _, event := range r.Items {
						if err := receive(event); err != nil {
							backoff(err)
							break
						}
					}
					if next := str(r.Data, "next_cursor"); next != "" {
						cursor = next
					} else if next := str(r.Data, "head_cursor"); next != "" {
						cursor = next
					}
				}
				failures = 3
				delay := 5 * time.Second
				if r.Data["has_more"] == true {
					delay = time.Second
				}
				retry.Reset(delay)
			} else {
				status("实时更新")
				streamCtx, cancel := context.WithCancel(ctx)
				streamCancel = cancel
				streaming = true
				startCursor := cursor
				go func() {
					err := client.Events(streamCtx, "/tasks/"+task+"/events", startCursor, func(event Object) {
						select {
						case events <- event:
						case <-streamCtx.Done():
						}
					})
					select {
					case streamDone <- err:
					case <-ctx.Done():
					}
				}()
			}
		}
	}
}
func containsInt(values []int, value int) bool {
	for _, item := range values {
		if item == value {
			return true
		}
	}
	return false
}
