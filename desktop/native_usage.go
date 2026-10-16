package main

import (
	"fmt"
	"net/url"
	"strconv"
	"time"

	"github.com/egoist/mygo/ui"
)

// usagePage shows one member's model usage for a calendar month (UTC). A
// member reads only their own (/account/usage); an administrator picks any
// member (/org/usage). Another member's personal models are listed without
// their name, as the gateway reports them.
type usagePage struct {
	everyone      bool
	members       []Object
	who, shown    string
	month         string
	summary       Object
	items         []Object
	names         map[string]string
	memberChoices []string
}

func (p *usagePage) Title() string {
	if p.everyone {
		return "用量"
	}
	return "我的用量"
}
func (*usagePage) Dirty() bool { return false }
func (p *usagePage) Close()    { *p = usagePage{everyone: p.everyone} }
func (p *usagePage) Open(a *NativeApp) {
	p.who = str(a.member(), "id")
	if p.month == "" {
		p.month = time.Now().UTC().Format("2006-01")
	}
	if p.everyone {
		a.Request("GET", "/members", nil, func(r Result) {
			p.members = r.Items
			p.names = map[string]string{}
			p.memberChoices = nil
			for _, item := range r.Items {
				id := str(item, "id")
				p.memberChoices = append(p.memberChoices, id)
				p.names[id] = str(item, "display_name") + "（" + str(item, "login") + "）"
			}
		})
	}
	p.load(a)
}

func (p *usagePage) load(a *NativeApp) {
	if p.who == "" {
		return
	}
	p.shown = p.who
	who, month := p.who, p.month
	a.Request("GET", "/members/"+url.PathEscape(who)+"/usage?month="+url.QueryEscape(month), nil, func(r Result) {
		if who != p.who || month != p.month {
			return
		}
		p.summary, p.items = r.Data, r.Items
	})
}

// shift moves the month by delta, never past the current one.
func (p *usagePage) shift(a *NativeApp, delta int) {
	at, err := time.Parse("2006-01", p.month)
	if err != nil {
		return
	}
	next := at.AddDate(0, delta, 0).Format("2006-01")
	if next > time.Now().UTC().Format("2006-01") {
		return
	}
	p.month = next
	p.summary, p.items = nil, nil
	p.load(a)
}

func (p *usagePage) View(c *ui.Context, a *NativeApp) {
	if p.everyone {
		nativeHint(c, "按成员查看每月模型用量，月份按 UTC 划分。其他成员的个人模型只显示为个人模型，不显示名称。")
	} else {
		nativeHint(c, "本账号每月的模型用量，月份按 UTC 划分。公司模型计入管理员设置的每月上限，个人模型不计入。")
	}
	ui.Row(c).FillWidth().Wrap().Gap(10).AlignItems(ui.Center).Children(func() {
		if p.everyone && len(p.memberChoices) > 0 {
			nativeChoice(c, &p.who, p.memberChoices, p.names).Label("成员").MinWidth(260)
		}
		current := p.month == time.Now().UTC().Format("2006-01")
		nativeGhostButton(c, "chevron-left", "上个月", a.Busy).OnClick(func() { p.shift(a, -1) })
		ui.Text(c, p.month).FontSize(14).FontWeight(600).SingleLine()
		nativeGhostButton(c, "chevron-right", "下个月", a.Busy || current).OnClick(func() { p.shift(a, 1) })
	})
	if p.who != p.shown && !a.Busy {
		p.summary, p.items = nil, nil
		p.load(a)
	}
	if p.summary == nil {
		nativeText(c, "正在读取用量。")
		return
	}
	used := integer(p.summary, "company_tokens")
	limit := "不限"
	if cap, ok := p.summary["monthly_company_tokens"].(float64); ok {
		limit = strconv.FormatInt(int64(cap), 10) + " tokens"
	}
	nativePairs(c, []string{"公司模型已用", "每月公司模型上限"}, []string{strconv.Itoa(used) + " tokens", limit})
	if len(p.items) == 0 {
		nativeEmpty(c, "activity", "这个月没有模型调用。", nil)
		return
	}
	rows := make([][]string, 0, len(p.items))
	for _, item := range p.items {
		owner := "公司模型"
		if str(item, "owner") == "personal" {
			owner = "个人模型"
		}
		model := str(item, "model")
		if model == "" {
			model = "（他人的个人模型）"
		} else if provider := str(item, "provider"); provider != "" {
			model = nativeLabel(provider) + " · " + model
		}
		rows = append(rows, []string{owner, model, strconv.Itoa(integer(item, "calls")), strconv.Itoa(integer(item, "input_tokens")), strconv.Itoa(integer(item, "output_tokens")), fmt.Sprint(integer(item, "tokens"))})
	}
	nativeTable(c, []string{"归属", "模型", "调用次数", "输入 tokens", "输出 tokens", "合计 tokens"}, rows, nil)
}
